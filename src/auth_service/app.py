from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, make_response
import sqlite3, os, uuid
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
import redis
import json

import debugpy
import os

if os.getenv("FLASK_DEBUG", "0") == "1":
    debugpy.listen(("0.0.0.0", 5678))
    print("Waiting for VS Code debugger to attach...")
    debugpy.wait_for_client()

app = Flask(__name__)
app.secret_key = "supersecret"
DB_PATH = os.path.join(os.path.dirname(__file__), "auth.db")

# Connect to Redis (hostname = redis container name)
redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)

REQUEST_COUNT = Counter('auth_requests_total', 'Total number of requests', ['method', 'endpoint'])

SESSION_TTL = 30  # 1 hour session expiry in Redis

@app.route("/")
def index():
    return redirect(url_for("login"))

@app.before_request
def before_request_func():
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

import logging

# Ensure you have this at the top of your file
logging.basicConfig(
    level=logging.INFO,  # Use DEBUG for more verbose output
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        logger.info("Login attempt for user: %s", username)

        try:
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.execute(
                    "SELECT * FROM users WHERE username=? AND password=?", (username, password)
                )
                user = cur.fetchone()

            if user:
                token = str(uuid.uuid4())
                session_data = {"name": username, "role": user[3]}
                logger.info("User %s authenticated successfully. Session token: %s", username, token)

                # Store session in Redis
                redis_client.setex(f"session:{token}", SESSION_TTL, json.dumps(session_data))
                logger.debug("Session stored in Redis for token: %s", token)

                # Create response with cookie
                resp = make_response(redirect("http://localhost:5001/dashboard"))
                resp.set_cookie("auth_token", token, max_age=SESSION_TTL, httponly=True)
                logger.info("Set session cookie for user: %s", username)

                return resp
            else:
                logger.warning("Invalid login attempt for user: %s", username)
                flash("Invalid credentials")
        except Exception as e:
            logger.error("Error during login for user %s: %s", username, str(e))
            flash("Internal error, please try again.")

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
    token = request.cookies.get("session_id")

    if not token:
        return jsonify({"status": "error"}), 403

    session_raw = redis_client.get(f"session:{token}")

    if not session_raw:
        return jsonify({"status": "error"}), 403

    session_data = json.loads(session_raw)

    return jsonify({"status": "ok", **session_data})

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)  # Important!
