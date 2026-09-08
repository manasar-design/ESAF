# login_all_users.py
"""
Login to all 100 users, store tokens, and fetch multiple dashboard data for each user.
Includes detailed timing and decoded response logging.
"""

import csv
import json
import os
import time
from datetime import datetime

from playwright.sync_api import sync_playwright
from main import encode_data, decode_data

BASE_URL       = "https://esaf-dev-api.esthenos.com/web/api/v1"
LOGIN_ENDPOINT = f"{BASE_URL}/organisation/user_login"

# Dashboard Endpoints (in order of execution)
USER_ACCESS_ENDPOINT = f"{BASE_URL}/organisation/get_user_access_feature"
DASHBOARD_STATS_ENDPOINT = f"{BASE_URL}/organisation/dashboard?stats_key=statistics"
DASHBOARD_DETAILED_ENDPOINT_V1 = f"{BASE_URL}/organisation/dashboard?start_date=2026-08-13&end_date=2026-08-13&stats_key=event_dashboard,income,average_running_loans,credit_score,gender,education"
DASHBOARD_DETAILED_ENDPOINT_V2 = f"{BASE_URL}/organisation/dashboard?start_date=2026-08-13&end_date=2026-08-13&stats_key=credit_type_stats,application_status_stats,failed_applications,bc_details"

SOURCE_CSV = "employee_data.csv"
TOKENS_CSV = "user_tokens.csv"
DASHBOARD_RESULTS_CSV = "dashboard_results.csv"
DASHBOARD_JSON = f"dashboard_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
LOGIN_RESPONSES_JSON = f"login_responses_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
DASHBOARD_RESPONSES_JSON = f"dashboard_responses_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
TIMING_LOG = f"timing_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

COMMON_PASSWORD = "Esaf@123"
OTP = "123456"

# Updated Headers
LOGIN_HEADERS = {
    "channel"      : "mobile",
    "device-type"  : "android",
    "app-version"  : "2.0.6-SIT",
    "Content-Type" : "text/plain"
}

DASHBOARD_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "app-version": "2.0.6-SIT"
}


