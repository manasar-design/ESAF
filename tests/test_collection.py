# test_collection.py
"""
Mobile collection flow for one FO user:
  1. Login                                              -> auth token
  2. GET /api/v1/collections/centers                    -> pick a center_id
  3. GET /api/v2/collections?center_id=...              -> collection data for that center
  4. POST <collection submit API>                       -> built from step 3's data (TODO)
"""
from playwright.sync_api import sync_playwright
from test_center_creation import (
    BASE_URL, DEFAULT_PASSWORD, ENCRYPT_CENTER_PAYLOADS, RESULTS_DIR,
    load_users, perform_login, call_get_api, call_api, extract_field,
)
import contextlib
import io
import json
import os
import time

# ============================================================
# CONFIGURATION
# ============================================================
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
        auth_headers, payload, encrypt=ENCRYPT_CENTER_PAYLOADS
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
