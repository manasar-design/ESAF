from playwright.sync_api import sync_playwright
from main import encode_data, decode_data
import random
import string
import time
import json
import csv
import os

# -----------------------------
# HELPERS
# -----------------------------
def generate_village_name():
    return "Village_" + ''.join(random.choices(string.ascii_letters, k=6))


def generate_village_code():
    return ''.join(random.choices('abcdef' + string.digits, k=24))


def read_employees(csv_file="employee_data.csv"):
    employees = []
    with open(csv_file, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("employee_id") and row.get("name"):
                employees.append(row)
    return employees


# -----------------------------
# CSV HELPERS
# -----------------------------
def save_to_csv(file, header, row):
    write_header = not os.path.exists(file)

    with open(file, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(header)
        writer.writerow(row)


# -----------------------------
# LOGGER
# -----------------------------
def log_api(name, status, response_text, start_time):
    duration = round(time.time() - start_time, 2)

    print(f"\n===== {name} API =====")
    print("Status:", status)
    print("Time Taken:", duration, "sec")

    try:
        decrypted = decode_data(response_text)
        parsed = json.loads(decrypted)
        print(json.dumps(parsed, indent=2))
        return parsed, duration
    except:
        print("Raw Response:", response_text)
        return None, duration


# -----------------------------
# MAIN TEST
# -----------------------------
def test_flow():

    print("\n🚀 Starting Automation\n")
    total_start = time.time()
    timings = []

    employees = read_employees()
    employee = random.choice(employees)

    login_payload = {
        "email": "ajaybm@gravity-sit.esafbank.com",
        "password": "Esaf@123",
        "verify_two_factor_otp": True,
        "otp": "123456"
    }

    encrypted_login = encode_data(json.dumps(login_payload))

    with sync_playwright() as p:
        request = p.request.new_context()

        # ---------------- LOGIN ----------------
        start = time.time()

        login_res = request.post(
            "https://gravity-sit-api.esafbank.com/api/v1/token/sourcing",
            headers={
                "channel": "mobile",
                "device-type": "android",
                "app-version": "2.0.6-SIT",
                "Content-Type": "text/plain",
                "device-mac-id": "f2166c84024b7977",
                "X-fos-APKVERSION": "1.0.7-DEBUG",
                "X-fos-FB-token": "token",
                "LATITUDE": "12.9646815",
                "LONGITUDE": "77.6439036"
            },
            data=encrypted_login
        )

        print("\n🔐 Login Status:", login_res.status)

        try:
            decrypted = decode_data(login_res.text())
            login_json = json.loads(decrypted)

            print("✅ Login Response:")
            print(json.dumps(login_json, indent=2))

            token = (
                login_json.get("token")
                or login_json.get("access_token")
                or login_json.get("data", {}).get("token")
                or login_json.get("data", {}).get("access_token")
            )

            if not token:
                raise Exception("Token not found")

            print("🔑 Token:", token)

        except Exception as e:
            print("❌ Login Failed:", e)
            return   # ❗ stop execution safely

        # ---------------- COMMON HEADERS ----------------
        auth_headers = {
            "instance-token": token,
            "Content-Type": "text/plain",
            "channel": "mobile",
            "device-type": "android",
            "app-version": "2.0.6-SIT",
            "device-mac-id": "f2166c84024b7977",
            "X-fos-APKVERSION": "1.0.7-DEBUG",
            "X-fos-FB-token": "token",
            "LATITUDE": "12.9646815",
            "LONGITUDE": "77.6439036"
        }

        total_users = 1

        # ---------------- LOOP ----------------
        for i in range(1, total_users + 1):

            print(f"\n🚀 Processing User {i}/{total_users}")

            payload = {
                "emp_code": "MRAUT",
                "sub-district_id": "69847b308b7f0a60a99c5201",
                "sub-district_name": "EGL",
                "surveyor_id": employee["employee_id"],
                "surveyor_name": employee["name"],
                "village_code": generate_village_code(),
                "village_name": generate_village_name()
            }

            encrypted_payload = encode_data(json.dumps(payload))

            start = time.time()

            res1 = request.post(
                "https://gravity-sit-api.esafbank.com/api/v1/create-village",
                headers=auth_headers,
                data=encrypted_payload   # ✅ fixed variable
            )

            parsed, duration = log_api(
                "CREATE VILLAGE",
                res1.status,
                res1.text(),
                start
            )

            timings.append(duration)

            # ---------------- TIME ESTIMATION ----------------
            avg_time = sum(timings) / len(timings)
            remaining = (total_users - i) * avg_time

            print(f"\n⏳ Avg API Time: {round(avg_time,2)} sec")
            print(f"⏳ Estimated Remaining Time: {round(remaining,2)} sec")

    total_time = round(time.time() - total_start, 2)

    print("\n=================================")
    print("✅ TOTAL EXECUTION TIME:", total_time, "sec")
    print("=================================")


# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    test_flow()