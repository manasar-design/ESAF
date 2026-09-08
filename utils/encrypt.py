import json
import base64
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


# Replace with your actual keys
SECRET_KEY = b'JSgFznpEFHQec+lDTOTgpXDdtorGzuyc'   # 16 / 24 / 32 bytes
IV = b'123123123123'               # 12 bytes for GCM


def encrypt_payload(payload: dict):

    try:
        data = json.dumps(payload).encode()

        cipher = AES.new(SECRET_KEY, AES.MODE_GCM, nonce=IV)

        ciphertext, tag = cipher.encrypt_and_digest(data)

        encrypted_data = base64.b64encode(ciphertext).decode()
        tag_data = base64.b64encode(tag).decode()

        return {
            "encryptedData": encrypted_data,
            "tag": tag_data
        }

    except Exception as e:
        print("Encryption Failed:", str(e))
        raise e