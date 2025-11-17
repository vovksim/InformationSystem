from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, make_response, g
import sqlite3, os, uuid
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
import redis
import json
import time
import threading
import logging

app = Flask(__name__)
app.secret_key = "supersecret"

DB_PATH = os.getenv("DB_PATH", "/app/data/auth.db")  # fallback if env not set
db_dir = os.path.dirname(DB_PATH)
os.makedirs(db_dir, exist_ok=True)

redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)

SESSION_TTL = 30  # seconds (adjust to 3600 for 1 hour)
SESSION_TTL_GAUGE_VALUE = SESSION_TTL
ACTIVE_SESSIONS_POLL_INTERVAL = 10  # seconds

logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for more verbose logs
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

REQUEST_COUNT = Counter(
    'auth_requests_total',
    'Total number of requests',
    ['method', 'endpoint']
)

REQUEST_LATENCY = Histogram(
    'auth_request_latency_seconds',
    'Request latency in seconds',
    ['method', 'endpoint']
)

LOGIN_SUCCESS = Counter(
    'auth_login_success_total',
    'Total successful logins'
)

LOGIN_FAILED = Counter(
    'auth_login_failed_total',
    'Total failed login attempts'
)

VALIDATE_SUCCESS = Counter(
    'auth_validate_success_total',
    'Total successful token validations'
)

VALIDATE_FAILED = Counter(
    'auth_validate_failed_total',
    'Total failed token validations'
)

TOKEN_VALIDATION_LATENCY = Histogram(
    'auth_token_validation_latency_seconds',
    'Latency of token validation'
)

REDIS_LATENCY = Histogram(
    'auth_redis_latency_seconds',
    'Time spent performing Redis operations',
    ['operation']
)

REDIS_ERRORS = Counter(
    'auth_redis_errors_total',
    'Count of Redis-related errors',
    ['operation']
)

ACTIVE_SESSIONS = Gauge(
    'auth_active_sessions',
    'Number of active sessions stored in Redis'
)

SESSION_TTL_GAUGE = Gauge(
    'auth_session_ttl_seconds',
    'Configured TTL for sessions in seconds'
)
SESSION_TTL_GAUGE.set(SESSION_TTL_GAUGE_VALUE)

# --- Helpers ---
def count_active_sessions():
    """
    Count session:* keys using SCAN (non-blocking) and set ACTIVE_SESSIONS gauge.
    """
    try:
        count = 0
        for _ in redis_client.scan_iter(match="session:*"):
            count += 1
        ACTIVE_SESSIONS.set(count)
        logger.debug("Active sessions counted: %d", count)
    except Exception as e:
        REDIS_ERRORS.labels(operation="scan_sessions").inc()
        logger.error("Error counting sessions in Redis: %s", e)

def active_sessions_loop(stop_event: threading.Event):
    """
    Background loop that periodically updates ACTIVE_SESSIONS gauge.
    Runs until stop_event is set.
    """
    logger.info("Starting active sessions monitor thread (interval %ss)", ACTIVE_SESSIONS_POLL_INTERVAL)
    while not stop_event.is_set():
        count_active_sessions()
        # wait with early exit
        stop_event.wait(ACTIVE_SESSIONS_POLL_INTERVAL)
    logger.info("Active sessions monitor thread stopping")

_active_sessions_stop_event = None
_active_sessions_thread = None

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

@app.before_request
def before_request_func():
    g._start_time = time.monotonic()
    if request.path != '/metrics':
        REQUEST_COUNT.labels(method=request.method, endpoint=request.path).inc()

@app.after_request
def after_request_func(response):
    try:
        if hasattr(g, "_start_time") and request.path != '/metrics':
            elapsed = time.monotonic() - g._start_time
            REQUEST_LATENCY.labels(method=request.method, endpoint=request.path).observe(elapsed)
    except Exception as e:
        logger.debug("Error recording request latency: %s", e)
    return response

@app.route('/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route("/")
def index():
    return redirect(url_for("login"))

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
                LOGIN_SUCCESS.inc()
                token = str(uuid.uuid4())
                session_data = {"name": username, "role": user[3] if len(user) > 3 else "user"}
                logger.info("User %s authenticated successfully. Session token: %s", username, token)
                try:
                    start = time.monotonic()
                    redis_client.setex(f"session:{token}", SESSION_TTL, json.dumps(session_data))
                    REDIS_LATENCY.labels(operation="set_session").observe(time.monotonic() - start)
                except Exception as e:
                    REDIS_ERRORS.labels(operation="set_session").inc()
                    logger.error("Redis error during session set: %s", e)
                    flash("Internal error (Redis). Please try again.")
                    return render_template("login.html")
                try:
                    count_active_sessions()
                except Exception:
                    pass
                resp = make_response(redirect("http://localhost:5001/dashboard"))
                resp.set_cookie("auth_token", token, max_age=SESSION_TTL, httponly=True)
                logger.info("Set session cookie for user: %s", username)
                return resp
            else:
                LOGIN_FAILED.inc()
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
    token = request.args.get("token") or request.cookies.get("session_id") or request.cookies.get("auth_token")
    logger.info("Token validation attempt. token=%s", token)

    if not token:
        VALIDATE_FAILED.inc()
        logger.warning("Validation failed: no token provided")
        return jsonify({"status": "error"}), 403

    with TOKEN_VALIDATION_LATENCY.time():
        try:
            start = time.monotonic()
            session_raw = redis_client.get(f"session:{token}")
            REDIS_LATENCY.labels(operation="get_session").observe(time.monotonic() - start)
        except Exception as e:
            REDIS_ERRORS.labels(operation="get_session").inc()
            logger.error("Redis error during token validation: %s", str(e))
            return jsonify({"status": "error"}), 500

    if not session_raw:
        VALIDATE_FAILED.inc()
        logger.warning("Validation failed: token not found or expired: %s", token)
        return jsonify({"status": "error"}), 403

    VALIDATE_SUCCESS.inc()
    session_data = json.loads(session_raw)
    return jsonify({"status": "ok", **session_data})


if __name__ == "__main__":
    logger.info(DB_PATH)
    init_db()
    _active_sessions_stop_event = threading.Event()
    _active_sessions_thread = threading.Thread(target=active_sessions_loop, args=(_active_sessions_stop_event,), daemon=True)
    _active_sessions_thread.start()

    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    finally:
        if _active_sessions_stop_event:
            _active_sessions_stop_event.set()
            if _active_sessions_thread:
                _active_sessions_thread.join(timeout=1)
