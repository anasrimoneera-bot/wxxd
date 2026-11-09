import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, it, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(it))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def create_token(user_id: int, tenant_id: int) -> str:
    s = get_settings()
    payload = {
        "sub": str(user_id),
        "tid": tenant_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=s.jwt_expire_hours),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])


def _aes() -> AESGCM:
    key = base64.b64decode(get_settings().secret_key_b64)
    if len(key) != 32:
        raise ValueError("SECRET_KEY_B64 must decode to 32 bytes")
    return AESGCM(key)


def encrypt_secret(plain: str) -> str:
    nonce = os.urandom(12)
    return base64.b64encode(nonce + _aes().encrypt(nonce, plain.encode(), None)).decode()


def decrypt_secret(token: str) -> str:
    raw = base64.b64decode(token)
    return _aes().decrypt(raw[:12], raw[12:], None).decode()
