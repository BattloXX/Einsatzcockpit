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
from datetime import UTC, datetime

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer, URLSafeTimedSerializer

from app.config import settings

_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="session")
# Deterministic (no timestamp) so the same incident+user always produces the same QR token.
# Validity is controlled via the DB (revoked_at / incident.status), not by expiry time.
_qr_signer = URLSafeSerializer(settings.SECRET_KEY, salt="qr-token")


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


def sign_session(user_id: int, *, qr: bool = False, incident_id: int | None = None) -> str:
    if qr:
        payload: dict | int = {"u": user_id, "qr": 1}
        if incident_id is not None:
            payload["i"] = incident_id  # type: ignore[index]
    else:
        payload = user_id
    return _signer.dumps(payload)


def unsign_session(token: str) -> tuple[int, bool, int | None] | None:
    """Returns (user_id, is_qr, qr_incident_id) or None."""
    try:
        data, ts = _signer.loads(
            token,
            max_age=settings.SESSION_MAX_AGE_SECONDS,
            return_timestamp=True,
        )
        if isinstance(data, int):
            # Enforce inactivity timeout for regular (non-QR) sessions.
            # The middleware refreshes the token on each request, so age == time
            # since last activity.
            age_s = (datetime.now(UTC) - ts.replace(tzinfo=UTC)).total_seconds()
            if age_s > settings.SESSION_INACTIVITY_SECONDS:
                return None
            return (data, False, None)
        return (data["u"], bool(data.get("qr")), data.get("i"))
    except (BadSignature, SignatureExpired):
        return None


def sign_qr_token(incident_id: int, user_id: int) -> str:
    return _qr_signer.dumps({"incident_id": incident_id, "user_id": user_id})


def unsign_qr_token(token: str) -> dict | None:
    try:
        return _qr_signer.loads(token)
    except BadSignature:
        return None
