from playwright.sync_api import sync_playwright
from main import encode_data, decode_data
import json
import csv
import time


CSV_FILE = "emails.csv"
COMMON_PASSWORD = "Esaf@123"


# -----------------------------
# READ EMAILS
# -----------------------------
def read_emails(file):
    emails = []
    with open(file, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            emails.append(row["email"].strip())
    return emails


# -----------------------------
# MAIN FLOW
# -----------------------------
def test_bulk_loan():

    print("\n🚀 Starting Login + API Flow\n")

    emails = read_emails(CSV_FILE)

    with sync_playwright() as p:

        request = p.request.new_context()

        for index, email in enumerate(emails, start=1):

            print(f"\n==============================")
            print(f"👤 User {index}: {email}")
            print(f"==============================")

            # ---------------- LOGIN ----------------
            login_payload = {
                "email": email,
                "password": COMMON_PASSWORD,
                "verify_two_factor_otp": True,
                "otp": "123456"
            }

            encrypted_login = encode_data(json.dumps(login_payload))

            login_res = request.post(
                "https://gravity-sit-api.esafbank.com/api/v1/token/sourcing",
                headers={
                "channel": "mobile",
                "device-type": "android",
                "app-version": "2.0.6-SIT",
                "Content-Type": "text/plain",
                "device-mac-id": "f2166c84024b7977",
                "X-fos-APKVERSION": "1.0.7-DEBUG",
                "X-fos-FB-token": "fdcAHNg8Qy6YioQ1u-1IFX:APA91bHzWhQYE53zA-fXMEB0ydF2U9cNshXiNCftxnKVOvj7m2Nrppgm64BMv1O_8cQkqPnAfCRRGVj0V3avmphdfeosom-Ig84MESqtYIDqDmQmlhGG6wM",
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

                # ---------------- TOKEN EXTRACTION ----------------
                token = (
                    login_json.get("token")
                    or login_json.get("access_token")
                    or login_json.get("data", {}).get("token")
                    or login_json.get("data", {}).get("access_token")
                )

                if not token:
                    print("❌ Token not found, skipping user")
                    continue

                print("🔑 Token:", token)

            except Exception as e:
                print("❌ Login Decrypt Failed:", e)
                continue   # 🔥 VERY IMPORTANT
                print("🔑 Token:", token)

            # ---------------- COMMON HEADERS ----------------
            auth_headers = {
                "instance-token": token,
                "Content-Type": "text/plain",
                "channel": "mobile",
                "device-type": "android",
                "app-version": "2.0.6-SIT",
                "Content-Type": "text/plain",
                "device-mac-id": "f2166c84024b7977",
                "X-fos-APKVERSION": "1.0.7-DEBUG",
                "X-fos-FB-token": "fdcAHNg8Qy6YioQ1u-1IFX:APA91bHzWhQYE53zA-fXMEB0ydF2U9cNshXiNCftxnKVOvj7m2Nrppgm64BMv1O_8cQkqPnAfCRRGVj0V3avmphdfeosom-Ig84MESqtYIDqDmQmlhGG6wM",
                "LATITUDE": "12.9646815",
                "LONGITUDE": "77.6439036"
            }

            # ---------------- GET API 1 ----------------
            print("\n📡 Calling GET API 1")

            res1 = request.get(
                "https://gravity-sit-api.esafbank.com/api/v1/customers",
                headers=auth_headers
            )

            print("Status:", res1.status)

            try:
                dec1 = decode_data(res1.text())
                print("Response API 1:", dec1)
            except:
                print("Raw API 1:", res1.text())

            # ---------------- GET API 2 ----------------
            print("\n📡 Calling GET API 2")

            res2 = request.get(
                "https://gravity-sit-api.esafbank.com/api/v1/mobile/profile",
                headers=auth_headers
            )

            print("Status:", res2.status)

            try:
                dec2 = decode_data(res2.text())
                print("Response API 2:", dec2)
            except:
                print("Raw API 2:", res2.text())

            #------------------- POST API 3 ------------------

            mobile_payload = {
                "category_id": "JLG",
                "category_name": "Joint Liability Group",
                "village_id": "Jayanagar",
                "center_id": "0111500023",
                "mobile": 8771119222,
                "otp": 123456
            }
            encrypted_mobile = encode_data(json.dumps(mobile_payload))

            res3 = request.post(
                "https://gravity-sit-api.esafbank.com/api/v1/customer/mobile/otp",
                headers=auth_headers,
                data=encrypted_mobile
            )
            print("Status:", res3.status)

            try:
                dec3 = decode_data(res3.text())
                print("Response API 3:", dec3)
            except:
                print("Raw API 3:", res3.text())

            #----------------------------- POST API 4 ------------------------

            mobile_ver_payload = {
                "category_id": "JLG",
                "category_name": "Joint Liability Group",
                "village_id": "Jayanagar",
                "center_id": "0111500023",
                "mobile": 8771119222,
                "otp": 123456
            }
            encrypted_mobile_verified = encode_data(json.dumps(mobile_ver_payload))


            # ---------------- OPTIONAL DELAY ----------------
            time.sleep(1)


# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    test_bulk_loan()