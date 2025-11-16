from flask import Flask, render_template, request, redirect, jsonify, Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from pymongo import MongoClient
import requests, os, logging
from bson import ObjectId

app = Flask(__name__)

# --------------------------
# CONFIGURATION
# --------------------------
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://localhost:5000")
AUTH_SERVICE_INNER = os.getenv("AUTH_SERVICE_INNER", "http://auth_service:5000")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/")
MONGO_DB = "crm"
MONGO_COLLECTION = "orders"

mongo_client = MongoClient(MONGO_URI)
orders_db = mongo_client[MONGO_DB][MONGO_COLLECTION]

# --------------------------
# LOGGING
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# --------------------------
# PROMETHEUS METRICS
# --------------------------
REQUEST_COUNT = Counter('crm_requests_total', 'Total number of requests', ['method', 'endpoint'])


@app.before_request
def before_request_func():
    if request.path != "/metrics":
        REQUEST_COUNT.labels(method=request.method, endpoint=request.path).inc()


@app.route('/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


# --------------------------
# AUTH VALIDATION HELPER
# --------------------------
def validate_session(req):
    token = req.cookies.get("auth_token")
    if not token:
        return None

    try:
        resp = requests.get(
            f"{AUTH_SERVICE_INNER}/api/validate",
            cookies={"session_id": token},
            timeout=1.0
        )
    except Exception as e:
        logger.error("Auth-service unreachable: %s", str(e))
        return None

    data = resp.json()
    return data if resp.status_code == 200 and data.get("status") == "ok" else None


# --------------------------
# DASHBOARD PAGE
# --------------------------
@app.route("/dashboard")
def dashboard():
    session = validate_session(request)
    if not session:
        return redirect(f"{AUTH_SERVICE_URL}/login")

    username = session.get("name")

    user_orders = list(orders_db.find({"username": username}))
    for o in user_orders:
        o["_id"] = str(o["_id"])

    return render_template("dashboard.html", user=session, orders=user_orders)


@app.route("/")
def home():
    return redirect("/dashboard")


# --------------------------
# API — CRUD OPERATIONS
# --------------------------

# CREATE ORDER
@app.route("/api/orders", methods=["POST"])
def create_order():
    session = validate_session(request)
    if not session:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    item = data.get("item")
    price = data.get("price")

    if not item or not price:
        return jsonify({"error": "Missing fields"}), 400

    order = {
        "username": session["name"],
        "item": item,
        "price": price
    }

    inserted = orders_db.insert_one(order)

    return jsonify({"status": "ok", "order_id": str(inserted.inserted_id)}), 201


# READ ALL USER ORDERS
@app.route("/api/orders", methods=["GET"])
def get_orders():
    session = validate_session(request)
    if not session:
        return jsonify({"error": "Unauthorized"}), 403

    username = session["name"]
    orders = list(orders_db.find({"username": username}))

    for o in orders:
        o["_id"] = str(o["_id"])

    return jsonify({"orders": orders})


# DELETE ORDER
@app.route("/api/orders/<order_id>", methods=["DELETE"])
def delete_order(order_id):
    session = validate_session(request)
    if not session:
        return jsonify({"error": "Unauthorized"}), 403

    username = session["name"]

    result = orders_db.delete_one({
        "_id": ObjectId(order_id),
        "username": username
    })

    if result.deleted_count == 0:
        return jsonify({"error": "Order not found"}), 404

    return jsonify({"status": "deleted"})


# UPDATE ORDER
@app.route("/api/orders/<order_id>", methods=["PUT"])
def update_order(order_id):
    session = validate_session(request)
    if not session:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    item = data.get("item")
    price = data.get("price")

    updates = {}
    if item:
        updates["item"] = item
    if price:
        updates["price"] = price

    if not updates:
        return jsonify({"error": "Nothing to update"}), 400

    username = session["name"]

    result = orders_db.update_one(
        {"_id": ObjectId(order_id), "username": username},
        {"$set": updates}
    )

    if result.matched_count == 0:
        return jsonify({"error": "Order not found"}), 404

    return jsonify({"status": "updated"})


# --------------------------
# APP START
# --------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
