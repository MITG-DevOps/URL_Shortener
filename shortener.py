"""
Malone IT Group – Localhost URL Shortener + File Drop
====================================================
Enhanced single‑file Flask app with:
- URL or file shortening (mutually exclusive)
- 10‑minute TTL for **both** links **and** files (auto‑pruned background thread)
- Live TTL countdown on main page **and** /admin dashboard
- `/api/metadata/<code>` JSON endpoint
- Copy + QR buttons with toast
- Dark / light mode toggle with `localStorage` persistence
- **Hostname‑to‑IP auto‑redirect** so QR codes always resolve on mobile
- **Quiet logging**: `/api/metadata/*` requests no longer spam the console
"""

import os, threading, time, string, random, sqlite3, io, socket, logging
from flask import (
    Flask, request, redirect, render_template_string,
    send_from_directory, send_file, jsonify
)
from werkzeug.utils import secure_filename
from werkzeug.serving import WSGIRequestHandler
import qrcode

# ── Config ────────────────────────────────────────────────────────────────
DB_FILE = "urls.db"
UPLOAD_FOLDER = os.path.join("static", "uploads")
PORT = 5050
DELETE_AFTER_SECONDS = 10 * 60  # 10 minutes

# Resolve local IP once at startup (used for stable base URL)
HOSTNAME   = socket.gethostname().split(".")[0]  # bare hostname
LOCAL_IP   = socket.gethostbyname(socket.gethostname())
BASE_URL   = f"http://{LOCAL_IP}:{PORT}/"  # what we want everyone to use

