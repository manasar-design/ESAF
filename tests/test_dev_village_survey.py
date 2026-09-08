import csv
import requests
import json
from main import encode_data, decode_data   # your existing functions


LOGIN_URL = "https://esaf-dev-api.esthenos.com/api/v1/token/sourcing"
GET_API_URL = "https://esaf-dev-api.esthenos.com/api/v1/villages/?status=village_survey_pending"

OUTPUT_FILE = "village_survey_details.csv"


def login_token(email):
    payload = {
        "email": email,
        "password": "Esaf@123",
        "verify_two_factor_otp": True,
        "otp": "123456"
    }

    encoded_payload = encode_data(json.dumps(payload))

    response = requests.post(
        LOGIN_URL,
        data=encoded_payload,
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
            }
        )

    print(f"\n🔐 Raw Status: {response.status_code}")
    print(f"🔐 Raw Response: {response.text}")

    decoded_response = decode_data(response.text)
    print(f"🔓 Decoded Response: {decoded_response}")

    if response.status_code != 200:
        print(f"❌ Login failed: {email}")
        return None

    decoded_response = decode_data(response.text)
    json_data = json.loads(decoded_response)

    token = json_data.get("token")

    if not token:
        print(f"❌ Login failed properly. Response: {json_data}")
        return None

    print(f"✅ Login token: {token}")

    if not token:
        print(f"❌ Token not found for {email}")
        return None

    print(f"✅ Login success: {response.status_code}")
    return token


def call_get_api(token):
    headers = {
        "instance-token": token,   # 🔥 primary auth
        "channel": "mobile",
        "device-type": "android",
        "app-version": "2.0.6-SIT",
        "Content-Type": "application/json",   # 🔥 changed
        "device-mac-id": "f2166c84024b7977",
        "X-fos-APKVERSION": "1.0.7-DEBUG",
        "X-fos-FB-token": "fdcAHNg8Qy6YioQ1u-1IFX:APA91bHzWhQYE53zA-fXMEB0ydF2U9cNshXiNCftxnKVOvj7m2Nrppgm64BMv1O_8cQkqPnAfCRRGVj0V3avmphdfeosom-Ig84MESqtYIDqDmQmlhGG6wM",
        "LATITUDE": "12.9646815",
        "LONGITUDE": "77.6439036"
    }

    response = requests.get(
        "https://esaf-dev-api.esthenos.com/api/v1/villages",
        headers=headers,
        params={"status": "village_survey_pending"},
    )

    print("STATUS:", response.status_code)
    print("RAW:", response.text)

    return response

    encoded_request = encode_data("")  # if GET requires encoded empty payload

    response = requests.get(
    GET_API_URL,
    data=encoded_request,
    headers=headers
    )
    print(f"\n🔐 Raw Status: {response.status_code}")
    print(f"🔐 Raw Response: {response.text}")

    if response.status_code != 200:
        print("❌ GET API failed")
        return []

    decoded_response = decode_data(response.text)
    return json.loads(decoded_response).get("villages", [])

    decoded_response = decode_data(response.text)
    json_data = json.loads(decoded_response)

    print(f"🔐 Raw Response: {decoded_response}")

    print("\n🔎 FULL DECODED JSON:")
    print(json.dumps(json_data, indent=2))

    villages = json_data.get("villages", [])
    co_name = json_data.get("CO_name")
    branch_name = json_data.get("branch_name")

    enriched_data = []

    for v in villages:
        v["CO_name"] = co_name
        v["branch_name"] = branch_name
        enriched_data.append(v)

    print(f"📊 Extracted {len(enriched_data)} records")

    return enriched_data

    data_section = json_data.get("data", {})

    if isinstance(data_section, list):
        data_list = data_section
    elif isinstance(data_section, dict):
        data_list = json_data.get("villages", [])
    else:
        data_list = []

    print(f"📊 Extracted {len(data_list)} records")

    return data_list


def extract_and_save(all_data):
    with open(OUTPUT_FILE, "w", newline="") as file:
        writer = csv.writer(file)

        # Header
        writer.writerow([
            "CO_name",
            "branch_name",
            "name",
            "village_code",
            "village_id"
        ])

        for item in all_data:
            print("ITEM:", item)
            writer.writerow([
                item.get("CO_name") or item.get("coName"),
                item.get("branch_name") or item.get("branchName"),
                item.get("name") or item.get("villageName"),
                item.get("village_code") or item.get("villageCode"),
                item.get("village_id") or item.get("id")
            ])

    print(f"\n🎯 Data saved to {OUTPUT_FILE}")


def test_dev_village_survey():
    all_records = []

    with open("employee_data_dev.csv", "r") as file:
        reader = csv.DictReader(file)

        for row in reader:
            email = row["email"]

            print(f"\n🚀 Processing: {email}")

            token = login_token(email)
            if not token:
                continue

            data = call_get_api(token)

            if data:
                all_records.extend(data)

    extract_and_save(all_records)


if __name__ == "__main__":
    test_dev_village_survey()