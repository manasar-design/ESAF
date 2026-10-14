# test_business_sourcing.py
"""
Mobile business sourcing flow for one FO user:
  1. POST /api/v1/token/sourcing                                   -> auth token
  2. GET  /api/v1/products-categories                              -> product categories
  3. GET  /api/v1/workflow/GL                                      -> GL workflow
  4. GET  /api/v2/meeting-list-details                             -> meeting list
  5. GET  /api/v2/organisation/centers?status=active&application_type=business_sourcing&page=1
                                                                   -> pick center_id(s)
  6. GET  /api/v1/customers?center_id=...                          -> customer list for a center
  7. GET  /api/v1/sourcing/customer?customer_id=...                -> customer details
"""
from playwright.sync_api import sync_playwright
from main import encode_data, decode_data
import csv
import json
import os
import time

# ============================================================
# CONFIGURATION
# ============================================================
BASE_URL = "https://esaf-dev-api.esthenos.com"
# BASE_URL = "https://gravity-sit-api.esafbank.com"
# BASE_URL = "https://gravity-api.esaf.bank.in"

LOGIN_ENDPOINT = f"{BASE_URL}/api/v1/token/sourcing"

# Login is always AES-encrypted; this controls the sourcing API payloads.
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
    "device-mac-id": "f2166c84024b7977",
    # The production edge (Akamai) 403s a generic client; the app's UA gets through.
    "User-Agent": "GravityBank-Android/1.0"
}

DEFAULT_PASSWORD = "Esaf@123"
USERS_OFFSET = 0   # Skip this many users from the top
TOTAL_USERS = 1

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
USERS_FILE  = os.path.abspath(os.path.join(BASE_DIR, "..", "DATA", "emails_DEV_FO.txt"))
# USERS_FILE  = os.path.abspath(os.path.join(BASE_DIR, "..", "DATA", "emails_SIT_FO.txt"))
# USERS_FILE  = os.path.abspath(os.path.join(BASE_DIR, "..", "DATA", "emails_UAT_FO.txt"))

PRODUCTS_CATEGORIES_ENDPOINT = f"{BASE_URL}/api/v1/products-categories"
WORKFLOW_GL_ENDPOINT         = f"{BASE_URL}/api/v1/workflow/RMFL"
MEETING_LIST_ENDPOINT        = f"{BASE_URL}/api/v2/meeting-list-details"
CENTERS_ENDPOINT             = f"{BASE_URL}/api/v2/organisation/centers"
CUSTOMERS_ENDPOINT           = f"{BASE_URL}/api/v1/customers"
CUSTOMER_DETAILS_ENDPOINT    = f"{BASE_URL}/api/v1/sourcing/customer"
CUSTOMER_DEDUPE_ENDPOINT     = f"{BASE_URL}/api/v2/customer/dedupe"

# Dedupe check run against the selected center.
DEDUPE_ID_TYPE       = "Aadhaar Card"
DEDUPE_ID_VALUE      = "303725520712"
DEDUPE_CATEGORY_CODE = "RMFL"

CENTERS_PAGE = 1
# Center to source from: matched against center_id / external_center_id / name
# across all pages of the centers list. None = walk the first page as before.
TARGET_CENTER = "80450041"
CENTER_MATCH_FIELDS = ["center_id", "id", "external_center_id", "name"]
# How deep to walk: None = every center / every customer.
MAX_CENTERS = 1
MAX_CUSTOMERS_PER_CENTER = 1

CENTER_ID_FIELDS   = ["center_id", "id", "_id"]
CUSTOMER_ID_FIELDS = ["customer_id", "id", "_id"]
LAST_STATUS = {}  # API name -> last HTTP status, filled by call_get_api for run summaries
LIST_KEYS = ("centers_list", "customers_list", "customer_list", "data", "result",
             "centers", "center_list", "customers", "applications", "list", "results")


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
    for nested_key in ("data", "result"):
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
    LAST_STATUS[name] = res.status

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

    # /api/v1/token/sourcing expects an AES-encrypted body (text/plain).
    res, login_json = call_api(request, "LOGIN", LOGIN_ENDPOINT, LOGIN_HEADERS, payload, encrypt=True)

    if res.status != 200 or not login_json:
        print(f"  ❌ Login failed for {username}. Status: {res.status}")
        return None

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
# API 1-3 — MASTER DATA (products, GL workflow, meeting list)
# ============================================================
def get_products_categories(request, auth_headers: dict):
    res, resp_json = call_get_api(request, "PRODUCTS CATEGORIES", PRODUCTS_CATEGORIES_ENDPOINT, auth_headers)
    if res.status != 200:
        print(f"  ❌ products-categories failed. Status: {res.status}")
    return resp_json


def get_workflow_gl(request, auth_headers: dict):
    res, resp_json = call_get_api(request, "WORKFLOW GL", WORKFLOW_GL_ENDPOINT, auth_headers)
    if res.status != 200:
        print(f"  ❌ workflow/GL failed. Status: {res.status}")
    return resp_json


def get_meeting_list_details(request, auth_headers: dict):
    res, resp_json = call_get_api(request, "MEETING LIST DETAILS", MEETING_LIST_ENDPOINT, auth_headers)
    if res.status != 200:
        print(f"  ❌ meeting-list-details failed. Status: {res.status}")
    return resp_json