# ── Flask app ─────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Database helpers ──────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS urls (
                code       TEXT PRIMARY KEY,
                target     TEXT,
                created_at INTEGER,
                hits       INTEGER DEFAULT 0
            )
            """
        )

init_db()

def generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))

def save_mapping(code: str, target: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO urls (code, target, created_at, hits) VALUES (?,?,?,0)",
            (code, target, int(time.time()))
        )

def get_row(code: str):
    with sqlite3.connect(DB_FILE) as conn:
        return conn.execute(
            "SELECT code, target, created_at, hits FROM urls WHERE code = ?",
            (code,)
        ).fetchone()

def seconds_left(created_at: int) -> int:
    return max(0, DELETE_AFTER_SECONDS - (int(time.time()) - created_at))

# ── Cleanup daemon ────────────────────────────────────────────────────────

def cleanup(interval: int = 60):
    while True:
        now = int(time.time())
        with sqlite3.connect(DB_FILE) as conn:
            expired = conn.execute(
                "SELECT code, target FROM urls WHERE ? - created_at > ?",
                (now, DELETE_AFTER_SECONDS),
            ).fetchall()
            for code, tgt in expired:
                if tgt.startswith("/uploads/"):
                    path = os.path.join(UPLOAD_FOLDER, tgt.replace("/uploads/", "", 1))
                    if os.path.isfile(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                conn.execute("DELETE FROM urls WHERE code = ?", (code,))
        time.sleep(interval)

threading.Thread(target=cleanup, daemon=True).start()

# ── Hostname → IP redirect (so QR codes using hostname still work) ────────

@app.before_request
def force_ip_host():
    host = request.host.split(":")[0]
    if host != LOCAL_IP and host.startswith(HOSTNAME):
        return redirect(BASE_URL.rstrip("/") + request.full_path, code=301)

# ── Helper: build absolute short link using IP ────────────────────────────

def make_short(code: str) -> str:
    return f"{BASE_URL}{code}"

# ── Quiet request handler (skip /api/metadata/* lines) ────────────────────

class QuietHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        if self.path.startswith("/api/metadata/"):
            return  # skip this noise
        super().log_request(code, size)

# ── HTML template (unchanged) ─────────────────────────────────────────────
HTML_TEMPLATE = """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <title>Malone IT URL Shortener</title>
  <link rel=\"icon\" href=\"/static/logo.png\">
  <style>
    :root {
      --bg: #f7f7f7;
      --fg: #4a4a4a;
      --card-bg: #ffffff;
    }
    body {
      font-family: \"Segoe UI\", sans-serif;
      background: var(--bg);
      color: var(--fg);
      display: flex;
      justify-content: center;
      padding: 2rem;
    }
    .card {
      background: var(--card-bg);
      max-width: 600px;
      width: 100%;
      padding: 2rem;
      border-radius: 8px;
      box-shadow: 0 0 20px rgba(0,0,0,.05);
    }
    .top-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }
    img.logo { max-width: 200px; }
    h2 { color: #e25f24; text-align: center; margin: 0 0 1rem; }
    input, button {
      font-size: 1rem; border-radius: 6px;
    }
    input[type=url], input[type=text], input[type=file] {
      display: block;
      margin: 0 auto 1rem;
      padding: .75rem;
      border: 1px solid #ccc;
      width: 100%;
      max-width: 600px;
      box-sizing: border-box;
    }
    input[type=submit], button {
      background: #e25f24; color: #fff; padding: .75rem 1.25rem; border: none; cursor: pointer;
    }
    .box { margin-top: 1rem; padding: 1rem; border-left: 5px solid #e25f24; border-radius: 6px; word-break: break-word; }
    .error { background: #ffe5e5; color: #a10000; border-color: #d00000; }
    .qr-button { background: #e25f24; color: #fff; padding: .75rem 1.25rem; border-radius: 6px; text-decoration:none; margin-left:.5rem; }
    .toggle-icon { background:none; border:none; font-size:1.5rem; cursor:pointer; color:#e25f24; }
    #drop-zone { border:2px dashed #ccc; padding:1rem; text-align:center; color:#999; margin-bottom:1rem; }
    #ttl-info { font-size: 0.95rem; color: #666; margin-top: 0.5rem; text-align: center; }
    .admin-countdown {
      font-family: monospace;
    }
  </style>
</head>
<body>
  <div class=\"card\">
    <div class=\"top-bar\">
      <img src=\"/static/logo.png\" alt=\"Malone IT Group Logo\" class=\"logo\">
      <button class=\"toggle-icon\" onclick=\"toggleDark()\" id=\"themeToggle\">🌙</button>
    </div>

    <h2>Malone IT Short Link Generator</h2>

    <form id=\"uploadForm\" action=\"/upload\" method=\"post\" enctype=\"multipart/form-data\">
      <input name=\"url\"  type=\"url\"  placeholder=\"Paste long URL here...\">
      <div style=\"text-align:center; margin: 0.5rem 0 1rem; font-weight:bold; color:#888;\">— OR —</div>
      <div id=\"drop-zone\">Drop file here or use the chooser</div>
      <input type=\"file\" name=\"file\" id=\"fileInput\">
      <input name=\"code\" type=\"text\" placeholder=\"Optional custom path (e.g. Ninja)\">
      <input type=\"submit\" value=\"Submit\">
    </form>

    {% if short_url %}
      <div class=\"box\">
        <strong>Short URL:</strong><br>
        <input value=\"{{ short_url }}\" id=\"shortLink\" readonly style=\"width:100%; margin-bottom: 1rem;\" >
        <button onclick=\"copy('shortLink')\">Copy</button>
        <a href=\"/qr/{{ short_url.split('/')[-1] }}\" target=\"_blank\" class=\"qr-button\">QR Code</a>
        <div id=\"ttl-info\">This link will expire in <span id=\"countdown\">{{ ttl }}</span> seconds</div>
      </div>
    {% elif error_message %}
      <div class=\"box error\">{{ error_message }}</div>
    {% endif %}
  </div>

  <script>
    function copy(id){
      const input=document.getElementById(id);
      const text=input.value||input.innerText;
      if(navigator.clipboard && window.isSecureContext){
        navigator.clipboard.writeText(text).then(()=>showToast('Copied to clipboard!')).catch(()=>fallbackCopy());
      }else{
        fallbackCopy();
      }
      function fallbackCopy(){
        input.select();
        document.execCommand('copy');
        showToast('Copied!');
      }
    }

    function showToast(msg){
      const toast=document.createElement('div');
      toast.textContent=msg;
      Object.assign(toast.style,{
        position:'fixed',bottom:'30px',left:'50%',transform:'translateX(-50%)',background:'#e25f24',color:'#fff',padding:'10px 20px',borderRadius:'5px',boxShadow:'0 2px 10px rgba(0,0,0,0.2)',zIndex:'9999',pointerEvents:'none',opacity:'0',transition:'opacity .3s'
      });
      document.body.appendChild(toast);
      requestAnimationFrame(()=>toast.style.opacity='1');
      setTimeout(()=>{
        toast.style.opacity='0';
        toast.addEventListener('transitionend',()=>toast.remove(),{once:true});
      },2000);
    }

    function toggleDark() {
      const root = document.body;
      const isDark = root.dataset.theme === 'dark';
      if (!isDark) {
        setDarkTheme();
        localStorage.setItem('theme', 'dark');
      } else {
        setLightTheme();
        localStorage.setItem('theme', 'light');
      }
    }

    function setDarkTheme() {
      const root = document.body;
      root.dataset.theme = 'dark';
      root.style.setProperty('--bg','#1e1e1e');
      root.style.setProperty('--fg','#f0f0f0');
      root.style.setProperty('--card-bg','#2e2e2e');
      document.getElementById('themeToggle').innerText='☀️';
    }

    function setLightTheme() {
      const root = document.body;
      root.dataset.theme = 'light';
      root.style.setProperty('--bg','#f7f7f7');
      root.style.setProperty('--fg','#4a4a4a');
      root.style.setProperty('--card-bg','#ffffff');
      document.getElementById('themeToggle').innerText='🌙';
    }

    window.onload = function() {
      const saved = localStorage.getItem('theme') || 'light';
      if (saved === 'dark') {
        setDarkTheme();
      } else {
        setLightTheme();
      }
      document.body.dataset.theme = saved;

      const dropZone = document.getElementById('drop-zone');
      const fileInput = document.getElementById('fileInput');
      if (dropZone) {
        dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.style.background = '#eee'; });
        dropZone.addEventListener('dragleave', e => { e.preventDefault(); dropZone.style.background = ''; });
        dropZone.addEventListener('drop', e => {
          e.preventDefault(); dropZone.style.background = '';
          fileInput.files = e.dataTransfer.files;
        });
      }

      const countdownEl = document.getElementById("countdown");
      if (countdownEl) {
        let timeLeft = parseInt(countdownEl.innerText);
        const interval = setInterval(() => {
          if (timeLeft <= 1) {
            countdownEl.innerText = "expired";
            clearInterval(interval);
          } else {
            timeLeft -= 1;
            countdownEl.innerText = timeLeft;
          }
        }, 1000);
      }

      const adminCountdowns = document.querySelectorAll(".admin-countdown");
      adminCountdowns.forEach(span => {
        let timeLeft = parseInt(span.dataset.expiry);
        const interval = setInterval(() => {
          if (timeLeft <= 1) {
            span.innerText = "expired";
            clearInterval(interval);
          } else {
            timeLeft -= 1;
            span.innerText = timeLeft + 's';
          }
        }, 1000);
      });
    }
  </script>
</body>
</html>
"""

# ── Routes (unchanged except removed log-level tinkering) ─────────────────

@app.route("/", methods=["GET"])
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route("/upload", methods=["POST"])
def upload_file():
    short_url = error_message = None
    file = request.files.get("file")
    url  = request.form.get("url", "").strip()
    code = request.form.get("code", "").strip() or generate_code()

    if (file and file.filename) and url:
        error_message = "Error: Provide EITHER a URL OR a file, not both."
    elif file and file.filename:
        filename = f"{int(time.time())}_{secure_filename(file.filename)}"
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(path)
        save_mapping(code, "/uploads/" + filename)
        short_url = make_short(code)
    elif url:
        save_mapping(code, url)
        short_url = make_short(code)
    else:
        error_message = "Error: Please provide a file or a URL."

    return render_template_string(HTML_TEMPLATE, short_url=short_url, error_message=error_message, ttl=DELETE_AFTER_SECONDS)

@app.route("/<code>")
def redirect_code(code):
    row = get_row(code)
    if not row:
        return "Not found", 404
    code, target, created_at, _ = row
    if time.time() - created_at > DELETE_AFTER_SECONDS:
        return "Link expired", 410
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE urls SET hits = hits + 1 WHERE code = ?", (code,))
    if target.startswith("/uploads/"):
        return send_from_directory(app.config["UPLOAD_FOLDER"], target.replace("/uploads/", "", 1), as_attachment=True)
    return redirect(target)

@app.route("/qr/<code>")
def qr_code(code):
    if not get_row(code):
        return "Not found", 404
    img = qrcode.make(make_short(code))
    buf = io.BytesIO(); img.save(buf); buf.seek(0)
    return send_file(buf, mimetype="image/png")

@app.route("/admin")
def admin():
    q = request.args.get("q", "").strip()
    with sqlite3.connect(DB_FILE) as conn:
        if q:
            rows = conn.execute("SELECT code, target, created_at, hits FROM urls WHERE code LIKE ? OR target LIKE ? ORDER BY created_at DESC", (f"%{q}%", f"%{q}%")).fetchall()
        else:
            rows = conn.execute("SELECT code, target, created_at, hits FROM urls ORDER BY created_at DESC").fetchall()
    out = "<h1>Admin</h1><form><input name='q' placeholder='Search' value='%s'><input type='submit' value='Search'></form>" % q
    out += "<table border=1 cellpadding=5><tr><th>Code</th><th>Target</th><th>Created</th><th>Expires In</th><th>Hits</th></tr>"
    for code, target, created_at, hits in rows:
        expires = seconds_left(created_at)
        out += f"<tr><td>{code}</td><td>{target}</td><td>{time.ctime(created_at)}</td><td><span class='admin-countdown' data-expiry='{expires}'>{expires}s</span></td><td><span class='admin-hits' data-code='{code}'>{hits}</span></td></tr>"
    out += """
    </table>
    <script>
      window.onload = function() {
        document.querySelectorAll('.admin-countdown').forEach(span => {
          let t = parseInt(span.dataset.expiry);
          let i = setInterval(() => {
            if (t <= 1) {
              span.innerText = 'expired';
              clearInterval(i);
            } else {
              t -= 1;
              span.innerText = t + 's';
            }
          }, 1000);
        });

        function refreshHits() {
          document.querySelectorAll('.admin-hits').forEach(el => {
            fetch(`/api/metadata/${el.dataset.code}`)
              .then(res => res.json())
              .then(data => {
                if (data.hits !== undefined) el.innerText = data.hits;
              });
          });
        }
        setInterval(refreshHits, 3000);
      }
    </script>
    """
    return out

@app.route("/api/metadata/<code>")
def metadata(code):
    row = get_row(code)
    if not row:
        return jsonify(error="Not found"), 404
    _, target, created_at, hits = row
    return jsonify({
        "target": target,
        "created_at": created_at,
        "expires_in": seconds_left(created_at),
        "hits": hits
    })

# ── Run ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"* Running on {BASE_URL} (CTRL+C to quit)")
    app.run(host="0.0.0.0", port=PORT, request_handler=QuietHandler)
