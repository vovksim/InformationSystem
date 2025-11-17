#!/usr/bin/env python3
import time
import random
import uuid
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import requests
from faker import Faker
import threading

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("db_stresser")

# ---------- config (from env) ----------
AUTH_URL = os.getenv("AUTH_URL", "http://auth_service:5000")
CRM_URL = os.getenv("CRM_URL", "http://crm_service:5001")
WORKERS = int(os.getenv("WORKERS", 5))
TARGET_OPS_PER_SEC = int(os.getenv("TARGET_OPS_PER_SEC", 10))
REPORT_INTERVAL = int(os.getenv("REPORT_INTERVAL_SEC", 10))

if TARGET_OPS_PER_SEC <= 0:
    TARGET_OPS_PER_SEC = 10
if WORKERS <= 0:
    WORKERS = 1

INTERVAL = WORKERS / TARGET_OPS_PER_SEC  # seconds per worker between operations

fake = Faker()

# Example users pool for login attempts
USERS = [
    {"username": "user1", "password": "pass1"},
    {"username": "user2", "password": "pass2"},
    {"username": "user3", "password": "pass3"},
]

# Stats
_stats = {
    "ops": 0,
    "register_ok": 0,
    "register_fail": 0,
    "login_ok": 0,
    "login_fail": 0,
    "create_ok": 0,
    "create_fail": 0,
    "delete_ok": 0,
    "delete_fail": 0,
}

# ---------- helpers ----------
def safe_json(r: requests.Response):
    ctype = r.headers.get("Content-Type", "")
    if "application/json" in ctype:
        try:
            return r.json()
        except Exception:
            return None
    return None

def log_response_details(prefix: str, r: requests.Response):
    body = r.text.strip()
    body_snip = (body[:500] + "...") if len(body) > 500 else body
    logger.debug("%s status=%s headers=%s body=%s", prefix, r.status_code, dict(r.headers), body_snip)

