# employee_creation.py
from playwright.sync_api import sync_playwright
from main import encode_data, decode_data
import random
import string
import time
import json
import csv
import os
from post_update_logger import log_update_to_csv

# ============================================================
# CONFIGURATION
# ============================================================
# BASE_URL = "https://gravity-sit-api.esaf.com/web/api/v1"
BASE_URL = "https://esaf-dev-api.esthenos.com/web/api/v1"

LOGIN_ENDPOINT  = f"{BASE_URL}/organisation/user_login"
CREATE_ENDPOINT = f"{BASE_URL}/organisation/employees"
UPDATE_ENDPOINT = f"{BASE_URL}/organisation/employee"

TOTAL_USERS = 10  # Change this to control how many rows are pulled from CSV

LOGIN_PAYLOAD = {
    "email": "Manasa@esaf-dev.esthenos.com",
    "password": "Esaf@123",
    "verify_two_factor_otp": True,
    "otp": "123456"
}

LOGIN_HEADERS = {
    "channel": "mobile",
    "device-type": "android",
    "app-version": "2.0.6-SIT",
    "Content-Type": "text/plain"
}

HIERARCHY_ID = "657af9f94eef19efa4e1f1ba"
EMAIL_DOMAIN  = "esaf-dev.esthenos.com"

CSV_EMAILS           = "emails.csv"
CSV_EMPLOYEE_IDS     = "employee_ids.csv"
CSV_CREATE_RESPONSE  = "create_response.csv"
CSV_EMPLOYEE_DATA    = "employee_data.csv"
CSV_SUMMARY          = "execution_summary.csv"
CSV_USERS_DETAILS    = "100_FO_USERS.csv"   # ← source of user data


# ============================================================
# CSV HELPERS
# ============================================================
def save_to_csv(file: str, header: list, row: list):
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
            print(f"🗑️  Removed old file: {file}")


def _normalize_key(key: str) -> str:
    """Normalize CSV header names: 'First Name' -> 'first_name'."""
    return (key or "").strip().lower().replace(" ", "_")


def load_user_details(csv_file: str) -> list:
    """
    Reads the master user-details CSV and returns a list of dicts
    with normalized keys, e.g.:
    {"salutation": "Mr", "first_name": "Raj", "last_name": "Kumar", "username": "raj.kumar"}

    Auto-detects delimiter (comma, pipe, semicolon, tab) so it works
    whether the file is comma-separated or pipe-separated etc.
    """
    if not os.path.exists(csv_file):
        raise FileNotFoundError(f"❌ User details file not found: {csv_file}")

    with open(csv_file, newline="", encoding="utf-8-sig") as f:
        sample = f.read(2048)
        f.seek(0)

        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",|;\t")
        except csv.Error:
            # Fallback: default to comma if sniffing fails
            dialect = csv.excel

        reader = csv.DictReader(f, dialect=dialect)

        users = []
        for raw_row in reader:
            row = {_normalize_key(k): (v or "").strip() for k, v in raw_row.items()}
            users.append(row)

    if not users:
        raise ValueError(f"❌ No rows found in {csv_file}")

    print(f"📂 Loaded {len(users)} user record(s) from {csv_file} "
          f"(delimiter detected: '{dialect.delimiter}')")
    return users


# ============================================================
# REQUEST LOGGER
# ============================================================
def log_request(
    name: str,
    method: str,
    endpoint: str,
    headers: dict,
    payload: dict | None = None,
    encrypted_payload: str | None = None,
    params: dict | None = None
):
    """
    Print full request details before every API call.
    """
    print(f"\n{'─' * 50}")
    print(f"  📤 REQUEST  →  {name}")
    print(f"{'─' * 50}")
    print(f"  Method   : {method}")
    print(f"  Endpoint : {endpoint}")

    if params:
        print(f"\n  🔗 Query Params:")
        print(json.dumps(params, indent=4))

    print(f"\n  📋 Headers:")
    print(json.dumps(headers, indent=4))

    if payload is not None:
        print(f"\n  📦 Payload (unencrypted / raw JSON):")
        print(json.dumps(payload, indent=4))

    if encrypted_payload is not None:
        preview = (
            encrypted_payload[:80] + "..."
            if len(encrypted_payload) > 80
            else encrypted_payload
        )
        print(f"\n  🔒 Encrypted Payload ({len(encrypted_payload)} chars):")
        print(f"  {preview}")

    print(f"{'─' * 50}")


