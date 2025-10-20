# app/crypto_utils.py

from cryptography.fernet import Fernet
import os
import base64
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

def get_encryption_key():
    """Генерирует или загружает ключ шифрования."""
    key_file = os.path.join(os.path.dirname(__file__), '..', 'config', 'secret.key')
    if os.path.exists(key_file):
        with open(key_file, 'rb') as f:
            key = f.read()
    else:
        # Генерируем новый ключ
        key = Fernet.generate_key()
        # Сохраняем его
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, 'wb') as f:
            f.write(key)
        print(f"⚠️  Новый ключ шифрования сохранён в {key_file}")
    return key

def encrypt_password(password: str) -> str:
    """Шифрует пароль."""
    key = get_encryption_key()
    f = Fernet(key)
    encrypted = f.encrypt(password.encode())
    return base64.urlsafe_b64encode(encrypted).decode()

def decrypt_password(encrypted_password: str) -> str:
    """Расшифровывает пароль."""
    key = get_encryption_key()
    f = Fernet(key)
    decrypted = f.decrypt(base64.urlsafe_b64decode(encrypted_password.encode()))
    return decrypted.decode()