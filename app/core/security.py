"""Auth-/Crypto-Helfer.

Passwort-Hashing: bcrypt (12 Runden).
API-Key-Hashing: SHA256 — bewusst gewählt, weil:
  - API-Keys sind 32-Byte-Zufallswerte (~256 Bit Entropie), Wörterbuchangriffe
    auf den Hash sind nicht praktikabel.
  - Indexierter Hash-Lookup pro Request bleibt schnell. Argon2/bcrypt würde
    pro Request 100-300 ms Latenz pro Schlüssel hinzufügen.
  - Vergleich erfolgt per `hmac.compare_digest` (timing-sicher).
"""
import hashlib
import hmac
import secrets

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="session")
_qr_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="qr-token")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def verify_api_key(plain: str, stored: str) -> bool:
    return hmac.compare_digest(stored or "", hash_api_key(plain))


def generate_api_key() -> str:
    return "fwwo_" + secrets.token_urlsafe(32)


def sign_session(user_id: int) -> str:
    return _signer.dumps(user_id)


def unsign_session(token: str) -> int | None:
    try:
        return _signer.loads(token, max_age=settings.SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def sign_qr_token(incident_id: int, user_id: int) -> str:
    return _qr_signer.dumps({"incident_id": incident_id, "user_id": user_id})


def unsign_qr_token(token: str) -> dict | None:
    try:
        return _qr_signer.loads(token)
    except (BadSignature, SignatureExpired):
        return None
