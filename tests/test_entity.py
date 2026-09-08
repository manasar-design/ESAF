# create_and_approve_bc.py
"""
Single script to:
1. Login with 1 employee user
2. Create 1 BC (Business Correspondent)
3. Login as checker
4. Get pending BC approvals
5. Approve the created BC
"""

import csv
import json
import os
import time
import random
import string
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright
from main import encode_data, decode_data

# ============================================================
# CONFIGURATION
# ============================================================
BASE_URL = "https://esaf-dev-api.esthenos.com/web/api/v1"

LOGIN_ENDPOINT = f"{BASE_URL}/organisation/user_login"
GET_STATES_ENDPOINT = f"{BASE_URL}/organisation/get_state_location"
GET_DISTRICTS_ENDPOINT = f"{BASE_URL}/organisation/district"
CREATE_BC_ENDPOINT = f"{BASE_URL}/organisation/bc"  # ← Create BC
GET_PENDING_BC_ENDPOINT = f"{BASE_URL}/organisation/bc/pending_approval"  # ← Get pending BCs
APPROVE_BC_ENDPOINT = f"{BASE_URL}/organisation/bc/{{code}}/approval_action"  # ← Approve BC

SOURCE_CSV = "employee_data.csv"
RESULT_CSV = "bc_creation_approval_result.csv"

# Employee user credentials
EMPLOYEE_PASSWORD = "Esaf@123"

# Checker/Admin credentials
CHECKER_EMAIL = "ajay@esaf-dev.esthenos.com"
CHECKER_PASSWORD = "Esaf@123"
OTP = "123456"

LOGIN_HEADERS = {
    "channel"      : "mobile",
    "device-type"  : "android",
    "app-version"  : "2.0.6-SIT",
    "Content-Type" : "text/plain"
}


