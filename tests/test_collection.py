# test_collection.py
"""
Mobile collection flow for one FO user:
  1. Login                                              -> auth token
  2. GET /api/v1/collections/centers                    -> pick a center_id
  3. GET /api/v2/collections?center_id=...              -> collection data for that center
  4. POST <collection submit API>                       -> built from step 3's data (TODO)
"""
from playwright.sync_api import sync_playwright
from main import encode_data, decode_data
import contextlib
import csv
import io
import json
import os
import time

# ============================================================
# CONFIGURATION
# ============================================================
# BASE_URL = "https://esaf-dev-api.esthenos.com"
# BASE_URL = "https://gravity-sit-api.esafbank.com"
BASE_URL = "https://guat-api.esafbank.com"

LOGIN_ENDPOINT = f"{BASE_URL}/api/v1/token/sourcing"

# Login is always AES-encrypted; this controls the collection API payloads.
ENCRYPT_PAYLOADS = True

LOGIN_HEADERS = {
    "channel": "mobile",
    "device-type": "android",
    "app-version": "2.0.6-SIT",
    "Content-Type": "text/plain",
    "X-fos-APKVERSION": "1.0.7-DEBUG",
    "X-fos-FB-token": "fdcAHNg8Qy6YioQ1u-1IFX:APA91bHzWhQYE53zA-fXMEB0ydF2U9cNsh",
    "LATITUDE": "12.9646815",
    "LONGITUDE": "77.6439036",
    "device-mac-id": "f2166c84024b7977"
}

DEFAULT_PASSWORD = "Esaf@123"
USERS_OFFSET = 0   # Skip this many users from the top

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
# USERS_FILE  = os.path.abspath(os.path.join(BASE_DIR, "..", "DATA", "emails_DEV_FO.txt"))
# USERS_FILE  = os.path.abspath(os.path.join(BASE_DIR, "..", "DATA", "emails_SIT_FO.txt"))
USERS_FILE  = os.path.abspath(os.path.join(BASE_DIR, "..", "DATA", "emails_UAT_FO.txt"))
RESULTS_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "results"))

COLLECTION_DATE = time.strftime("%Y-%m-%d")  # e.g. 2026-09-27
LOAN_TYPE = "center"
TOTAL_USERS = 1
LIST_ALL_CENTER_CUSTOMERS = True  # print every center's customer list before collecting

COLLECTION_CENTERS_ENDPOINT = f"{BASE_URL}/api/v1/collections/centers"
COLLECTIONS_ENDPOINT        = f"{BASE_URL}/api/v2/collections"
MAKE_PAYMENTS_ENDPOINT      = f"{BASE_URL}/api/v1/make-payments"
PAYMENT_MODE = "CASH"
# Safety switch: False = only build + print the make-payments payload (dry run).
# Set True only when you actually want the collection posted.
SUBMIT_PAYMENTS = True
# v2/collections has no "loan_id" field — application_id is the loan account
# number, so it's sent as loan_id too. Change here if the backend expects otherwise.
LOAN_ID_FIELD = "application_id"

CENTER_ID_FIELDS = ["center_id", "id", "_id"]
LIST_KEYS = ("centers_list", "collections_list", "data", "result", "centers", "center_list", "collections", "list")


def find_list(resp_json):
    """Return the first list found at top level or under a common container key."""
    if isinstance(resp_json, list):
        return resp_json
    if isinstance(resp_json, dict):
        for key in LIST_KEYS:
            value = resp_json.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = find_list(value)
                if nested:
                    return nested
    return None


