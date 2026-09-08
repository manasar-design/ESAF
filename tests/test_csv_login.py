from playwright.sync_api import sync_playwright
from main import encode_data, decode_data
import json
import time
import csv


CSV_FILE = "emails.csv"
COMMON_PASSWORD = "Esaf@123"


def read_emails(file):
    emails = []
    with open(file, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row["email"].strip()
            emails.append(email)
    return emails


def test_csv_login():

    print("\n🚀 Starting Bulk Login\n")

    emails = read_emails(CSV_FILE)

    with sync_playwright() as p:   # ✅ THIS LINE

        request = p.request.new_context()   # ✅ MUST BE INDENTED

        for index, email in enumerate(emails, start=1):   # ✅ ALSO INDENTED

            print(f"\n👤 User {index}: {email}")

            payload = {
                "email": email,
                "password": COMMON_PASSWORD,
                "verify_two_factor_otp": True,
                "otp": "123456"
            }

            encrypted_payload = encode_data(json.dumps(payload))

            response = request.post(
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
                data=encrypted_payload
            )

            print("Status:", response.status)

            try:
                decrypted = decode_data(response.text())
                print("Response:", decrypted)
            except Exception as e:
                print("❌ Decrypt error:", e)

    print("FINAL PAYLOAD:", payload)


if __name__ == "__main__":
    test_csv_login()