def read_emails_from_csv(file_path: str) -> list:
    """Read email addresses from CSV file."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"❌ CSV file not found: {file_path}")
    emails = []
    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row.get("email")
            if email and email.strip():
                emails.append(email.strip())
    return emails


def save_token(email: str, token: str, status: str = "SUCCESS", error: str = ""):
    """Append token to CSV."""
    write_header = not os.path.exists(TOKENS_CSV)
    with open(TOKENS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["email", "token", "status", "error"])
        writer.writerow([email, token, status, error])


def save_dashboard_result(email: str, api_name: str, status: str, response_data: str = "", error: str = ""):
    """Append dashboard result to CSV with API name."""
    write_header = not os.path.exists(DASHBOARD_RESULTS_CSV)
    with open(DASHBOARD_RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["email", "api_name", "status", "response_data", "error"])
        writer.writerow([email, api_name, status, response_data, error])


def log_timing(email: str, operation: str, duration: float, status: str):
    """Log timing information to CSV."""
    write_header = not os.path.exists(TIMING_LOG)
    with open(TIMING_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp", "email", "operation", "duration_seconds", "status"])
        writer.writerow([datetime.now().isoformat(), email, operation, f"{duration:.3f}", status])


def login_one_user(request, email: str, all_login_responses: list) -> dict:
    """Login single user, return token with detailed logging."""
    payload = {
        "email": email,
        "password": COMMON_PASSWORD,
        "verify_two_factor_otp": True,
        "otp": OTP
    }

    encrypted_payload = encode_data(json.dumps(payload))

    print(f"  🔐 Logging in {email}...", end=" ", flush=True)
    start = time.time()

    try:
        res = request.post(LOGIN_ENDPOINT, headers=LOGIN_HEADERS, data=encrypted_payload)
    except Exception as e:
        duration = time.time() - start
        print(f"❌ ERROR ({duration:.2f}s)")
        log_timing(email, "login", duration, "ERROR")
        return {"email": email, "token": "", "status": "ERROR", "error": str(e)}

    duration = time.time() - start
    
    # Store raw response
    raw_response = res.text()
    
    # Try to decrypt response
    decoded_response = None
    try:
        decoded_response = decode_data(raw_response)
    except Exception as e:
        decoded_response = f"Decryption failed: {str(e)}"

    # Log the full response
    response_log = {
        "email": email,
        "timestamp": datetime.now().isoformat(),
        "status_code": res.status,
        "duration_seconds": round(duration, 3),
        "raw_response": raw_response[:1000],  
        "decoded_response": decoded_response[:1000] if decoded_response else None,
        "headers": dict(res.headers)
    }
    all_login_responses.append(response_log)

    if res.status != 200:
        error_msg = decoded_response if decoded_response else raw_response
        print(f"❌ HTTP {res.status} ({duration:.2f}s)")
        log_timing(email, "login", duration, f"HTTP_{res.status}")
        return {"email": email, "token": "", "status": f"HTTP_{res.status}", "error": error_msg[:200]}

    # Parse response
    try:
        if decoded_response and decoded_response != f"Decryption failed: {str(e)}":
            response_json = json.loads(decoded_response)
        else:
            response_json = json.loads(raw_response)
    except Exception as e:
        print(f"❌ PARSE_ERROR ({duration:.2f}s)")
        log_timing(email, "login", duration, "PARSE_ERROR")
        return {"email": email, "token": "", "status": "PARSE_ERROR", "error": "Could not parse response"}

    token = (
        response_json.get("message")
        or response_json.get("token")
        or response_json.get("data", {}).get("token")
        or ""
    )

    if token:
        print(f"✅ ({duration:.2f}s)")
        log_timing(email, "login", duration, "SUCCESS")
        return {"email": email, "token": token, "status": "SUCCESS", "error": ""}
    else:
        print(f"❌ No token ({duration:.2f}s)")
        log_timing(email, "login", duration, "NO_TOKEN")
        return {"email": email, "token": "", "status": "FAILED", "error": "Token not in response"}


def fetch_api_data(request, email: str, token: str, endpoint: str, api_name: str, all_dashboard_responses: list) -> dict:
    """Generic function to fetch data from any API endpoint."""
    # Clone headers and add token
    api_headers = DASHBOARD_HEADERS.copy()
    api_headers["instance-token"] = token

    print(f"  📊 {api_name} for {email}...", end=" ", flush=True)
    start = time.time()

    try:
        res = request.get(endpoint, headers=api_headers)
    except Exception as e:
        duration = time.time() - start
        print(f"❌ ERROR ({duration:.2f}s)")
        log_timing(email, api_name, duration, "ERROR")
        return {"email": email, "api_name": api_name, "status": "ERROR", "data": None, "error": str(e)}

    duration = time.time() - start
    
    # Store raw response
    raw_response = res.text()
    
    # Try to decrypt response
    decoded_response = None
    try:
        decoded_response = decode_data(raw_response)
    except Exception as e:
        decoded_response = None

    # Log the full response
    response_log = {
        "email": email,
        "api_name": api_name,
        "endpoint": endpoint,
        "timestamp": datetime.now().isoformat(),
        "status_code": res.status,
        "duration_seconds": round(duration, 3),
        "raw_response": raw_response[:1000],
        "decoded_response": decoded_response[:1000] if decoded_response else None,
        "headers": dict(res.headers)
    }
    all_dashboard_responses.append(response_log)

    if res.status != 200:
        error_msg = decoded_response if decoded_response else raw_response
        print(f"❌ HTTP {res.status} ({duration:.2f}s)")
        log_timing(email, api_name, duration, f"HTTP_{res.status}")
        return {"email": email, "api_name": api_name, "status": f"HTTP_{res.status}", "data": None, "error": error_msg[:200]}

    # Parse response
    try:
        if decoded_response:
            data = json.loads(decoded_response)
        else:
            data = json.loads(raw_response)
    except Exception as e:
        print(f"❌ PARSE_ERROR ({duration:.2f}s)")
        log_timing(email, api_name, duration, "PARSE_ERROR")
        return {"email": email, "api_name": api_name, "status": "PARSE_ERROR", "data": None, "error": "Could not parse response"}

    print(f"✅ ({duration:.2f}s)")
    log_timing(email, api_name, duration, "SUCCESS")
    return {"email": email, "api_name": api_name, "status": "SUCCESS", "data": data, "error": ""}


def process_single_user(request, email: str, all_login_responses: list, all_dashboard_responses: list):
    """Process a single user: login + 4 dashboard APIs in specific order."""
    # Step 1: Login
    login_result = login_one_user(request, email, all_login_responses)
    
    result = {
        "email": email,
        "login": login_result,
        "user_access": None,
        "dashboard_stats": None,
        "dashboard_detailed_v1": None,
        "dashboard_detailed_v2": None
    }
    
    if login_result["status"] == "SUCCESS":
        token = login_result["token"]
        
        # Step 2: Call APIs in the specified order
        
        # 2a. User Access Feature (FIRST)
        result["user_access"] = fetch_api_data(
            request, email, token, USER_ACCESS_ENDPOINT, 
            "user_access_feature", all_dashboard_responses
        )
        
        # 2b. Dashboard Statistics (SECOND)
        result["dashboard_stats"] = fetch_api_data(
            request, email, token, DASHBOARD_STATS_ENDPOINT, 
            "dashboard_statistics", all_dashboard_responses
        )
        
        # 2c. Dashboard Detailed V1 (THIRD)
        result["dashboard_detailed_v1"] = fetch_api_data(
            request, email, token, DASHBOARD_DETAILED_ENDPOINT_V1, 
            "dashboard_detailed_v1", all_dashboard_responses
        )
        
        # 2d. Dashboard Detailed V2 (FOURTH)
        result["dashboard_detailed_v2"] = fetch_api_data(
            request, email, token, DASHBOARD_DETAILED_ENDPOINT_V2, 
            "dashboard_detailed_v2", all_dashboard_responses
        )
    
    return result


def main():
    print("\n🚀 Starting: Login & Multiple Dashboard Fetch")
    print(f"📄 Reading from       : {SOURCE_CSV}")
    print(f"📄 Tokens saved to    : {TOKENS_CSV}")
    print(f"📄 Dashboard CSV      : {DASHBOARD_RESULTS_CSV}")
    print(f"📄 Dashboard JSON     : {DASHBOARD_JSON}")
    print(f"📄 Login Responses    : {LOGIN_RESPONSES_JSON}")
    print(f"📄 Dashboard Responses: {DASHBOARD_RESPONSES_JSON}")
    print(f"📄 Timing Log         : {TIMING_LOG}\n")

    emails = read_emails_from_csv(SOURCE_CSV)
    print(f"✅ Found {len(emails)} email(s)\n")

    if not emails:
        print("⚠️  No emails found. Exiting.")
        return

    login_success = 0
    login_failed = 0
    api_success_counts = {
        "user_access_feature": 0, 
        "dashboard_statistics": 0, 
        "dashboard_detailed_v1": 0, 
        "dashboard_detailed_v2": 0
    }
    api_failed_counts = {
        "user_access_feature": 0, 
        "dashboard_statistics": 0, 
        "dashboard_detailed_v1": 0, 
        "dashboard_detailed_v2": 0
    }

    all_dashboard_data = []
    all_login_responses = []
    all_dashboard_responses = []

    overall_start = time.time()

    print(f"🔄 Processing {len(emails)} users sequentially...\n")
    print("API Call Order:")
    print("  1. User Access Feature")
    print("  2. Dashboard Statistics")
    print("  3. Dashboard Detailed V1 (event_dashboard, income, loans, credit, gender, education)")
    print("  4. Dashboard Detailed V2 (credit_type, application_status, failed_apps, bc_details)\n")

    with sync_playwright() as p:
        request = p.request.new_context()

        for idx, email in enumerate(emails, 1):
            result = process_single_user(request, email, all_login_responses, all_dashboard_responses)
            
            # Save login result
            login_result = result["login"]
            save_token(login_result["email"], login_result["token"], login_result["status"], login_result["error"])
            
            if login_result["status"] == "SUCCESS":
                login_success += 1
                
                # Process all 4 API results in order
                for api_key, api_name in [
                    ("user_access", "user_access_feature"),
                    ("dashboard_stats", "dashboard_statistics"),
                    ("dashboard_detailed_v1", "dashboard_detailed_v1"),
                    ("dashboard_detailed_v2", "dashboard_detailed_v2")
                ]:
                    api_result = result.get(api_key)
                    if api_result:
                        if api_result["status"] == "SUCCESS":
                            api_success_counts[api_name] += 1
                            save_dashboard_result(
                                email,
                                api_name,
                                "SUCCESS",
                                json.dumps(api_result["data"]),
                                ""
                            )
                        else:
                            api_failed_counts[api_name] += 1
                            save_dashboard_result(
                                email,
                                api_name,
                                api_result["status"],
                                "",
                                api_result["error"]
                            )
                        
                        # Add to main data collection
                        all_dashboard_data.append({
                            "email": email,
                            "api": api_name,
                            "data": api_result.get("data")
                        })
            else:
                login_failed += 1
                # Mark all APIs as failed due to login failure
                for api_name in api_success_counts.keys():
                    api_failed_counts[api_name] += 1
                    save_dashboard_result(
                        email,
                        api_name,
                        "LOGIN_FAILED",
                        "",
                        login_result["error"]
                    )
            
            # Progress update
            if idx % 10 == 0 or idx == len(emails):
                print(f"\n  📈 Progress: {idx}/{len(emails)} completed")

        request.dispose()

    overall_duration = time.time() - overall_start

    # Save all dashboard data to JSON
    with open(DASHBOARD_JSON, "w", encoding="utf-8") as f:
        json.dump(all_dashboard_data, f, indent=2, ensure_ascii=False)

    # Save all login responses
    with open(LOGIN_RESPONSES_JSON, "w", encoding="utf-8") as f:
        json.dump(all_login_responses, f, indent=2, ensure_ascii=False)

    # Save all dashboard responses
    with open(DASHBOARD_RESPONSES_JSON, "w", encoding="utf-8") as f:
        json.dump(all_dashboard_responses, f, indent=2, ensure_ascii=False)

    # Calculate average times
    def calc_avg_time(responses, status_code=200):
        successful = [r for r in responses if r.get("status_code") == status_code]
        if successful:
            return sum(r["duration_seconds"] for r in successful) / len(successful)
        return 0

    avg_login_time = calc_avg_time(all_login_responses)
    
    # Group dashboard responses by API name for timing stats
    api_times = {}
    for resp in all_dashboard_responses:
        api_name = resp.get("api_name", "unknown")
        if api_name not in api_times:
            api_times[api_name] = []
        api_times[api_name].append(resp["duration_seconds"])

    # Print Summary
    print("\n" + "=" * 90)
    print("📊 EXECUTION SUMMARY")
    print("=" * 90)
    print(f"  Total Users              : {len(emails)}")
    print(f"  Login Success            : {login_success} ✅")
    print(f"  Login Failed             : {login_failed} ❌")
    print(f"\n  API CALL RESULTS:")
    print(f"  1. User Access Feature   : {api_success_counts['user_access_feature']} ✅ | {api_failed_counts['user_access_feature']} ❌")
    print(f"  2. Dashboard Statistics  : {api_success_counts['dashboard_statistics']} ✅ | {api_failed_counts['dashboard_statistics']} ❌")
    print(f"  3. Dashboard Detailed V1 : {api_success_counts['dashboard_detailed_v1']} ✅ | {api_failed_counts['dashboard_detailed_v1']} ❌")
    print(f"  4. Dashboard Detailed V2 : {api_success_counts['dashboard_detailed_v2']} ✅ | {api_failed_counts['dashboard_detailed_v2']} ❌")
    print(f"\n⏱️  TIMING STATISTICS")
    print(f"  Total Execution Time     : {overall_duration:.2f}s")
    print(f"  Avg Login Time           : {avg_login_time:.3f}s")
    if api_times:
        print(f"  API Average Times:")
        for api_name, times in api_times.items():
            if times:
                avg_time = sum(times) / len(times)
                print(f"    - {api_name}: {avg_time:.3f}s")
    print(f"  Total Requests           : {len(emails) * 5}")  # 5 = login + 4 APIs
    print(f"  Requests per Second      : {(len(emails) * 5) / overall_duration:.2f}")
    print(f"\n📁 FILES GENERATED:")
    print(f"  - {TOKENS_CSV}")
    print(f"  - {DASHBOARD_RESULTS_CSV}")
    print(f"  - {DASHBOARD_JSON}")
    print(f"  - {LOGIN_RESPONSES_JSON}")
    print(f"  - {DASHBOARD_RESPONSES_JSON}")
    print(f"  - {TIMING_LOG}")
    print("=" * 90)


if __name__ == "__main__":
    main()