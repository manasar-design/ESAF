import json
import base64
from Crypto.Cipher import AES


# Same keys as encrypt.py
SECRET_KEY = b'JSgFznpEFHQec+lDTOTgpXDdtorGzuyc'
IV = b'123123123123'


def decrypt_payload(encrypted_text: str):

    try:
        # Decode base64
        encrypted_bytes = base64.b64decode(encrypted_text)

        # Split ciphertext and tag (last 16 bytes = tag)
        ciphertext = encrypted_bytes[:-16]
        tag = encrypted_bytes[-16:]

        cipher = AES.new(SECRET_KEY, AES.MODE_GCM, nonce=IV)

        decrypted = cipher.decrypt_and_verify(ciphertext, tag)

        return json.loads(decrypted.decode())

    except Exception as e:
        print("Decryption Failed:", str(e))
        raise e