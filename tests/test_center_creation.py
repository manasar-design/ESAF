# test_center_creation.py
"""
For each user login in DATA/emails_DEV_FO.json:
  1. Login                                          -> auth token
  2. POST /api/v2/meeting-details/update             -> propose a uniquely named center
  3. POST /api/v1/centers                            -> create the center from step 2's id
  4. POST /api/v1/organisation/centers/{id}/update-geo-socio-details -> fill in center details

All 4 calls run under the SAME user's auth token, so each user ends up
with their own center created + updated end to end.
"""
from playwright.sync_api import sync_playwright
from main import encode_data, decode_data
import random
import string
import time
import json
import csv
import os

# ============================================================
# CONFIGURATION
# ============================================================
BASE_URL = "https://esaf-dev-api.esthenos.com"
# BASE_URL = "https://guat-api.esafbank.com"


# NOTE: login endpoint isn't one of the 3 APIs given. /web/api/v1/... (the
# admin/web login used in test_employee_creation.py) returns "Access Denied:
# Mobile-Only Account" for FO users — this backend needs a separate mobile
# login endpoint for FO accounts that we don't have yet. See TODO below.
LOGIN_ENDPOINT            = f"{BASE_URL}/api/v1/token/sourcing"
MEETING_DETAILS_ENDPOINT  = f"{BASE_URL}/api/v2/meeting-details/update"
MEETING_LIST_ENDPOINT     = f"{BASE_URL}/api/v2/meeting-list-details"
CENTERS_ENDPOINT          = f"{BASE_URL}/api/v1/centers"
GEO_SOCIO_ENDPOINT_TMPL   = f"{BASE_URL}/api/v1/organisation/centers/{{center_id}}/update-geo-socio-details"

# Login is always AES-encrypted (matches the proven employee_creation flow).
# The 3 center APIs were handed over as plain readable JSON, so they default
# to unencrypted; flip to True if the server actually expects AES here too.
ENCRYPT_CENTER_PAYLOADS = True

LOGIN_HEADERS = {
    "channel": "mobile",
    "device-type": "android",
    "app-version": "2.0.6-SIT",
    "Content-Type": "text/plain",
    # Below headers captured from a working Postman request (mobile login) —
    # X-fos-FB-token looked truncated in the screenshot it was copied from,
    # confirm the full value before relying on it.
    "X-fos-APKVERSION": "1.0.7-DEBUG",
    "X-fos-FB-token": "fdcAHNg8Qy6YioQ1u-1IFX:APA91bHzWhQYE53zA-fXMEB0ydF2U9cNsh",
    "LATITUDE": "12.9646815",
    "LONGITUDE": "77.6439036",
    "device-mac-id": "f2166c84024b7977"
}

DEFAULT_PASSWORD = "Esaf@123"
TOTAL_CENTERS = 1  # How many users/centers to process from USERS_FILE — 1 user for now
USERS_OFFSET = 0   # Skip this many users from the top

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
# emails_DEV_FO.json doesn't exist yet (only emails_DEV_FO.txt, a single "email"
# column with no password). Pointing at the .txt for now — load_users() below
# handles both formats, so this can switch to a .json with real passwords later.
USERS_FILE  = os.path.abspath(os.path.join(BASE_DIR, "..", "DATA", "emails_DEV_FO.txt"))
RESULTS_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "results"))
CSV_CENTER_DETAILS = os.path.join(RESULTS_DIR, "center_creation_details.csv")

# --- Response field names used to chain ids between calls. ---
# Not confirmed against a live response yet: if extraction fails, the
# script prints the full raw response — add the real key name here.
NEW_CENTER_ID_FIELDS = ["new_center", "center_meeting_id", "meeting_id", "id", "_id"]
CENTER_ID_FIELDS     = ["center_id", "id", "_id", "center_meeting_id"]
MEETING_ID_FIELDS    = ["meeting_id", "center_meeting_id", "_id", "id"]

# --- Static payload defaults, lifted from the sample payloads. ---
LOCATION = {"latitude": 12.9647037, "longitude": 77.6438685, "tagged_address": ""}