# ============================================================
# RESPONSE LOGGER (UPDATED TO DECRYPT 500/ERROR RESPONSES)
# ============================================================
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

    # 1. Always try decryption first (even for 500 errors, since backend responses are encrypted)
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
    except Exception as e:
        pass

    # 2. Try Plain JSON (gateway errors)
    try:
        parsed = json.loads(response_text)
        print("  Response (plain JSON):")
        print(json.dumps(parsed, indent=4))
        return parsed, duration
    except Exception:
        pass

    # 3. Raw fallback
    print(f"  Raw Response : {response_text[:500]}")
    return None, duration


# ============================================================
# PAYLOAD GENERATORS
# ============================================================
def generate_customer_payload(user_row: dict) -> dict:
    """
    Builds the CREATE payload using data pulled from 100_FO_USERS.csv.
    Adds a unique timestamp suffix to emails/phones to prevent 500 duplicate conflict errors.
    """
    first_name = user_row.get("first_name", "").strip()
    last_name  = user_row.get("last_name", "").strip()
    salutation = user_row.get("salutation", "").strip()

    username = (
        user_row.get("username")
        or user_row.get("user_name")
        or first_name
    ).strip().lower().replace(" ", ".")

    if not first_name or not last_name:
        raise ValueError(f"❌ Incomplete user row in CSV: {user_row}")

    # Unique suffix prevents 500 Internal Server Errors due to existing duplicate email/phone numbers
    unique_suffix = int(time.time())
    email = f"{username}@{EMAIL_DOMAIN}"
    phone = "8765" + str(random.randint(100000, 999999))

    payload = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "hierarchy": HIERARCHY_ID,
        "postal_country": "-1",
        "date_of_joining": "2020-01-01",
        "gender": "male",
        "notify_email": "manasa.r@esthenos.com",
        "postal_address": "Indiranagar",
        "postal_state": "KA",
        "postal_city": "Bengaluru",
        "postal_code": "562114",
        "postal_tele_code": "+91",
        "postal_telephone": phone,
        "verify_two_factor_otp": False
    }

    if salutation:
        payload["salutation"] = salutation

    return payload


def generate_employee_id() -> str:
    return str(random.randint(100, 999))


def build_update_payload(data: dict) -> dict:
    payload = {
        "first_name": data.get("first_name"),
        "last_name": data.get("last_name"),
        "email": data.get("email"),
        "hierarchy": data.get("hierarchy"),
        "postal_country": "-1",
        "date_of_joining": data.get("date_of_joining"),
        "gender": data.get("gender"),
        "notify_email": data.get("notify_email"),
        "active": True,
        "reset_device_id": False,
        "postal_address": data.get("postal_address"),
        "postal_state": data.get("postal_state"),
        "postal_city": data.get("postal_city"),
        "postal_code": data.get("postal_code"),
        "postal_tele_code": "+91",
        "postal_telephone": data.get("postal_telephone"),
        "is_bc_employee": False,
        "access_bcs": [],
        "credit_officer_id": 0,
        "esaf_employee_id": generate_employee_id()
    }

    if data.get("salutation"):
        payload["salutation"] = data.get("salutation")

    return payload


