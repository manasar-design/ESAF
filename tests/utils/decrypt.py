import json
import base64
from Crypto.Cipher import AES

SECRET_KEY = "JSgFznpEFHQec+lDTOTgpXDdtorGzuyc"
IV_KEY = "123123123123"


def decrypt_payload(encrypted_base64):

    key = SECRET_KEY.encode("utf-8")
    iv = IV_KEY.encode("utf-8")

    data = base64.b64decode(encrypted_base64)

    # Split encrypted text and tag
    encrypted_text = data[:-16]
    tag = data[-16:]

    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)

    decrypted_data = cipher.decrypt_and_verify(encrypted_text, tag)

    return json.loads(decrypted_data.decode("utf-8"))