# ============================================================
# CSV / DATA HELPERS
# ============================================================
def load_users(limit: int) -> list:
    """
    Supports two formats:
      - .json: a list of {"username": ..., "password": ...} objects
      - .txt/.csv: a single "email" column, one address per line — each
        row becomes {"username": email, "password": DEFAULT_PASSWORD}
    """
    if not os.path.exists(USERS_FILE):
        raise FileNotFoundError(f"❌ Users file not found: {USERS_FILE}")

    if USERS_FILE.endswith(".json"):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
    else:
        with open(USERS_FILE, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            users = [
                {"username": row["email"].strip(), "password": DEFAULT_PASSWORD}
                for row in reader
                if row.get("email", "").strip()
            ]

    if not users:
        raise ValueError(f"❌ No users found in {USERS_FILE}")

    return users[USERS_OFFSET:USERS_OFFSET + limit]


def extract_field(response_json: dict, candidates: list, label: str):
    """
    Look for the first matching key among `candidates`, checking the
    top level and common nested containers ("data", "result").
    """
    if not isinstance(response_json, dict):
        return None

    containers = [response_json]
    for nested_key in ("data", "result", "center_details"):
        nested = response_json.get(nested_key)
        if isinstance(nested, dict):
            containers.append(nested)

    for container in containers:
        for key in candidates:
            value = container.get(key)
            if value not in (None, ""):
                return value

    print(f"  ⚠️  Could not find {label} using keys {candidates}.")
    print(f"      Full response: {json.dumps(response_json, indent=4)}")
    return None


# ============================================================
# REQUEST / RESPONSE LOGGING
# ============================================================
def log_request(name, method, endpoint, headers, payload=None, encrypted_payload=None):
    print(f"\n{'─' * 50}")
    print(f"  📤 REQUEST  →  {name}")
    print(f"{'─' * 50}")
    print(f"  Method   : {method}")
    print(f"  Endpoint : {endpoint}")
    print(f"\n  📋 Headers:")
    print(json.dumps(headers, indent=4))

    if payload is not None:
        print(f"\n  📦 Payload (raw JSON):")
        print(json.dumps(payload, indent=4))

    if encrypted_payload is not None:
        preview = encrypted_payload[:80] + "..." if len(encrypted_payload) > 80 else encrypted_payload
        print(f"\n  🔒 Encrypted Payload ({len(encrypted_payload)} chars):")
        print(f"  {preview}")

    print(f"{'─' * 50}")


def log_api(name: str, status: int, response_text: str, start_time: float):
    duration = round(time.time() - start_time, 2)

    print(f"\n{'=' * 40}")
    print(f"  📥 RESPONSE  ←  {name} API")
    print(f"{'=' * 40}")
    print(f"  Status     : {status}")
    print(f"  Time Taken : {duration} sec")

    if not response_text or response_text.strip() == "":
        print("  ⚠️  Empty response body.")
        return None, duration

    # 1. Try AES decryption first (in case the backend always encrypts responses).
    try:
        decrypted = decode_data(response_text)
        if decrypted and decrypted.strip():
            try:
                parsed = json.loads(decrypted)
                print("  Response (decrypted JSON):")
                print(json.dumps(parsed, indent=4))
                return parsed, duration
            except json.JSONDecodeError:
                print(f"  Response (decrypted string): {decrypted}")
                return {"message": decrypted}, duration
    except Exception:
        pass

    # 2. Plain JSON.
    try:
        parsed = json.loads(response_text)
        print("  Response (plain JSON):")
        print(json.dumps(parsed, indent=4))
        return parsed, duration
    except Exception:
        pass

    # 3. Raw fallback.
    print(f"  Raw Response : {response_text[:500]}")
    return None, duration


def call_api(request, name: str, endpoint: str, headers: dict, payload: dict, encrypt: bool):
    body = encode_data(json.dumps(payload)) if encrypt else json.dumps(payload)

    log_request(
        name=name,
        method="POST",
        endpoint=endpoint,
        headers=headers,
        payload=payload,
        encrypted_payload=body if encrypt else None
    )

    start = time.time()
    res = request.post(endpoint, headers=headers, data=body)
    resp_json, _ = log_api(name, res.status, res.text(), start)

    return res, resp_json


def call_get_api(request, name: str, endpoint: str, headers: dict):
    log_request(name=name, method="GET", endpoint=endpoint, headers=headers)

    start = time.time()
    res = request.get(endpoint, headers=headers)
    resp_json, _ = log_api(name, res.status, res.text(), start)

    return res, resp_json


# ============================================================
# LOGIN
# ============================================================
def perform_login(request, username: str, password: str):
    payload = {
        "email": username,
        "password": password,
        "verify_two_factor_otp": True,
        "otp": "123456"
    }

    # /api/v1/token/sourcing expects an AES-encrypted body (text/plain), same
    # as the /web/api/v1 admin login — confirmed live: sending plain JSON here
    # crashes the server with a 500, encrypted gets a real response.
    res, login_json = call_api(request, "LOGIN", LOGIN_ENDPOINT, LOGIN_HEADERS, payload, encrypt=True)

    if res.status != 200 or not login_json:
        print(f"  ❌ Login failed for {username}. Status: {res.status}")
        return None

    # "token" holds the real auth token; "message" is just a status string
    # (e.g. "token generated") and must not be used as a fallback ahead of it.
    auth_token = (
        login_json.get("token")
        or login_json.get("data", {}).get("token")
    )

    if not auth_token or not isinstance(auth_token, str) or len(auth_token) < 5:
        print(f"  ❌ Invalid token for {username}: '{auth_token}'")
        return None

    print(f"  ✅ Login successful for {username}.")

    content_type = "text/plain" if ENCRYPT_PAYLOADS else "application/json"
    return {
        **LOGIN_HEADERS,
        "Content-Type": content_type,
        "instance-token": auth_token
    }


# ============================================================
# API 1 — COLLECTION CENTERS
# ============================================================
def get_collection_centers(request, auth_headers: dict) -> list:
    endpoint = f"{COLLECTION_CENTERS_ENDPOINT}?collection_date={COLLECTION_DATE}&loan_type={LOAN_TYPE}"
    res, resp_json = call_get_api(request, "COLLECTION CENTERS", endpoint, auth_headers)

    if res.status != 200 or not resp_json:
        print(f"  ❌ collections/centers failed. Status: {res.status}")
        return []

    centers = find_list(resp_json) or []
    if not centers:
        print(f"  ⚠️  No centers due for collection on {COLLECTION_DATE}.")
    return centers


def get_collection_center_id(centers: list):
    # Skip centers already fully collected today.
    pending = [c for c in centers if (c.get("pending_collection") or 0) > 0]
    if not pending:
        print(f"  ⚠️  All centers already fully collected on {COLLECTION_DATE}.")
        return None

    # Pick the center with the smallest non-zero pending amount.
    smallest = min(pending, key=lambda c: c["pending_collection"])
    print(f"  🎯 Least pending center: {smallest.get('center_name')} ({smallest.get('pending_collection')})")
    return extract_field(smallest, CENTER_ID_FIELDS, "collection center_id")


# ============================================================
# API 2 — COLLECTION DETAILS FOR A CENTER
# ============================================================
def get_collections(request, auth_headers: dict, center_id):
    endpoint = (
        f"{COLLECTIONS_ENDPOINT}?collection_date={COLLECTION_DATE}"
        f"&loan_type={LOAN_TYPE}&center_id={center_id}"
    )
    res, resp_json = call_get_api(request, "COLLECTIONS", endpoint, auth_headers)

    if res.status != 200 or not resp_json:
        print(f"  ❌ v2/collections failed. Status: {res.status}")
        return None

    # Only loans with a collection_object_id and a non-zero demand can be
    # collected; the rest are advance-collection / no-loan customer rows.
    dues, seen = [], set()
    for c in find_list(resp_json) or []:
        app_id = c.get("application_id")
        if not c.get("collection_object_id") or (c.get("total_demand") or 0) <= 0 or app_id in seen:
            continue  # the API sometimes repeats a loan row; never pay one twice
        seen.add(app_id)
        dues.append(c)
    if not dues:
        print(f"  ⚠️  No collectible loans in center {center_id}.")
        return None

    print(f"\n  💰 {len(dues)} collectible loan(s) in center {center_id}:")
    for c in dues:
        print(f"     {c['customer_name']:<30} app={c['application_id']} "
              f"demand={c['total_demand']} emi={c['emi_amount']} obj={c['collection_object_id']}")
    return dues


def get_center_customers(request, auth_headers: dict, center_id) -> list:
    endpoint = (
        f"{COLLECTIONS_ENDPOINT}?collection_date={COLLECTION_DATE}"
        f"&loan_type={LOAN_TYPE}&center_id={center_id}"
    )
    res, resp_json = call_get_api(request, f"CUSTOMERS {center_id}", endpoint, auth_headers)
    if res.status != 200 or not resp_json:
        print(f"  ❌ v2/collections failed for center {center_id}. Status: {res.status}")
        return []
    return find_list(resp_json) or []


def print_all_center_customers(request, auth_headers: dict, centers: list):
    with contextlib.redirect_stdout(io.StringIO()):  # silence per-call request/response dumps
        per_center = [(c, get_center_customers(request, auth_headers, c["center_id"])) for c in centers]

    print(f"\n{'=' * 90}\n👥 CUSTOMERS PER CENTER ({COLLECTION_DATE})\n{'=' * 90}")
    for center, customers in per_center:
        print(f"\n  🏢 {center['center_name']} ({center['center_id']}) — "
              f"{len(customers)} customer(s), pending {center.get('pending_collection')}")
        for c in customers:
            print(f"     {c.get('customer_name', ''):<32} cif={c.get('customer_cif_id', ''):<10} "
                  f"app={c.get('application_id') or '-':<17} demand={c.get('total_demand')}")


# ============================================================
# API 3 — MAKE PAYMENTS
# ============================================================
def build_payment_payload(center_id, dues: list) -> dict:
    return {
        "group_payments": [{
            "center_id": str(center_id),
            "latitude": 0,
            "longitude": 0,
            "payment_done_date": "",
            "payment_due_date": COLLECTION_DATE,
            "saving_info": [],
            "individual_payment": [
                {
                    "group_id": "",
                    "loan_id": c.get(LOAN_ID_FIELD, ""),
                    "application_id": c["application_id"],
                    "payment_id": "",
                    "mobile_number": c.get("mobile_number", ""),
                    "total_collection": c["total_demand"],
                    "is_attended": True,
                    "absent_reason": "",
                    "is_advance_collection": bool(c.get("is_advance_collection", False)),
                    "payment_mode": PAYMENT_MODE,
                    "ptp_info": {"ptp_scheduled_on": "", "remarks": ""},
                }
                for c in dues
            ],
        }]
    }


def save_payment_log(username, center_id, payload: dict, status=None, response=None):
    """Write the make-payments request payload + response body to results/ as JSON."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"make_payments_{COLLECTION_DATE}_{center_id}_{stamp}.json")
    record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "username": username,
        "center_id": str(center_id),
        "endpoint": MAKE_PAYMENTS_ENDPOINT,
        "submitted": SUBMIT_PAYMENTS,
        "status": status,
        "request_payload": payload,
        "response_body": response,
    }
    with open(path, "w") as f:
        json.dump(record, f, indent=4)
    print(f"  💾 Saved make-payments log: {path}")


def make_payments(request, auth_headers: dict, username, center_id, dues: list) -> bool:
    payload = build_payment_payload(center_id, dues)
    total = sum(c["total_demand"] for c in dues)

    if not SUBMIT_PAYMENTS:
        print(f"\n  🧪 DRY RUN — make-payments NOT submitted (SUBMIT_PAYMENTS = False).")
        print(f"     Would collect {total} from {len(dues)} loan(s) in center {center_id}.")
        print(f"     POST {MAKE_PAYMENTS_ENDPOINT}")
        print(json.dumps(payload, indent=4))
        save_payment_log(username, center_id, payload)
        return False

    res, resp_json = call_api(
        request, "MAKE PAYMENTS", MAKE_PAYMENTS_ENDPOINT,
        auth_headers, payload, encrypt=ENCRYPT_PAYLOADS
    )
    save_payment_log(username, center_id, payload, res.status, resp_json)

    if res.status not in (200, 201) or not resp_json or resp_json.get("success") is False:
        print(f"  ❌ make-payments failed. Status: {res.status}. Message: {resp_json.get('message') if resp_json else None}")
        return False

    print(f"  ✅ Collected {total} from {len(dues)} loan(s) in center {center_id}.")
    return True


# ============================================================
# MAIN FLOW
# ============================================================
def test_flow():
    print(f"\n🚀 Starting Collection Automation (date={COLLECTION_DATE})")
    users = load_users(TOTAL_USERS)

    with sync_playwright() as p:
        request = p.request.new_context()

        for user in users:
            username = user["username"]
            print(f"\n{'=' * 50}\n🚀 Processing user: {username}\n{'=' * 50}")

            auth_headers = perform_login(request, username, user.get("password", DEFAULT_PASSWORD))
            if not auth_headers:
                continue

            centers = get_collection_centers(request, auth_headers)
            if LIST_ALL_CENTER_CUSTOMERS and centers:
                print_all_center_customers(request, auth_headers, centers)

            center_id = get_collection_center_id(centers)
            if not center_id:
                continue
            print(f"  🏢 collection center_id: {center_id}")

            dues = get_collections(request, auth_headers, center_id)
            if not dues:
                continue

            make_payments(request, auth_headers, username, center_id, dues)

        request.dispose()


if __name__ == "__main__":
    test_flow()