# ============================================================
# LOGIN
# ============================================================
def perform_login(request) -> tuple:
    print("\n🔐 Logging in...")
    start = time.time()

    encrypted_login = encode_data(json.dumps(LOGIN_PAYLOAD))

    log_request(
        name="LOGIN",
        method="POST",
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

    login_json, _ = log_api("LOGIN", login_res.status, login_res.text(), start)

    if not login_json:
        raise RuntimeError("❌ Login failed — could not parse response.")

    if login_res.status != 200:
        raise RuntimeError(
            f"❌ Login returned status {login_res.status}: {login_json}"
        )

    auth_token = (
        login_json.get("message")
        or login_json.get("token")
        or login_json.get("data", {}).get("token")
    )

    if not auth_token or not isinstance(auth_token, str) or len(auth_token) < 5:
        raise RuntimeError(f"❌ Invalid token: '{auth_token}'")

    print(f"\n✅ Login successful.")
    print(f"   Token : [{auth_token}]")

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
def create_employee(request, auth_headers: dict, user_index: int, user_row: dict) -> tuple:
    payload = generate_customer_payload(user_row)

    print(f"\n  📧 Email     : {payload['email']}")
    print(f"  📱 Phone     : {payload['postal_telephone']}")
    print(f"  👤 Name      : {payload.get('salutation', '')} "
          f"{payload['first_name']} {payload['last_name']}")
    print(f"  🏢 Hierarchy : {payload['hierarchy']}")

    encrypted_payload = encode_data(json.dumps(payload))

    log_request(
        name="CREATE EMPLOYEE",
        method="POST",
        endpoint=CREATE_ENDPOINT,
        headers=auth_headers,
        payload=payload,
        encrypted_payload=encrypted_payload
    )

    start = time.time()
    create_res = request.post(
        CREATE_ENDPOINT,
        headers=auth_headers,
        data=encrypted_payload
    )

    create_json, duration = log_api(
        "CREATE", create_res.status, create_res.text(), start
    )

    if create_res.status not in [200, 201] or not create_json:
        print(f"  ❌ CREATE failed for user {user_index}. "
              f"Status: {create_res.status}")
        return None, None, duration

    save_to_csv(CSV_EMAILS, ["email"], [payload["email"]])

    data = (
        create_json.get("data")
        or create_json.get("result")
        or create_json
    )

    if not data or not isinstance(data, dict):
        print(f"  ❌ No valid data block in response: {create_json}")
        return create_json, None, duration

    first_name  = data.get("first_name")
    last_name   = data.get("last_name")
    resp_email  = data.get("email")
    employee_id = data.get("employee_id")
    role        = data.get("role")

    if not all([first_name, last_name, resp_email, employee_id]):
        print(f"  ❌ Missing required fields in response data: {data}")
        return create_json, None, duration

    full_name = f"{first_name} {last_name}".strip()

    save_to_csv(CSV_EMPLOYEE_IDS, ["employee_id"], [employee_id])
    save_to_csv(CSV_CREATE_RESPONSE, ["response"], [json.dumps(create_json)])
    save_to_csv(
        CSV_EMPLOYEE_DATA,
        ["name", "email", "employee_id", "role"],
        [full_name, resp_email, employee_id, role]
    )

    print(f"  ✅ Employee created: {full_name} | ID: {employee_id}")
    return create_json, data, duration


# ============================================================
# UPDATE EMPLOYEE
# ============================================================
def update_employee(
    request, auth_headers: dict, data: dict, user_index: int
) -> tuple:
    employee_id = data.get("employee_id")

    if not employee_id:
        print(f"  ❌ No employee_id. Cannot update user {user_index}.")
        return None, 0.0

    update_payload   = build_update_payload(data)
    encoded_emp_id   = encode_data(json.dumps(employee_id))
    encrypted_update = encode_data(json.dumps(update_payload))

    print(f"\n  🔄 Updating employee : {employee_id}")
    print(f"  🆔 ESAF Employee ID  : {update_payload['esaf_employee_id']}")

    log_request(
        name="UPDATE EMPLOYEE",
        method="PUT",
        endpoint=UPDATE_ENDPOINT,
        headers=auth_headers,
        payload=update_payload,
        encrypted_payload=encrypted_update,
        params={"employee_id": encoded_emp_id}
    )

    start = time.time()
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
        print(f"  ✅ Employee updated successfully.")
        log_update_to_csv(data, update_payload, update_json)
    else:
        print(f"  ❌ UPDATE failed. Status: {update_res.status}")

    return update_json, duration


# ============================================================
# SUMMARY
# ============================================================
def print_summary(results: list, timings: list, total_time: float, total_users: int):
    success  = sum(1 for r in results if r["status"] == "SUCCESS")
    failed   = sum(1 for r in results if r["status"] == "FAILED")
    skipped  = sum(1 for r in results if r["status"] == "SKIPPED")
    avg_time = round(sum(timings) / len(timings), 2) if timings else 0

    print("\n" + "=" * 50)
    print("📊 EXECUTION SUMMARY")
    print("=" * 50)
    print(f"  Total Users  : {total_users}")
    print(f"  ✅ Success   : {success}")
    print(f"  ❌ Failed    : {failed}")
    print(f"  ⏭️  Skipped   : {skipped}")
    print(f"  ⏱️  Avg Time  : {avg_time} sec")
    print(f"  ⏱️  Total     : {total_time} sec")
    print("=" * 50)

    save_to_csv(
        CSV_SUMMARY,
        ["total", "success", "failed", "skipped", "avg_time_sec", "total_time_sec"],
        [total_users, success, failed, skipped, avg_time, total_time]
    )

    print("\n📋 Per-User Results:")
    print(f"  {'#':<5} {'Name':<25} {'Employee ID':<15} {'Status':<10}")
    print(f"  {'-' * 60}")
    for r in results:
        print(
            f"  {r['index']:<5} "
            f"{r['name']:<25} "
            f"{str(r.get('employee_id', 'N/A')):<15} "
            f"{r['status']:<10}"
        )


# ============================================================
# MAIN FLOW
# ============================================================
def test_flow():
    print("\n🚀 Starting Employee Creation Automation")

    total_start = time.time()
    clean_csv_files()

    all_users = load_user_details(CSV_USERS_DETAILS)

    effective_total = TOTAL_USERS
    if TOTAL_USERS > len(all_users):
        print(f"⚠️  TOTAL_USERS ({TOTAL_USERS}) exceeds available rows "
              f"({len(all_users)}). Clamping to {len(all_users)}.")
        effective_total = len(all_users)

    print(f"👥 Total Users to Create: {effective_total}\n")

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

        for i in range(1, effective_total + 1):
            print(f"\n{'=' * 50}")
            print(f"🚀 Processing User {i}/{effective_total}")
            print(f"{'=' * 50}")

            user_row = all_users[i - 1]

            user_result = {
                "index": i,
                "name": "N/A",
                "employee_id": "N/A",
                "status": "SKIPPED"
            }

            # CREATE
            try:
                create_json, data, create_dur = create_employee(
                    request, auth_headers, i, user_row
                )
            except ValueError as e:
                print(str(e))
                results.append(user_result)
                continue

            timings.append(create_dur)

            if not data:
                results.append(user_result)
                continue

            user_result["name"] = (
                f"{data.get('first_name', '')} "
                f"{data.get('last_name', '')}".strip()
            )
            user_result["employee_id"] = data.get("employee_id", "N/A")

            # UPDATE
            update_json, update_dur = update_employee(
                request, auth_headers, data, i
            )
            timings.append(update_dur)

            user_result["status"] = "SUCCESS" if update_json else "FAILED"
            results.append(user_result)

            if timings:
                avg = sum(timings) / len(timings)
                remaining = (effective_total - i) * avg * 2
                print(f"\n  ⏳ Avg API Time   : {round(avg, 2)} sec")
                print(f"  ⏳ Est. Remaining : {round(remaining, 2)} sec")

        request.dispose()

    total_time = round(time.time() - total_start, 2)
    print_summary(results, timings, total_time, effective_total)


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    test_flow()