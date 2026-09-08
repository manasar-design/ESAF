# Overwrite with fixed version
#cat > jmeter/helpers/employee_creation.py << 'PYEOF'
#!/usr/bin/env python3
import sys
import os

# ============================================================
# PATH SETUP — main.py is inside tests/
# ============================================================
PROJECT_ROOT = "/home/lenovo/Desktop/Manasa/playwright-python/tests"
sys.path.insert(0, PROJECT_ROOT)

from main import encode_data, decode_data

import random
import string
import time
import json
import csv

# ============================================================
# CONFIGURATION
# ============================================================
BASE_URL        = "https://esaf-dev-api.esthenos.com/web/api/v1"
LOGIN_ENDPOINT  = f"{BASE_URL}/organisation/user_login"
CREATE_ENDPOINT = f"{BASE_URL}/organisation/employees"
UPDATE_ENDPOINT = f"{BASE_URL}/organisation/employee"

HIERARCHY_ID    = "657af9f94eef19efa4e1f1b0"
EMAIL_DOMAIN    = "esaf-dev.esthenos.com"

# CSV paths
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
DATA_DIR        = os.path.join(BASE_DIR, "..", "data")
RESULTS_DIR     = os.path.join(BASE_DIR, "..", "results")

CSV_USERS           = os.path.join(DATA_DIR,    "users.csv")
CSV_EMAILS          = os.path.join(RESULTS_DIR, "emails.csv")
CSV_EMPLOYEE_IDS    = os.path.join(RESULTS_DIR, "employee_ids.csv")
CSV_CREATE_RESPONSE = os.path.join(RESULTS_DIR, "create_response.csv")
CSV_EMPLOYEE_DATA   = os.path.join(RESULTS_DIR, "employee_data.csv")
CSV_SUMMARY         = os.path.join(RESULTS_DIR, "execution_summary.csv")

LOGIN_PAYLOAD = {
    "email": "ajay@esaf-dev.esthenos.com",
    "password": "Esaf@123",
    "verify_two_factor_otp": True,
    "otp": "123456"
}

LOGIN_HEADERS = {
    "channel"     : "mobile",
    "device-type" : "android",
    "app-version" : "2.0.6-SIT",
    "Content-Type": "text/plain"
}


