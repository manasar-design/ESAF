import requests

LOGIN_URL = "https://gravity-sit-api.esafbank.com/api/v1/token/sourcing"

LOGIN_PAYLOAD = {
    "email": "ajayfo@gravity-sit.esafbank.com",
    "password": "Esaf@123",
    "verify_two_factor_otp": true,
    "otp": "123456"
}

_token = None  # global cache


def extract_token(data):
    """
    Try multiple possible locations for token
    """

    # Standard cases
    token = (
        data.get("token") or
        data.get("data", {}).get("token") or
        data.get("message")   # fallback (your case)
    )

    # Extra safety: ensure it's actually a token-like string
    if isinstance(token, str) and len(token) > 10:
        return token

    return None


def get_token():
    global _token

    if _token is None:
        print("🔐 Fetching new token...")

        response = requests.post(LOGIN_URL, json=LOGIN_PAYLOAD)
        data = response.json()

        _token = extract_token(data)

        if not _token:
            raise Exception(f"❌ Token not found in response: {data}")

    return _token