# ---------- AUTH interactions (form-based, fixed) ----------
def register_user_session(sess: requests.Session, username: str, password: str) -> bool:
    """
    Register user by sending form-encoded POST to /register.
    Matches auth_service.register which expects only username & password form fields.
    """
    url = f"{AUTH_URL}/register"
    data = {"username": username, "password": password}  # NO email field — matches your auth service
    try:
        # do NOT follow redirects (but following is OK for register); keep default allow_redirects=True
        r = sess.post(url, data=data, timeout=10)
        log_response_details("[REGISTER]", r)
        # register typically redirects to login (302) or returns 200
        if r.status_code in (200, 302):
            return True
        logger.debug("Register returned unexpected status: %s, body=%s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        logger.error("Exception during register: %s", e)
        return False

def login_user_session(sess: requests.Session, username: str, password: str) -> Optional[str]:
    """
    Login using form POST to /login. IMPORTANT: do not follow redirects (auth returns a redirect
    to an absolute localhost URL). Extract auth_token cookie from response or Set-Cookie header.
    Returns auth_token string or None.
    """
    url = f"{AUTH_URL}/login"
    data = {"username": username, "password": password}
    try:
        # IMPORTANT: don't follow redirect to localhost:5001 — capture cookie from login response
        r = sess.post(url, data=data, timeout=10, allow_redirects=False)
        log_response_details("[LOGIN]", r)

        # 1) check response cookies (requests will populate r.cookies)
        if "auth_token" in r.cookies:
            token = r.cookies.get("auth_token")
            # also ensure session has it
            sess.cookies.set("auth_token", token)
            logger.info("[AUTH] Login cookie auth_token acquired (from response.cookies)")
            return token

        # 2) check session cookies (sometimes cookies are in session)
        if "auth_token" in sess.cookies:
            token = sess.cookies.get("auth_token")
            logger.info("[AUTH] Login cookie auth_token present in session.cookies")
            return token

        # 3) parse Set-Cookie header as fallback
        sc = r.headers.get("Set-Cookie", "")
        if sc:
            for part in sc.split(";"):
                part = part.strip()
                if part.startswith("auth_token="):
                    token = part.split("=", 1)[1]
                    sess.cookies.set("auth_token", token)
                    logger.info("[AUTH] Login cookie auth_token acquired (from Set-Cookie header)")
                    return token

        # If not found, login did not produce cookie/token
        logger.debug("[AUTH] Login did not produce auth_token. status=%s, body=%s", r.status_code, r.text[:200])
        return None
    except Exception as e:
        logger.error("Exception during login: %s", e)
        return None

def validate_token(sess: requests.Session, token: Optional[str]) -> bool:
    if not token:
        return False
    url = f"{AUTH_URL}/api/validate"
    try:
        params = {"token": token}
        r = sess.get(url, params=params, timeout=5)
        log_response_details("[VALIDATE]", r)
        j = safe_json(r)
        if j and j.get("status") == "ok":
            return True
        return False
    except Exception as e:
        logger.error("Exception during validate: %s", e)
        return False

# ---------- CRM interactions (use correct /api routes) ----------
def create_order(sess: requests.Session) -> Optional[str]:
    url = f"{CRM_URL}/api/orders"
    body = {"item": fake.word(), "price": random.randint(5, 200)}
    try:
        r = sess.post(url, json=body, timeout=10)
        log_response_details("[CRM CREATE]", r)
        if r.status_code in (200, 201):
            j = safe_json(r)
            if j and j.get("order_id"):
                return str(j.get("order_id"))
        else:
            logger.debug("Create order returned non-200: %s %s", r.status_code, r.text[:200])
            return None
    except Exception as e:
        logger.error("Exception during create_order: %s", e)
        return None

def list_orders(sess: requests.Session):
    url = f"{CRM_URL}/api/orders"
    try:
        r = sess.get(url, timeout=10)
        log_response_details("[CRM LIST]", r)
        if r.status_code == 200:
            j = safe_json(r)
            if j and "orders" in j:
                return j["orders"]
        return []
    except Exception as e:
        logger.error("Exception during list_orders: %s", e)
        return []

def delete_order(sess: requests.Session, order_id: str) -> bool:
    url = f"{CRM_URL}/api/orders/{order_id}"
    try:
        r = sess.delete(url, timeout=10)
        log_response_details("[CRM DELETE]", r)
        return r.status_code == 200
    except Exception as e:
        logger.error("Exception during delete_order: %s", e)
        return False

# ---------- worker ----------
def worker_task(worker_id: int):
    sess = requests.Session()
    sess.headers.update({"User-Agent": f"db-stresser/1.0 worker/{worker_id}"})
    local_ops = 0
    last_report = time.time()

    while True:
        start = time.time()
        service_choice = random.choices(["auth", "crm"], weights=[0.6, 0.4])[0]

        if service_choice == "auth":
            action = random.choices(["register", "login"], weights=[0.3, 0.7])[0]
            if action == "register":
                username = fake.unique.user_name() + str(uuid.uuid4())[:6]
                password = fake.password()
                ok = register_user_session(sess, username, password)
                if ok:
                    _stats["register_ok"] += 1
                    # add newly registered user to pool for future logins
                    USERS.append({"username": username, "password": password})
                else:
                    _stats["register_fail"] += 1
                _stats["ops"] += 1
                local_ops += 1
            else:
                user = random.choice(USERS)
                token = login_user_session(sess, user["username"], user["password"])
                if token:
                    _stats["login_ok"] += 1
                else:
                    _stats["login_fail"] += 1
                _stats["ops"] += 1
                local_ops += 1

        else:  # crm
            # attempt login for a user
            user = random.choice(USERS)
            token = login_user_session(sess, user["username"], user["password"])
            if not token:
                # try to register & login quick
                uname = fake.unique.user_name() + str(uuid.uuid4())[:6]
                pwd = fake.password()
                if register_user_session(sess, uname, pwd):
                    USERS.append({"username": uname, "password": pwd})
                    token = login_user_session(sess, uname, pwd)

            if token:
                order_id = create_order(sess)
                if order_id:
                    _stats["create_ok"] += 1
                    if random.random() < 0.5:
                        time.sleep(random.uniform(0.05, 0.3))
                        if delete_order(sess, order_id):
                            _stats["delete_ok"] += 1
                        else:
                            _stats["delete_fail"] += 1
                else:
                    _stats["create_fail"] += 1
                _stats["ops"] += 1
                local_ops += 1
            else:
                _stats["ops"] += 1
                _stats["login_fail"] += 1
                local_ops += 1

        now = time.time()
        if now - last_report >= REPORT_INTERVAL:
            logger.info(f"[WORKER {worker_id}] local_ops={local_ops}")
            local_ops = 0
            last_report = now

        elapsed = time.time() - start
        to_sleep = max(0.0, INTERVAL - elapsed)
        time.sleep(to_sleep)

# ---------- monitor thread to print global stats ----------
def stats_reporter():
    last_ops = 0
    while True:
        time.sleep(REPORT_INTERVAL)
        ops = _stats.get("ops", 0)
        ops_delta = ops - last_ops
        last_ops = ops
        logger.info("OPS in last %ds: %d (total_ops=%d) registers OK/FAIL %d/%d logins OK/FAIL %d/%d creates OK/FAIL %d/%d deletes OK/FAIL %d/%d",
                    REPORT_INTERVAL,
                    ops_delta, ops,
                    _stats.get("register_ok",0), _stats.get("register_fail",0),
                    _stats.get("login_ok",0), _stats.get("login_fail",0),
                    _stats.get("create_ok",0), _stats.get("create_fail",0),
                    _stats.get("delete_ok",0), _stats.get("delete_fail",0)
                    )

# ---------- main ----------
def main():
    logger.info("Starting db_stresser workers=%d target_ops_per_sec=%d interval_per_worker=%.3fs", WORKERS, TARGET_OPS_PER_SEC, INTERVAL)
    t = threading.Thread(target=stats_reporter, daemon=True)
    t.start()

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i in range(WORKERS):
            ex.submit(worker_task, i+1)

if __name__ == "__main__":
    main()
