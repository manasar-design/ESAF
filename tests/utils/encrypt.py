import json
import base64
from Crypto.Cipher import AES

SECRET_KEY = "JSgFznpEFHQec+lDTOTgpXDdtorGzuyc"
IV_KEY = "123123123123"


def encrypt_payload(payload):

    key = SECRET_KEY.encode("utf-8")
    iv = IV_KEY.encode("utf-8")

    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)

    encrypted_data, tag = cipher.encrypt_and_digest(
        json.dumps(payload).encode("utf-8")
    )

    # Combine encrypted + tag (same as Node.js)
    final_data = encrypted_data + tag

    return {
        "encryptedData": base64.b64encode(final_data).decode("utf-8")
    }