# ============================================================
# CSV HELPERS
# ============================================================
def save_to_csv(file: str, header: list, row: list):
    os.makedirs(os.path.dirname(file), exist_ok=True)
    write_header = not os.path.exists(file)
    with open(file, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(header)
        writer.writerow(row)


def clean_csv_files():
    files = [
        CSV_EMAILS, CSV_EMPLOYEE_IDS,
        CSV_CREATE_RESPONSE, CSV_EMPLOYEE_DATA, CSV_SUMMARY
    ]
    for file in files:
        if os.path.exists(file):
            os.remove(file)
            print(f"🗑️  Removed: {file}")


def load_users_from_csv() -> list:
    """Load all users from users.csv into a list of dicts."""
    if not os.path.exists(CSV_USERS):
        raise FileNotFoundError(
            f"❌ users.csv not found at {CSV_USERS}\n"
            f"   Run: python jmeter/helpers/generate_users.py"
        )
    users = []
    with open(CSV_USERS, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            users.append(row)

    print(f"📋 Loaded {len(users)} users from {CSV_USERS}")
    return users


# ============================================================
# LOGGER
# ============================================================
def log_api(name: str, status: int, response_text: str, start_time: float):
    duration = round(time.time() - start_time, 2)

    print(f"\n{'=' * 45}")
    print(f"  {name} API")
    print(f"{'=' * 45}")
    print(f"  Status     : {status}")
    print(f"  Time Taken : {duration} sec")

    if not response_text or response_text.strip() == "":
        print("  ⚠️  Empty response body.")
        return None, duration

    # Try decrypt first
    try:
        decrypted = decode_data(response_text)
        if decrypted and decrypted.strip():
            parsed = json.loads(decrypted)
            print("  Response:")
            print(json.dumps(parsed, indent=4))
            return parsed, duration
    except Exception:
        pass

    # Try plain JSON
    try:
        parsed = json.loads(response_text)
        print("  Response (plain JSON):")
        print(json.dumps(parsed, indent=4))
        return parsed, duration
    except Exception:
        pass

    print(f"  Raw: {response_text[:300]}")
    return None, duration


def log_request(name: str, endpoint: str, headers: dict,
                payload: dict = None, encrypted_payload: str = None,
                params: dict = None):
    """Print full request details before sending."""
    print(f"\n{'─' * 55}")
    print(f"  📤 {name} REQUEST")
    print(f"{'─' * 55}")
    print(f"  🌐 Endpoint : {endpoint}")

    if params:
        print(f"  🔗 Params   :")
        for k, v in params.items():
            print(f"      {k} : {str(v)[:60]}...")

    print(f"  📋 Headers  :")
    for k, v in headers.items():
        print(f"      {k} : {v}")

    if payload:
        print(f"  📦 Payload (Readable):")
        print(json.dumps(payload, indent=6))

    if encrypted_payload:
        print(f"  🔐 Payload (Encrypted):")
        print(f"      {encrypted_payload[:100]}...")

    print(f"{'─' * 55}")


# ============================================================
# PAYLOAD BUILDERS
# ============================================================
def build_create_payload(user: dict) -> dict:
    """Build create payload from users.csv row."""
    return {
        "first_name"          : user["first_name"],
        "last_name"           : user["last_name"],
        "email"               : user["email"],
        "hierarchy"           : HIERARCHY_ID,
        "postal_country"      : "-1",
        "date_of_joining"     : "2020-01-01",
        "gender"              : "male",
        "notify_email"        : "manasa.r@esthenos.com",
        "postal_address"      : "Indiranagar",
        "postal_state"        : "KA",
        "postal_city"         : "Bengaluru",
        "postal_code"         : "123123",
        "postal_tele_code"    : "+91",
        "postal_telephone"    : user["phone"],
        "verify_two_factor_otp": False
    }


def build_update_payload(data: dict, esaf_emp_id: str) -> dict:
    """Build update payload from CREATE response data."""
    return {
        "first_name"      : data.get("first_name"),
        "last_name"       : data.get("last_name"),
        "email"           : data.get("email"),
        "hierarchy"       : data.get("hierarchy"),
        "postal_country"  : "-1",
        "date_of_joining" : data.get("date_of_joining"),
        "gender"          : data.get("gender"),
        "notify_email"    : data.get("notify_email"),
        "active"          : True,
        "reset_device_id" : False,
        "postal_address"  : data.get("postal_address"),
        "postal_state"    : data.get("postal_state"),
        "postal_city"     : data.get("postal_city"),
        "postal_code"     : data.get("postal_code"),
        "postal_tele_code": "+91",
        "postal_telephone": data.get("postal_telephone"),
        "is_bc_employee"  : False,
        "access_bcs"      : [],
        "credit_officer_id": 0,
        "esaf_employee_id": esaf_emp_id
    }


# ============================================================
# LOGIN
# ============================================================
def perform_login(request) -> tuple:
    print("\n🔐 Logging in...")
    start = time.time()

    encrypted_login = encode_data(json.dumps(LOGIN_PAYLOAD))

    log_request(
        name="LOGIN",
        endpoint=LOGIN_ENDPOINT,
        headers=LOGIN_HEADERS,
        payload=LOGIN_PAYLOAD,
        encrypted_payload=encrypted_login
    )

    login_res = request.post(
        LOGIN_ENDPOINT,
        headers=LOGIN_HEADERS,
        data=encrypted_login
    )

    login_json, _ = log_api(
        "LOGIN", login_res.status, login_res.text(), start
    )

    if not login_json or login_res.status != 200:
        raise RuntimeError(
            f"❌ Login failed. Status: {login_res.status}"
        )

    auth_token = (
        login_json.get("message")
        or login_json.get("token")
        or login_json.get("data", {}).get("token")
    )

    if not auth_token:
        raise RuntimeError("❌ Token not found in login response.")

    print(f"\n✅ Login successful. Token: [{auth_token}]")

    auth_headers = {
        "channel"      : "mobile",
        "device-type"  : "android",
        "app-version"  : "2.0.6-SIT",
        "Content-Type" : "text/plain",
        "instance-token": auth_token
    }

    return auth_headers, auth_token


# ============================================================
# CREATE EMPLOYEE
# ============================================================
def create_employee(request, auth_headers: dict,
                    user: dict, user_index: int) -> tuple:

    payload           = build_create_payload(user)
    encrypted_payload = encode_data(json.dumps(payload))

    log_request(
        name="CREATE EMPLOYEE",
        endpoint=CREATE_ENDPOINT,
        headers=auth_headers,
        payload=payload,
        encrypted_payload=encrypted_payload
    )

    start      = time.time()
    create_res = request.post(
        CREATE_ENDPOINT,
        headers=auth_headers,
        data=encrypted_payload
    )

    create_json, duration = log_api(
        "CREATE", create_res.status, create_res.text(), start
    )

    if create_res.status not in [200, 201] or not create_json:
        print(f"  ❌ CREATE failed. Status: {create_res.status}")
        return None, None, duration

    save_to_csv(CSV_EMAILS, ["email"], [payload["email"]])

    data = (
        create_json.get("data")
        or create_json.get("result")
        or create_json
    )

    if not data:
        print(f"  ❌ No data block in response.")
        return create_json, None, duration

    first_name  = data.get("first_name")
    last_name   = data.get("last_name")
    resp_email  = data.get("email")
    employee_id = data.get("employee_id")
    role        = data.get("role")

    if not all([first_name, last_name, resp_email, employee_id]):
        print(f"  ❌ Missing fields in response: {data}")
        return create_json, None, duration

    full_name = f"{first_name} {last_name}".strip()

    save_to_csv(CSV_EMAILS,
                ["email"], [resp_email])
    save_to_csv(CSV_CREATE_RESPONSE,
                ["response"], [json.dumps(create_json)])
    save_to_csv(CSV_EMPLOYEE_DATA,
                ["name", "email", "employee_id", "role", "esaf_employee_id", "credit_officer_id"],
                [full_name, resp_email, employee_id, role, user["esaf_employee_id"], user["credit_officer_id"]])

    print(f"  ✅ Created: {full_name} | ID: {employee_id}")
    return create_json, data, duration


# ============================================================
# UPDATE EMPLOYEE
# ============================================================
def update_employee(request, auth_headers: dict,
                    data: dict, esaf_emp_id: str,
                    user_index: int) -> tuple:

    employee_id      = data.get("employee_id")
    update_payload   = build_update_payload(data, esaf_emp_id)
    encoded_emp_id   = encode_data(json.dumps(employee_id))
    encrypted_update = encode_data(json.dumps(update_payload))

    log_request(
        name="UPDATE EMPLOYEE",
        endpoint=UPDATE_ENDPOINT,
        headers=auth_headers,
        payload=update_payload,
        encrypted_payload=encrypted_update,
        params={"employee_id": encoded_emp_id}
    )

    start      = time.time()
    update_res = request.put(
        UPDATE_ENDPOINT,
        headers=auth_headers,
        params={"employee_id": encoded_emp_id},
        data=encrypted_update
    )

    update_json, duration = log_api(
        "UPDATE", update_res.status, update_res.text(), start
    )

    if update_res.status == 200 and update_json:
        print(f"  ✅ Updated successfully.")
    else:
        print(f"  ❌ UPDATE failed. Status: {update_res.status}")

    return update_json, duration


# ============================================================
# SUMMARY
# ============================================================
def print_summary(results: list, timings: list, total_time: float):
    success  = sum(1 for r in results if r["status"] == "SUCCESS")
    failed   = sum(1 for r in results if r["status"] == "FAILED")
    skipped  = sum(1 for r in results if r["status"] == "SKIPPED")
    avg_time = round(sum(timings) / len(timings), 2) if timings else 0

    print("\n" + "=" * 55)
    print("📊 EXECUTION SUMMARY")
    print("=" * 55)
    print(f"  Total    : {len(results)}")
    print(f"  ✅ Success : {success}")
    print(f"  ❌ Failed  : {failed}")
    print(f"  ⏭️  Skipped : {skipped}")
    print(f"  ⏱️  Avg Time : {avg_time} sec")
    print(f"  ⏱️  Total    : {total_time} sec")
    print("=" * 55)

    save_to_csv(
        CSV_SUMMARY,
        ["total", "success", "failed", "skipped",
         "avg_time_sec", "total_time_sec"],
        [len(results), success, failed, skipped, avg_time, total_time]
    )

    print(f"\n  {'#':<5} {'Name':<20} {'Employee ID':<15} {'Status'}")
    print(f"  {'-' * 55}")
    for r in results:
        print(
            f"  {r['index']:<5} "
            f"{r['name']:<20} "
            f"{str(r.get('employee_id','N/A')):<15} "
            f"{r['status']}"
        )

    print(f"\n📁 Results saved to: {RESULTS_DIR}")


# ============================================================
# MAIN
# ============================================================
def test_flow():
    from playwright.sync_api import sync_playwright

    print("\n🚀 Starting Employee Creation")
    print(f"📁 Data  : {DATA_DIR}")
    print(f"📁 Results: {RESULTS_DIR}\n")

    total_start = time.time()
    clean_csv_files()

    # Load users from CSV
    users   = load_users_from_csv()
    timings = []
    results = []

    with sync_playwright() as p:
        request = p.request.new_context()

        # LOGIN
        try:
            auth_headers, auth_token = perform_login(request)
        except RuntimeError as e:
            print(str(e))
            return

        # LOOP THROUGH USERS FROM CSV
        for i, user in enumerate(users, 1):
            print(f"\n{'=' * 55}")
            print(f"🚀 Processing User {i}/{len(users)}: "
                  f"{user['first_name']} {user['last_name']}")
            print(f"{'=' * 55}")

            user_result = {
                "index"      : i,
                "name"       : f"{user['first_name']} {user['last_name']}",
                "employee_id": "N/A",
                "status"     : "SKIPPED"
            }

            # CREATE
            create_json, data, create_dur = create_employee(
                request, auth_headers, user, i
            )
            timings.append(create_dur)

            if not data:
                results.append(user_result)
                continue

            user_result["employee_id"] = data.get("employee_id", "N/A")

            # UPDATE (use esaf_employee_id from CSV)
            update_json, update_dur = update_employee(
                request, auth_headers,
                data, user["esaf_employee_id"], i
            )
            timings.append(update_dur)

            user_result["status"] = "SUCCESS" if update_json else "FAILED"
            results.append(user_result)

            # Time estimate
            if timings:
                avg       = sum(timings) / len(timings)
                remaining = (len(users) - i) * avg * 2
                print(f"\n  ⏳ Avg Time      : {round(avg, 2)} sec")
                print(f"  ⏳ Est. Remaining: {round(remaining, 2)} sec")

        request.dispose()

    total_time = round(time.time() - total_start, 2)
    print_summary(results, timings, total_time)


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    test_flow()
PYEOF