import csv
import json
import requests

from main import encode_data, decode_data   # import your functions

LOGIN_URL = "https://gravity-sit-api.esafbank.com/api/v1/token/sourcing"   # change this

INPUT_CSV = "emails.csv"
OUTPUT_CSV = "tokens.csv"


def test_token():
    results = []

    with open(INPUT_CSV, mode="r") as file:
        reader = csv.DictReader(file)

        for i, row in enumerate(reader, start=1):
            username = row.get("email")
            password = "Esaf@123"

            print(f"\n🚀 Processing User {i}: {username}")

            try:
                # 🔹 Step 1: Prepare payload
                payload = {
                    "email": username,
                    "password": password,
                    "verify_two_factor_otp": False,
                    "otp": "123456"
                }

                payload_json = json.dumps(payload)

                # 🔹 Step 2: Encode request
                encoded_payload = encode_data(payload_json)

                request_body = {
                    "request": encoded_payload
                }

                # 🔹 Step 3: Call API
                response = requests.post(LOGIN_URL, json=request_body)

                print(f"Status Code: {response.status_code}")

                if response.status_code != 200:
                    print(f"❌ Login failed for {username}")
                    continue

                response_json = response.json()

                # 🔹 Step 4: Decode response
                encoded_response = response_json.get("response")

                if not encoded_response:
                    print("❌ No response field found")
                    continue

                decoded_response = decode_data(encoded_response)

                print("✅ Decoded Response:", decoded_response)

                decoded_json = json.loads(decoded_response)

                # 🔹 Step 5: Extract token
                token = decoded_json.get("token")

                if token:
                    print(f"🎯 Token received for {username}")
                    results.append({
                        "email": username,
                        "token": token
                    })
                else:
                    print(f"❌ Token not found for {username}")

            except Exception as e:
                print(f"❌ Error for {username}: {str(e)}")

    # 🔹 Step 6: Save tokens
    with open(OUTPUT_CSV, mode="w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["email", "token"])
        writer.writeheader()
        writer.writerows(results)

    print("\n🎯 Tokens stored in tokens.csv")


if __name__ == "__main__":
    test_token()