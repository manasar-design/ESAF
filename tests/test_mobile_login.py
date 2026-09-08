from playwright.sync_api import sync_playwright
from main import encode_data, decode_data
import json
import time


def test_login():

    print("\n🚀 Starting Login \n")

    start_time = time.time()

    # -----------------------------
    # LOGIN PAYLOAD (PLAIN)
    # -----------------------------
    payload = {
        "email": "ajayfo@gravity-sit.esafbank.com",
        "password": "Esaf@123",
        "verify_two_factor_otp": True,
        "otp": "123456"
    }

    print("\n========== REQUEST BODY (PLAIN) ==========")
    print(json.dumps(payload, indent=4))

    # -----------------------------
    # ENCRYPT PAYLOAD
    # -----------------------------
    encrypted_payload = encode_data(json.dumps(payload))

    print("\n========== REQUEST BODY (ENCRYPTED) ==========")
    print(encrypted_payload)

    with sync_playwright() as p:

        request = p.request.new_context()

        # -----------------------------
        # LOGIN API CALL
        # -----------------------------
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

        duration = round(time.time() - start_time, 2)

        print("\n========== RESPONSE STATUS ==========")
        print("Status Code:", response.status)
        print("Time Taken:", duration, "sec")

        raw_response = response.text()

        print("\n========== RAW RESPONSE ==========")
        print(raw_response)

        # -----------------------------
        # DECRYPT RESPONSE
        # -----------------------------
        print("\n========== DECRYPTED RESPONSE ==========")

        try:
            decrypted = decode_data(raw_response)

            print("\n🔓 Decrypted Raw:")
            print(decrypted)

            try:
                parsed = json.loads(decrypted)
                print("\n📦 JSON Response:")
                print(json.dumps(parsed, indent=4))

                # OPTIONAL: Extract token
                token = (
                    parsed.get("message")
                    or parsed.get("token")
                    or parsed.get("data", {}).get("token")
                )

                print("\n🔑 Token:", token)

            except:
                print("⚠️ Decrypted data is not JSON")

        except Exception as e:
            print("❌ Decryption failed:", str(e))


# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    test_login()