# ============================================================
# CSV HELPERS
# ============================================================
def read_first_user_from_csv(file_path: str) -> dict:
    """Read only the FIRST user from employee_data.csv"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"❌ CSV file not found: {file_path}")
    
    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("email"):
                return {
                    "name": row.get("name", ""),
                    "email": row.get("email", ""),
                    "employee_id": row.get("employee_id", ""),
                    "role": row.get("role", "")
                }
    return None


def save_result(employee_email: str, bc_name: str, bc_code: str,
                creation_status: str, approval_status: str, 
                creation_error: str = "", approval_error: str = ""):
    """Save the result to CSV"""
    write_header = not os.path.exists(RESULT_CSV)
    with open(RESULT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "employee_email", "bc_name", "bc_code",
                "creation_status", "approval_status",
                "creation_error", "approval_error"
            ])
        writer.writerow([
            employee_email, bc_name, bc_code,
            creation_status, approval_status,
            creation_error, approval_error
        ])


# ============================================================
# API HELPERS
# ============================================================
def log_api_call(name: str, method: str, endpoint: str, payload: dict = None):
    """Print API call details"""
    print(f"\n  {'─' * 60}")
    print(f"  📤 {name}")
    print(f"  {'─' * 60}")
    print(f"     Method   : {method}")
    print(f"     Endpoint : {endpoint}")
    if payload:
        print(f"     Payload  : {json.dumps(payload, indent=16)[:200]}...")
    print(f"  {'─' * 60}")


def log_api_response(name: str, status: int, response_text: str):
    """Print API response details"""
    print(f"\n  📥 {name} Response")
    print(f"     Status : {status}")
    
    if not response_text or response_text.strip() == "":
        print("     Body   : Empty")
        return None
    
    # Try decrypt
    try:
        decrypted = decode_data(response_text)
        if decrypted:
            parsed = json.loads(decrypted)
            print(f"     Body   : {json.dumps(parsed, indent=16)[:300]}...")
            return parsed
    except:
        pass
    
    # Try plain JSON
    try:
        parsed = json.loads(response_text)
        print(f"     Body   : {json.dumps(parsed, indent=16)[:300]}...")
        return parsed
    except:
        pass
    
    print(f"     Body   : {response_text[:200]}...")
    return None


def get_states(request) -> list:
    """Fetch states from API"""
    print("\n  🌍 Fetching states...", end=" ", flush=True)
    try:
        res = request.get(GET_STATES_ENDPOINT, headers=LOGIN_HEADERS)
        if res.status == 200:
            try:
                decrypted = decode_data(res.text())
                states_data = json.loads(decrypted) if decrypted else {}
            except:
                states_data = json.loads(res.text())
            
            states = states_data.get("data", states_data.get("states", []))
            if isinstance(states, list) and len(states) > 0:
                print(f"✅ Got {len(states)} states")
                return states
    except Exception as e:
        print(f"❌")
    
    # Fallback
    print("⚠️  Using fallback states")
    return [
        {"name": "Karnataka", "code": "KA"},
        {"name": "Maharashtra", "code": "MH"},
        {"name": "Tamil Nadu", "code": "TN"}
    ]


def get_districts(request, state: str = "Karnataka") -> list:
    """Fetch districts from API"""
    print(f"  🗺️  Fetching districts for {state}...", end=" ", flush=True)
    try:
        params = {"state": state}
        res = request.get(GET_DISTRICTS_ENDPOINT, headers=LOGIN_HEADERS, params=params)
        if res.status == 200:
            try:
                decrypted = decode_data(res.text())
                districts_data = json.loads(decrypted) if decrypted else {}
            except:
                districts_data = json.loads(res.text())
            
            districts = districts_data.get("data", districts_data.get("districts", []))
            if isinstance(districts, list) and len(districts) > 0:
                print(f"✅ Got {len(districts)} districts")
                return districts
    except Exception as e:
        print(f"❌")
    
    # Fallback
    print("⚠️  Using fallback districts")
    return [
        {"name": "Bengaluru", "code": "BG"},
        {"name": "Kolar", "code": "KL"},
        {"name": "Mysore", "code": "MY"}
    ]


# ============================================================
# LOGIN
# ============================================================
def login_user(request, email: str, password: str) -> tuple:
    """Login and return (token, success_bool)"""
    payload = {
        "email": email,
        "password": password,
        "verify_two_factor_otp": True,
        "otp": OTP
    }

    encrypted_payload = encode_data(json.dumps(payload))
    
    log_api_call("LOGIN", "POST", LOGIN_ENDPOINT, payload)

    try:
        res = request.post(
            LOGIN_ENDPOINT,
            headers=LOGIN_HEADERS,
            data=encrypted_payload
        )
    except Exception as e:
        print(f"  ❌ Request failed: {e}")
        return "", False

    response_json = log_api_response("LOGIN", res.status, res.text())

    if res.status != 200 or not response_json:
        print(f"  ❌ Login failed")
        return "", False

    token = (
        response_json.get("message")
        or response_json.get("token")
        or response_json.get("data", {}).get("token")
        or ""
    )

    if token:
        print(f"  ✅ Login successful. Token: {token[:50]}...")
        return token, True
    else:
        print(f"  ❌ No token in response")
        return "", False


# ============================================================
# BC CREATION
# ============================================================
def generate_bc_payload(states: list, districts: list) -> dict:
    """Generate BC payload with dynamic name and code"""
    suffix = ''.join(random.choices(string.ascii_uppercase, k=3))
    bc_name = f"BC_{suffix}_{int(time.time())}"
    bc_code = f"BC{random.randint(10000, 99999)}"
    
    state = random.choice(states) if states else {"name": "Karnataka"}
    district = random.choice(districts) if districts else {"name": "Bengaluru"}
    
    state_name = state.get("name") if isinstance(state, dict) else state
    district_name = district.get("name") if isinstance(district, dict) else district

    opened_on = (datetime.now() + timedelta(days=random.randint(1, 30))).isoformat()

    return {
        "name": bc_name,
        "code": bc_code,
        "is_draft": False,
        "address": "Main Street",
        "contact_person_name": "Contact Person",
        "contact_person_number": "9876543210",
        "contact_person_email": "contact@gmail.com",
        "fldg": 0,
        "life_insurance": 0,
        "general_insurance": 0,
        "processing_fee": 0,
        "interest_rate": 0,
        "share_on_insurance": 0,
        "entity_type": "Internal",
        "mode_gl_configs": [
            {
                "mode_of_operation": "MBC",
                "cash_gl_code": "6556",
                "cashless_gl_code": "54654"
            }
        ],
        "opened_on": opened_on,
        "country": "India",
        "state": state_name,
        "district": district_name,
        "pin_code": "465768",
        "latitude": None,
        "longitude": None,
        "office_number": "9876543210",
        "email_id": "bc@gmail.com",
        "group_loan_verification_enabled": False,
        "individual_loan_verification_enabled": False,
        "group_loan_verification": [],
        "individual_loan_verification": [],
        "ptp_mandatory": False,
        "collection_receipt_mandatory": False,
        "foreclosure_receipt_mandatory": False,
        "max_group_loans_per_client": 0,
        "max_individual_loans_per_client": 0,
        "arrear_check_days": 0,
        "etb_cooling_period_days": 0,
        "disbursement_tolerance_days": 0,
        "scheduling_advance_from_days": 0,
        "scheduling_advance_to_days": 0,
        "disable_disbursement": False,
        "disable_sourcing": False,
        "group_loan_indebtedness": 0,
        "individual_loan_indebtedness": 0,
        "collection_window": {"start_time": "", "end_time": ""},
        "disbursement_window": {"start_time": "", "end_time": ""},
        "disbursement_scheduling_window": {"start_time": "", "end_time": ""},
        "foreclosure_window": {"start_time": "", "end_time": ""},
        "approved_exposure_limit": 0,
        "alert_trigger_threshold": 0
    }


def create_bc(request, token: str, states: list, districts: list) -> tuple:
    """Create one BC"""
    payload = generate_bc_payload(states, districts)
    encrypted_payload = encode_data(json.dumps(payload))

    auth_headers = {
        "channel"      : "mobile",
        "device-type"  : "android",
        "app-version"  : "2.0.6-SIT",
        "Content-Type" : "text/plain",
        "instance-token": token
    }

    log_api_call("CREATE BC", "POST", CREATE_BC_ENDPOINT, payload)

    try:
        res = request.post(
            CREATE_BC_ENDPOINT,
            headers=auth_headers,
            data=encrypted_payload
        )
    except Exception as e:
        print(f"  ❌ Request failed: {e}")
        return "", "", "ERROR", str(e)

    response_json = log_api_response("CREATE BC", res.status, res.text())

    if res.status not in [200, 201] or not response_json:
        error_msg = json.dumps(response_json) if response_json else res.text()[:100]
        print(f"  ❌ BC creation failed")
        return "", "", "FAILED", error_msg

    bc_code = (
        response_json.get("data", {}).get("code")
        or response_json.get("code")
        or ""
    )
    
    bc_name = (
        response_json.get("data", {}).get("name")
        or response_json.get("name")
        or payload.get("name")
        or ""
    )

    if bc_code:
        print(f"  ✅ BC created. Code: {bc_code}, Name: {bc_name}")
        return bc_code, bc_name, "SUCCESS", ""
    else:
        print(f"  ❌ No BC code in response")
        return "", "", "FAILED", "BC code not found"


# ============================================================
# BC APPROVAL
# ============================================================
def get_pending_bcs(request, token: str) -> list:
    """Get list of pending BC approvals"""
    auth_headers = {
        "channel"      : "mobile",
        "device-type"  : "android",
        "app-version"  : "2.0.6-SIT",
        "Content-Type" : "text/plain",
        "instance-token": token
    }

    log_api_call("GET PENDING BCs", "GET", GET_PENDING_BC_ENDPOINT)

    try:
        res = request.get(
            GET_PENDING_BC_ENDPOINT,
            headers=auth_headers
        )
    except Exception as e:
        print(f"  ❌ Request failed: {e}")
        return []

    response_json = log_api_response("GET PENDING BCs", res.status, res.text())

    if res.status != 200 or not response_json:
        print(f"  ❌ Failed to fetch pending BCs")
        return []

    # Parse pending BCs (adjust based on actual API response structure)
    pending_bcs = response_json.get("data", response_json.get("bcs", []))
    
    if isinstance(pending_bcs, list):
        print(f"  ✅ Found {len(pending_bcs)} pending BC(s)")
        return pending_bcs
    else:
        print(f"  ⚠️  Unexpected response format")
        return []


def approve_bc(request, bc_code: str, token: str) -> tuple:
    """Approve one BC by code"""
    
    # Build endpoint with code
    endpoint = APPROVE_BC_ENDPOINT.replace("{{code}}", bc_code)
    
    payload = {
        "approved": True,
        "remarks": "Approved by checker"
    }

    encrypted_payload = encode_data(json.dumps(payload))

    auth_headers = {
        "channel"      : "mobile",
        "device-type"  : "android",
        "app-version"  : "2.0.6-SIT",
        "Content-Type" : "text/plain",
        "instance-token": token
    }

    log_api_call("APPROVE BC", "POST", endpoint, payload)

    try:
        res = request.post(
            endpoint,
            headers=auth_headers,
            data=encrypted_payload
        )
    except Exception as e:
        print(f"  ❌ Request failed: {e}")
        return "ERROR", str(e)

    response_json = log_api_response("APPROVE BC", res.status, res.text())

    if res.status not in [200, 201]:
        error_msg = json.dumps(response_json) if response_json else res.text()[:100]
        print(f"  ❌ BC approval failed")
        return "FAILED", error_msg

    if response_json and (response_json.get("status") == "success" or response_json.get("success")):
        print(f"  ✅ BC approved successfully")
        return "APPROVED", ""
    else:
        print(f"  ❌ Approval status unclear")
        return "FAILED", json.dumps(response_json)[:100]


# ============================================================
# MAIN FLOW
# ============================================================
def main():
    print("\n" + "=" * 70)
    print("🚀 CREATE & APPROVE BC (Business Correspondent)")
    print("=" * 70)

    # Read first user
    print(f"\n📄 Reading from: {SOURCE_CSV}")
    user = read_first_user_from_csv(SOURCE_CSV)
    
    if not user:
        print("❌ No users found in CSV")
        return
    
    print(f"✅ Found user: {user['name']} ({user['email']})\n")

    creation_status = "FAILED"
    approval_status = "SKIPPED"
    creation_error = ""
    approval_error = ""
    bc_code = ""
    bc_name = "N/A"

    with sync_playwright() as p:
        request = p.request.new_context()

        print("=" * 70)
        print("STEP 1: GET MASTER DATA")
        print("=" * 70)
        
        states = get_states(request)
        districts = get_districts(request, "Karnataka")

        print("\n" + "=" * 70)
        print("STEP 2: EMPLOYEE LOGIN & CREATE BC")
        print("=" * 70)
        
        employee_token, login_success = login_user(request, user["email"], EMPLOYEE_PASSWORD)
        
        if login_success:
            bc_code, bc_name, creation_status, creation_error = create_bc(
                request, employee_token, states, districts
            )

        print("\n" + "=" * 70)
        print("STEP 3: CHECKER LOGIN & GET PENDING BCs")
        print("=" * 70)
        
        if creation_status == "SUCCESS":
            checker_token, checker_login_success = login_user(
                request, CHECKER_EMAIL, CHECKER_PASSWORD
            )
            
            if checker_login_success:
                pending_bcs = get_pending_bcs(request, checker_token)
                
                if pending_bcs:
                    print("\n" + "=" * 70)
                    print("STEP 4: APPROVE CREATED BC")
                    print("=" * 70)
                    
                    approval_status, approval_error = approve_bc(
                        request, bc_code, checker_token
                    )
                else:
                    print(f"\n  ⚠️  No pending BCs found for approval")
                    approval_status = "NOT_FOUND"
        else:
            print(f"\n  ⏭️  Skipping approval (BC creation failed)")
            approval_status = "SKIPPED"

        request.dispose()

    # Save result
    save_result(
        user["email"],
        bc_name,
        bc_code,
        creation_status,
        approval_status,
        creation_error,
        approval_error
    )

    # Print summary
    print("\n" + "=" * 70)
    print("📊 FINAL SUMMARY")
    print("=" * 70)
    print(f"  Employee         : {user['name']} ({user['email']})")
    print(f"  BC Name          : {bc_name}")
    print(f"  BC Code          : {bc_code if bc_code else 'N/A'}")
    print(f"  Creation Status  : {creation_status}")
    print(f"  Approval Status  : {approval_status}")
    if creation_error:
        print(f"  Creation Error   : {creation_error[:100]}")
    if approval_error:
        print(f"  Approval Error   : {approval_error[:100]}")
    print(f"\n📁 Results saved to: {RESULT_CSV}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()