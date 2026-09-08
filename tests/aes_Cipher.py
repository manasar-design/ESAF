import base64
from Crypto.Cipher import AES


class AESGCMCipher:

    def __init__(self, key):
        self.key = key
        self.nonce = b"123123123123"  # 12 bytes for GCM

    def encrypt(self, data):
        if isinstance(data, str):
            data = data.encode()

        cipher = AES.new(self.key, AES.MODE_GCM, nonce=self.nonce)
        ciphertext, tag = cipher.encrypt_and_digest(data)

        encrypted = base64.b64encode(ciphertext + tag).decode()
        return encrypted

    def decrypt(self, encrypted_data):
        encrypted_bytes = base64.b64decode(encrypted_data)

        ciphertext = encrypted_bytes[:-16]
        tag = encrypted_bytes[-16:]

        cipher = AES.new(self.key, AES.MODE_GCM, nonce=self.nonce)
        decrypted = cipher.decrypt_and_verify(ciphertext, tag)

        return decrypted.decode()