CENTER_FORM_DEFAULTS = {
    "date_im": "09/09/2026",
    "im_done": "Yes",
    "is_followup_required": "No",
    "lead": "By Survey",
    "place_im": "Bangalore",
    "total_attendance": "500"
}

GEO_SOCIO_DEFAULTS = {
    "center_address": "bangalore",
    "center_coordinator": None,
    "center_coordinator_id": None,
    "center_coordinator_mobile": None,
    "center_coordinator_role": None,
    "center_group_product": None,
    "center_pincode": "590001",
    "client": None,
    "landmark": "school",
    "day": "Friday",
    "time": "11:00 AM",
    "week": "1st",
    "repayment_frequency": "Monthly",
    # "village_id": "EVL26050400001",
    # "village_name": "Kanigiri"
}

CENTER_CSV_HEADER = ["username", "center_name", "new_center_id", "center_id", "status"]


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


def save_center_result(row: dict):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    write_header = not os.path.exists(CSV_CENTER_DETAILS)
    with open(CSV_CENTER_DETAILS, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CENTER_CSV_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def generate_center_name() -> str:
    return f"BANGALORE{time.strftime('%d%m')}"


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

    content_type = "text/plain" if ENCRYPT_CENTER_PAYLOADS else "application/json"
    return {
        **LOGIN_HEADERS,
        "Content-Type": content_type,
        "instance-token": auth_token
    }


# ============================================================
# API 1 — CREATE / PROPOSE CENTER (meeting-details/update)
# ============================================================
def create_meeting_details(request, auth_headers: dict, center_name: str) -> bool:
    payload = {
        "center_form": {
            **CENTER_FORM_DEFAULTS,
            "propose_center": center_name
        },
        "location": LOCATION
    }

    res, resp_json = call_api(
        request, "CREATE MEETING DETAILS", MEETING_DETAILS_ENDPOINT,
        auth_headers, payload, encrypt=ENCRYPT_CENTER_PAYLOADS
    )

    if res.status not in (200, 201) or not resp_json or resp_json.get("success") is False:
        print(f"  ❌ meeting-details/update failed. Status: {res.status}. Message: {resp_json.get('message') if resp_json else None}")
        return False

    # This response carries no id (just {"errors": [], "message": ..., "success":
    # true}) — the id has to be looked up afterwards via meeting-list-details.
    return True


# ============================================================
# API 1b — LOOK UP THE JUST-CREATED MEETING'S ID
# ============================================================
def get_last_meeting_id(request, auth_headers: dict):
    res, resp_json = call_get_api(request, "MEETING LIST DETAILS", MEETING_LIST_ENDPOINT, auth_headers)

    if res.status not in (200, 201) or not resp_json:
        print(f"  ❌ meeting-list-details failed. Status: {res.status}")
        return None

    items = resp_json if isinstance(resp_json, list) else None
    if items is None and isinstance(resp_json, dict):
        for key in ("data", "result", "meetings", "meeting_list", "list", "introduction_meetings_list"):
            value = resp_json.get(key)
            if isinstance(value, list):
                items = value
                break

    if not items:
        print(f"  ⚠️  Could not find a meeting list in the response.")
        print(f"      Full response: {json.dumps(resp_json, indent=4)}")
        return None

    return extract_field(items[-1], MEETING_ID_FIELDS, "last meeting_id")


# ============================================================
# API 2 — CREATE CENTER
# ============================================================
def create_center(request, auth_headers: dict, new_center_id):
    payload = {"center_meeting_id": new_center_id}

    res, resp_json = call_api(
        request, "CREATE CENTER", CENTERS_ENDPOINT,
        auth_headers, payload, encrypt=ENCRYPT_CENTER_PAYLOADS
    )

    if res.status not in (200, 201) or not resp_json or resp_json.get("success") is False:
        print(f"  ❌ centers create failed. Status: {res.status}. Message: {resp_json.get('message') if resp_json else None}")
        return None

    return extract_field(resp_json, CENTER_ID_FIELDS, "center_id")


# ============================================================
# API 3 — UPDATE GEO/SOCIO DETAILS
# ============================================================
def build_geo_socio_payload(center_id, center_name: str) -> dict:
    formation_date = CENTER_FORM_DEFAULTS["date_im"].replace("/", "")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    now_12h = time.strftime("%I:%M:%S %p").lower()

    return {
        **GEO_SOCIO_DEFAULTS,
        "center_name": center_name,
        "formation_date": formation_date,
        "location": LOCATION,
        "meeting_time_start": now_12h,
        "meeting_end_time": now_12h,
        "meeting_video_key": f"centers/{center_id}/IM/{center_id}_{stamp}.mp4",
        "meeting_video_path": (
            f"/storage/emulated/0/Android/data/gravity.esaf.bank.debug/"
            f"files/Pictures/{center_id}_{stamp}.mp4"
        )
    }


def update_geo_socio_details(request, auth_headers: dict, center_id, center_name: str) -> bool:
    endpoint = GEO_SOCIO_ENDPOINT_TMPL.format(center_id=center_id)
    payload = build_geo_socio_payload(center_id, center_name)

    res, resp_json = call_api(
        request, "UPDATE GEO-SOCIO DETAILS", endpoint,
        auth_headers, payload, encrypt=ENCRYPT_CENTER_PAYLOADS
    )

    if res.status not in (200, 201) or not resp_json or resp_json.get("success") is False:
        print(f"  ❌ update-geo-socio-details failed. Status: {res.status}. Message: {resp_json.get('message') if resp_json else None}")
        return False

    print(f"  ✅ Center {center_id} ({center_name}) geo/socio details updated.")
    return True


# ============================================================
# PER-USER FLOW
# ============================================================
def process_user(request, user: dict) -> dict:
    username = user.get("username")
    password = user.get("password", DEFAULT_PASSWORD)

    result = {
        "username": username, "center_name": "",
        "new_center_id": "", "center_id": "", "status": "FAILED"
    }

    print(f"\n{'=' * 50}\n🚀 Processing user: {username}\n{'=' * 50}")

    auth_headers = perform_login(request, username, password)
    if not auth_headers:
        return result

    center_name = generate_center_name()
    result["center_name"] = center_name
    print(f"  🏷️  Proposed center name: {center_name}")

    if not create_meeting_details(request, auth_headers, center_name):
        return result

    meeting_id = get_last_meeting_id(request, auth_headers)
    if not meeting_id:
        return result
    result["new_center_id"] = meeting_id
    print(f"  🆕 last meeting_id: {meeting_id}")

    # meeting_id is also expected in the header for the create-center call,
    # in addition to the request body.
    center_headers = {**auth_headers, "meeting_id": str(meeting_id)}

    center_id = create_center(request, center_headers, meeting_id)
    if not center_id:
        return result
    result["center_id"] = center_id
    print(f"  🏢 center id: {center_id}")

    if update_geo_socio_details(request, auth_headers, center_id, center_name):
        result["status"] = "SUCCESS"

    return result


# ============================================================
# SUMMARY
# ============================================================
def print_summary(results: list):
    success = sum(1 for r in results if r["status"] == "SUCCESS")
    failed = len(results) - success

    print("\n" + "=" * 50)
    print("📊 EXECUTION SUMMARY")
    print("=" * 50)
    print(f"  Total     : {len(results)}")
    print(f"  ✅ Success : {success}")
    print(f"  ❌ Failed  : {failed}")
    print("=" * 50)

    print(f"\n  {'Username':<35} {'Center Name':<40} {'Status':<10}")
    print(f"  {'-' * 90}")
    for r in results:
        print(f"  {r['username']:<35} {r['center_name']:<40} {r['status']:<10}")


# ============================================================
# MAIN FLOW
# ============================================================
def test_flow():
    print("\n🚀 Starting Center Creation Automation")

    users = load_users(TOTAL_CENTERS)
    print(f"👥 Users to process: {len(users)}\n")

    results = []
    with sync_playwright() as p:
        request = p.request.new_context()

        for user in users:
            result = process_user(request, user)
            results.append(result)
            save_center_result(result)

        request.dispose()

    print_summary(results)


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    test_flow()
