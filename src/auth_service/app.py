from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
import sqlite3, os, uuid
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from flask import Response

app = Flask(__name__)
app.secret_key = "supersecret"
DB_PATH = os.path.join(os.path.dirname(__file__), "auth.db")

sessions = {}  # in-memory session tokens for CRM access

REQUEST_COUNT = Counter('auth_requests_total', 'Total number of requests', ['method', 'endpoint'])

@app.before_request
def before_request_func():
    # Skip counting Prometheus scrapes
    if request.path != '/metrics':
        REQUEST_COUNT.labels(method=request.method, endpoint=request.path).inc()

@app.route('/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user'
        )
        """)
        conn.commit()

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
            user = cur.fetchone()

        if user:
            token = str(uuid.uuid4())
            sessions[token] = {"name": username, "role": user[3]}
            return redirect(f"http://localhost:5001/dashboard?token={token}")
        else:
            flash("Invalid credentials")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
                conn.commit()
            flash("Account created. You can log in now.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists.")
    return render_template("register.html")

@app.route("/api/validate")
def validate():
    token = request.args.get("token")
    if token in sessions:
        return jsonify({"status": "ok", **sessions[token]})
    return jsonify({"status": "error"}), 403

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