# ============================================================
# API 4 — SOURCING CENTERS
# ============================================================
def get_sourcing_centers(request, auth_headers: dict, page: int = CENTERS_PAGE) -> list:
    endpoint = (
        f"{CENTERS_ENDPOINT}?status=active"
        f"&application_type=business_sourcing&page={page}"
    )
    res, resp_json = call_get_api(request, "SOURCING CENTERS", endpoint, auth_headers)

    if res.status != 200 or not resp_json:
        print(f"  ❌ organisation/centers failed. Status: {res.status}")
        return [], False

    centers = find_list(resp_json) or []
    if not centers:
        print(f"  ⚠️  No active business sourcing centers on page {page}.")
    return centers, bool((resp_json.get("pagination") or {}).get("has_next"))


def find_target_center(request, auth_headers: dict, target: str):
    """Page through organisation/centers until a center matches `target`."""
    page = 1
    while True:
        centers, has_next = get_sourcing_centers(request, auth_headers, page)
        for center in centers:
            if any(str(center.get(f) or "") == target for f in CENTER_MATCH_FIELDS):
                print(f"  🎯 Found center {target} on page {page}: "
                      f"{center.get('name')} ({center.get('center_id')})")
                return center
        if not has_next:
            print(f"  ❌ Center {target} not found in any page of active business sourcing centers.")
            return None
        page += 1


# ============================================================
# API 5 — CUSTOMER LIST FOR A CENTER
# ============================================================
def get_customers(request, auth_headers: dict, center_id) -> list:
    endpoint = f"{CUSTOMERS_ENDPOINT}?center_id={center_id}"
    res, resp_json = call_get_api(request, f"CUSTOMERS {center_id}", endpoint, auth_headers)

    if res.status != 200 or not resp_json:
        print(f"  ❌ customers failed for center {center_id}. Status: {res.status}")
        return []

    customers = find_list(resp_json) or []
    if not customers:
        print(f"  ⚠️  No customers in center {center_id}.")
    return customers


# ============================================================
# API 6 — CUSTOMER DETAILS
# ============================================================
def get_customer_details(request, auth_headers: dict, customer_id):
    endpoint = f"{CUSTOMER_DETAILS_ENDPOINT}?customer_id={customer_id}"
    res, resp_json = call_get_api(request, f"CUSTOMER DETAILS {customer_id}", endpoint, auth_headers)

    if res.status != 200 or not resp_json:
        print(f"  ❌ sourcing/customer failed for customer {customer_id}. Status: {res.status}")
        return None
    return resp_json


# ============================================================
# API 7 — CUSTOMER DEDUPE
# ============================================================
def customer_dedupe(request, auth_headers: dict, center_id):
    payload = {
        "id_type": DEDUPE_ID_TYPE,
        "id_value": DEDUPE_ID_VALUE,
        "customer_id": "",
        "center_id": center_id,
        "category_code": DEDUPE_CATEGORY_CODE
    }
    res, resp_json = call_api(
        request, "CUSTOMER DEDUPE", CUSTOMER_DEDUPE_ENDPOINT,
        auth_headers, payload, encrypt=ENCRYPT_PAYLOADS
    )
    LAST_STATUS["CUSTOMER DEDUPE"] = res.status

    if res.status not in (200, 201) or not resp_json:
        print(f"  ❌ customer/dedupe failed for center {center_id}. Status: {res.status}")
        return None
    return resp_json


def run_sourcing_sequence(request, auth_headers: dict) -> dict:
    """All business sourcing GET APIs for one logged-in user, in app order:
        products-categories -> workflow/GL -> meeting-list-details -> centers
        -> customers per center -> customer details per customer -> customer dedupe.
    A failing master-data call is logged but doesn't stop the rest."""
    LAST_STATUS.clear()
    summary = {"total_centers": 0, "total_customers": 0, "details_ok": 0, "details_failed": 0}

    get_products_categories(request, auth_headers)
    get_workflow_gl(request, auth_headers)
    get_meeting_list_details(request, auth_headers)

    if TARGET_CENTER:
        target = find_target_center(request, auth_headers, TARGET_CENTER)
        centers = [target] if target else []
    else:
        centers, _ = get_sourcing_centers(request, auth_headers)
    summary["total_centers"] = len(centers)

    for center in centers[:MAX_CENTERS]:
        center_id = extract_field(center, CENTER_ID_FIELDS, "center_id")
        if not center_id:
            continue

        customers = get_customers(request, auth_headers, center_id)
        summary["total_customers"] += len(customers)
        print(f"\n  🏢 {center.get('name') or center_id} ({center_id}): {len(customers)} customer(s)")

        for customer in customers[:MAX_CUSTOMERS_PER_CENTER]:
            customer_id = extract_field(customer, CUSTOMER_ID_FIELDS, "customer_id")
            if not customer_id:
                continue
            if get_customer_details(request, auth_headers, customer_id):
                summary["details_ok"] += 1
            else:
                summary["details_failed"] += 1

        customer_dedupe(request, auth_headers, center_id)

    return summary


# ============================================================
# MAIN FLOW
# ============================================================
def test_flow():
    print(f"\n🚀 Starting Business Sourcing Automation")
    users = load_users(TOTAL_USERS)

    with sync_playwright() as p:
        request = p.request.new_context()

        for user in users:
            username = user["username"]
            print(f"\n{'=' * 50}\n🚀 Processing user: {username}\n{'=' * 50}")

            auth_headers = perform_login(request, username, user.get("password", DEFAULT_PASSWORD))
            if not auth_headers:
                continue

            seq = run_sourcing_sequence(request, auth_headers)
            print(f"\n  📋 statuses={json.dumps(LAST_STATUS)}")
            print(f"  📊 total_centers={seq['total_centers']} total_customers={seq['total_customers']} "
                  f"details_ok={seq['details_ok']} details_failed={seq['details_failed']}")

        request.dispose()


if __name__ == "__main__":
    test_flow()
