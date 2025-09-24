import os
import re
import pickle
from collections import Counter
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

try:
    from docx import Document
    HAVE_DOCX = True
except Exception:
    Document = None
    HAVE_DOCX = False

try:
    import PyPDF2
    HAVE_PYPDF2 = True
except Exception:
    PyPDF2 = None
    HAVE_PYPDF2 = False

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change_me_to_a_random_secret_key")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "best_sentiment_pipeline.pkl")
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
ALLOWED_EXTENSIONS = {"txt", "pdf", "docx"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
BASE_DIR = os.path.dirname(__file__)
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# ---------------- HTML templates ----------------
base_html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Moodify</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='styles.css') }}" />
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  </head>
  <body>
    <div class="topbar">
      <div class="logo">
        <img src="{{ url_for('static', filename='logo.png') }}" alt="logo" class="logo-img">
        Moodify
      </div>
      <div class="profile"> 
        <a href="{{ url_for('profile') }}" class="profile-link"> 
          <div class="avatar">{{ session.get('name','U')[:1]|upper }}</div>
        </a>
      </div>
    </div>

    <nav class="nav dark-nav">
      <a href="{{ url_for('dashboard') }}" class="nav-item {% if active=='dashboard' %}active{% endif %}">Dashboard</a>
      <a href="{{ url_for('predict') }}" class="nav-item {% if active=='predict' %}active{% endif %}">Predict</a>
      <a href="{{ url_for('result') }}" class="nav-item {% if active=='result' %}active{% endif %}">Result</a>
    </nav>

    <main class="container">
      {% with messages = get_flashed_messages() %}
        {% if messages %}
          <ul class="flashes">
          {% for m in messages %}
            <li>{{ m }}</li>
          {% endfor %}
          </ul>
        {% endif %}
      {% endwith %}
      {% block content %}{% endblock %}
    </main>

    <footer class="footer">Team Code Sparks</footer>
  </body>
</html>
"""

login_html = """{% extends 'base.html' %}
{% block content %}
<div class="card center-card">
  <h2>Sign In</h2>
  <form method="post" action="{{ url_for('login') }}">
    <label>Name</label>
    <input name="name" required />
    <label>Email</label>
    <input name="email" type="email" required />
    <label>Password</label>
    <input name="password" type="password" required />
    <button type="submit">Enter</button>
  </form>
</div>
{% endblock %}
"""

dashboard_html = """{% extends 'base.html' %}
{% block content %}
  <div class="centered">
    <h1 class="main-heading">Moodify</h1>
    <p class="description">Every sentence carries a story, From joy to frustration, every mood matters.<br>
     Bridging the gap between text and true emotions.</p>
  </div>
  <div class="dashboard-cards">
    <div class="card">
      <h3>Quick Stats</h3>
      <p>Total uploads: {{ stats.total_uploads }}</p>
      <p>Last predicted label: {{ stats.last_label }}</p>
    </div>
    <div class="card">
      <h3>Top Labels</h3>
      <canvas id="labelsChart"></canvas>
    </div>
  </div>
  <script>
    const labels = {{ stats.labels | tojson }};
    const counts = {{ stats.counts | tojson }};
    const ctx = document.getElementById('labelsChart');
    if (ctx) {
      new Chart(ctx, {
        type: 'bar',
        data: { labels: labels, datasets: [{ label: 'Counts', data: counts, backgroundColor:'rgba(173, 216, 230, 1)'}] },
        options: { responsive:true, plugins:{ tooltip:{ enabled:true } } }
      });
    }
  </script>
{% endblock %}
"""

predict_html = """{% extends 'base.html' %}
{% block content %}
  <div class="card">
    <h2>Make a Prediction</h2>
    <form method="post" enctype="multipart/form-data">
      <label>Enter text</label>
      <textarea name="text_input" rows="3" style="width:100%;min-width:300px;resize:vertical;">{{ request.form.get('text_input','') }}</textarea>

      <label>Or upload file (txt, pdf, docx)</label>
      <input type="file" name="file_input" />

      <button type="submit">Predict</button>
    </form>
  </div>

  {% if prediction %}
  <div class="card">
    <h3>Prediction</h3>
    <p><strong>Label:</strong> {{ prediction.label }}</p>

    <canvas id="predChart" width="300" height="300"></canvas>

    <h4>Top words</h4>
    <ul>
      {% for w,c in top_words %}
        <li>{{ w }} — {{ c }}</li>
      {% endfor %}
    </ul>

    {% if summary %}
      <h4>Summary</h4>
      <p>{{ summary }}</p>
    {% endif %}

    {% if digit_counts %}
      <h4>Digit Frequency (KDE Plot)</h4>
      <canvas id="digitChart"></canvas>
    {% endif %}
  </div>

  <script>
    const p_labels = {{ prediction.labels | tojson }};
    const p_scores = {{ prediction.scores | tojson }};
    const pctx = document.getElementById('predChart');
    if (pctx) {
      const colors = p_labels.map(l => l.toLowerCase() === "positive" ? "green" : (l.toLowerCase() === "negative" ? "red" : "#36a2eb"));
      new Chart(pctx, { type:'pie', data:{ labels:p_labels, datasets:[{ data:p_scores, backgroundColor: colors, hoverOffset:20 }] },
        options: { responsive:true, plugins:{ tooltip:{ enabled:true } } } });
    }

    {% if digit_counts %}
    const d_labels = {{ digit_counts.keys() | list | tojson }};
    const d_scores = {{ digit_counts.values() | list | tojson }};
    const dctx = document.getElementById('digitChart');
    if (dctx) {
      new Chart(dctx, {
        type:'line',
        data:{ labels:d_labels, datasets:[{ 
            label:'KDE Approximation',
            data:d_scores,
            fill:true,
            borderColor:'#36a2eb',
            backgroundColor:'rgba(54,162,235,0.2)',
            tension:0.4
        }]},
        options:{ responsive:true, plugins:{ tooltip:{ enabled:true } } }
      });
    }
    {% endif %}
  </script>
  {% endif %}
{% endblock %}
"""

result_html = """{% extends 'base.html' %}
{% block content %}
  <div class="card">
    <h2>Prediction Result</h2>
    {% if prediction %}
      <p><strong>Label:</strong> {{ prediction.label }}</p>
      <canvas id="resultBarChart" width="400" height="200"></canvas>
      <script>
        const r_labels = {{ prediction.labels | tojson }};
        const r_scores = {{ prediction.scores | tojson }};
        const rctx = document.getElementById('resultBarChart');
        new Chart(rctx, {
          type: 'bar',
          data: { labels: r_labels, datasets: [{ label: 'Scores', data: r_scores, backgroundColor: 'rgba(54,162,235,0.6)' }] },
          options: { responsive:true, plugins:{ tooltip:{ enabled:true } } }
        });
      </script>
    {% else %}
      <p>No prediction made yet.</p>
    {% endif %}
  </div>
{% endblock %}
"""

profile_html = """{% extends 'base.html' %}
{% block content %}
  <div class="card center-card">
    <h2>Profile</h2>
    <form method="post" action="{{ url_for('profile_edit') }}">
      <label>Name <span class="edit-icon">✎</span></label>
      <input name="name" value="{{ session.get('name') }}" />
      <label>Email <span class="edit-icon">✎</span></label>
      <input name="email" value="{{ session.get('email') }}" />
      <button type="submit">Save</button>
    </form>
    <form method="post" action="{{ url_for('logout') }}"><button>Logout</button></form>
  </div>
{% endblock %}
"""

styles_css = '''
html,body{height:100%;margin:0;font-family:Inter,Arial,Helvetica,sans-serif;background:#0f1724;color:#e6eef8}
.topbar{display:flex;justify-content:space-between;align-items:center;padding:12px 24px;background:#071021}
.logo{font-weight:700;display:flex;align-items:center;gap:10px}
.logo-img{width:40px;height:40px;border-radius:50%}
.profile-link{text-decoration:none}
.avatar{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:#1f2937;color:#fff}
.nav{display:flex;gap:8px;padding:12px 24px;background:#071021}
.dark-nav{background:#000}
.nav-item{color:#9aa6b2;text-decoration:none;padding:8px 12px;border-radius:8px}
.nav-item.active{background:#1f2937;color:#fff}
.container{padding:24px}
.centered{text-align:center;margin:40px 0}
.main-heading{font-size:48px;font-weight:700;margin-bottom:12px}
.centered .description{font-size:18px;margin-top:10px;line-height:1.6}
.card{background:#0b1220;padding:18px;border-radius:12px;box-shadow:0 4px 12px rgba(2,6,23,0.6);margin:12px}
.center-card{max-width:420px;margin:40px auto}
.dashboard-cards{display:flex;gap:12px;flex-wrap:wrap}
.footer{text-align:center;padding:12px;color:#7b8794}
.flashes{list-style:none;padding:0}
input,textarea,button{width:100%;padding:8px;margin:6px 0;border-radius:8px;border:1px solid #1f2937;background:#08111a;color:#e6eef8}
textarea{resize:vertical;min-width:300px}
canvas{display:block;margin:12px auto}
button{cursor:pointer}
.edit-icon{cursor:pointer;font-size:0.9em;color:#7b8794;margin-left:6px}
'''

templates = {
    "base.html": base_html,
    "login.html": login_html,
    "dashboard.html": dashboard_html,
    "predict.html": predict_html,
    "result.html": result_html,
    "profile.html": profile_html,
}
for name, content in templates.items():
    with open(os.path.join(TEMPLATE_DIR, name), "w", encoding="utf-8") as f:
        f.write(content)
with open(os.path.join(STATIC_DIR, "styles.css"), "w", encoding="utf-8") as f:
    f.write(styles_css)

model = None
MODEL_LOAD_ERROR = None
STATS = {"total_uploads": 0, "labels": [], "counts": [], "last_label": None}
LAST_UPLOADED_TEXT = ""

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def read_text_from_file(filepath):
    ext = filepath.rsplit(".", 1)[1].lower()
    text = ""
    try:
        if ext == "txt":
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f: text = f.read()
        elif ext == "docx" and HAVE_DOCX:
            text = "\n".join(p.text for p in Document(filepath).paragraphs)
        elif ext == "pdf" and HAVE_PYPDF2:
            with open(filepath, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception: text = ""
    return text

def simple_top_words(text, n=10):
    tokens = re.findall(r"\b[a-zA-Z]{2,}\b", (text or "").lower())
    stop = set(["the","and","is","in","to","of","a","it","for","that","this","on","with","as","are"])
    tokens = [t for t in tokens if t not in stop]
    return Counter(tokens).most_common(n)

def summarize_text(text):
    text = re.sub(r"\s+", " ", text.strip())
    return text

def digit_frequency(text):
    return {str(d): text.count(str(d)) for d in range(10)}

def load_model_safe():
    global model, MODEL_LOAD_ERROR
    if not os.path.exists(MODEL_PATH): model=None; MODEL_LOAD_ERROR="Missing"; return False
    try:
        with open(MODEL_PATH, "rb") as m: model=pickle.load(m)
        MODEL_LOAD_ERROR=None
        return True
    except Exception as e: model=None; MODEL_LOAD_ERROR=str(e); return False

load_model_safe()

@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        name=request.form.get("name")
        email=request.form.get("email")
        password=request.form.get("password")
        if not (name and email and password):
            flash("Please fill all fields"); return render_template("login.html")
        session["authenticated"]=True
        session["name"]=name
        session["email"]=email
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if not session.get("authenticated"): return redirect(url_for("login"))
    labels=STATS.get("labels",[]); counts=STATS.get("counts",[]); last=STATS.get("last_label")
    stats={"total_uploads":STATS.get("total_uploads",0),"labels":labels,"counts":counts,"last_label":last}
    return render_template("dashboard.html", active="dashboard", stats=stats)

@app.route("/predict", methods=["GET","POST"])
def predict():
    global LAST_UPLOADED_TEXT
    if not session.get("authenticated"): return redirect(url_for("login"))
    prediction=None; top_words=[]; summary=None; digit_counts=None
    if request.method=="POST":
        text=request.form.get("text_input","").strip()
        file=request.files.get("file_input")
        uploaded_text=""
        if file and file.filename!="" and allowed_file(file.filename):
            filename=secure_filename(file.filename)
            path=os.path.join(UPLOAD_FOLDER,filename)
            file.save(path)
            uploaded_text=read_text_from_file(path)
            LAST_UPLOADED_TEXT=uploaded_text
        elif text:
            uploaded_text=text; LAST_UPLOADED_TEXT=text
        else:
            flash("Provide text input or file"); return redirect(url_for("predict"))
        
        if model:
            try:
                if hasattr(model,"predict_proba"):
                    probs=list(model.predict_proba([uploaded_text])[0])
                    classes=list(getattr(model,"classes_",[]))
                    max_idx=max(range(len(probs)), key=lambda i:probs[i]) if probs else 0
                    label=classes[max_idx] if classes else "unknown"
                    prediction={"label":label,"labels":classes,"scores":probs}
                else:
                    label=model.predict([uploaded_text])[0]
                    prediction={"label":label,"labels":[label],"scores":[1.0]}
            except: label="Error"; prediction={"label":label,"labels":[],"scores":[]}
        else:
            pos=["good","great","happy","excellent","love"]; neg=["bad","sad","terrible","hate","worst"]
            s=sum((uploaded_text.lower()).count(w) for w in pos)
            t=sum((uploaded_text.lower()).count(w) for w in neg)
            label="positive" if s>=t else "negative"
            prediction={"label":label,"labels":["positive","negative"],"scores":[s,t]}
        
        STATS["total_uploads"]+=1
        lbl=prediction.get("label")
        STATS["last_label"]=lbl
        if lbl:
            if lbl in STATS.get("labels",[]):
                idx=STATS["labels"].index(lbl)
                STATS["counts"][idx]+=1
            else:
                STATS.setdefault("labels",[]).append(lbl)
                STATS.setdefault("counts",[]).append(1)
        top_words=simple_top_words(uploaded_text)
        summary=summarize_text(uploaded_text)
        digit_counts=digit_frequency(uploaded_text)
    
    return render_template("predict.html", active="predict", prediction=prediction, top_words=top_words, summary=summary, digit_counts=digit_counts)

@app.route("/result")
def result():
    if not session.get("authenticated"): return redirect(url_for("login"))
    return render_template("result.html", active="result", prediction=STATS.get("last_label") and {"label":STATS["last_label"],"labels":STATS.get("labels",[]),"scores":STATS.get("counts",[])} )

@app.route("/profile")
def profile():
    if not session.get("authenticated"): return redirect(url_for("login"))
    return render_template("profile.html", active="")

@app.route("/profile_edit", methods=["POST"])
def profile_edit():
    if not session.get("authenticated"): return redirect(url_for("login"))
    session["name"]=request.form.get("name")
    session["email"]=request.form.get("email")
    flash("Profile updated successfully")
    return redirect(url_for("profile"))

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__=="__main__":
    app.run(debug=True)
