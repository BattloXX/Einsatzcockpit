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

from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer, URLSafeTimedSerializer

from app.config import settings

_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="session")
# Deterministic (no timestamp) so the same incident+user always produces the same QR token.
# Validity is controlled via the DB (revoked_at / incident.status), not by expiry time.
_qr_signer = URLSafeSerializer(settings.SECRET_KEY, salt="qr-token")


def hash_password(plain: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    import bcrypt
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


def generate_sms_gateway_token() -> str:
    return "smsgw_" + secrets.token_urlsafe(32)


def sign_session(
    user_id: int,
    *,
    qr: bool = False,
    device: bool = False,
    incident_id: int | None = None,
    display_name: str | None = None,
) -> str:
    if qr:
        payload: dict | int = {"u": user_id, "qr": 1}
        if incident_id is not None:
            payload["i"] = incident_id  # type: ignore[index]
        if display_name:
            payload["n"] = display_name  # type: ignore[index]
    elif device:
        payload = {"u": user_id, "d": 1}
    else:
        payload = user_id
    return _signer.dumps(payload)


def unsign_session(token: str) -> tuple[int, bool, int | None, bool, str | None] | None:
    """Returns (user_id, is_qr, qr_incident_id, is_device, display_name) or None."""
    try:
        # Load without max_age so we can check expiry manually per session type.
        data, ts = _signer.loads(token, max_age=None, return_timestamp=True)
        now = datetime.now(UTC)
        age_s = (now - ts.replace(tzinfo=UTC)).total_seconds()

        if isinstance(data, dict) and data.get("d"):
            # Device session: no timeout whatsoever.
            return (data["u"], False, None, True, None)

        if isinstance(data, dict) and data.get("qr"):
            # QR session: enforce absolute max_age only (DB controls incident validity).
            if age_s > settings.SESSION_MAX_AGE_SECONDS:
                return None
            return (data["u"], True, data.get("i"), False, data.get("n"))

        if isinstance(data, int):
            # Regular user session: enforce absolute max_age AND inactivity window.
            # The middleware re-signs on each request (sliding window), so age ≈ time
            # since last activity.
            if age_s > settings.SESSION_MAX_AGE_SECONDS:
                return None
            if age_s > settings.SESSION_INACTIVITY_SECONDS:
                return None
            return (data, False, None, False, None)

        return None
    except (BadSignature, SignatureExpired):
        return None


def get_author_name(request) -> str | None:
    """Returns the name to record as author in log/journal entries.

    QR sessions: the name entered on the /qr-name page (from session payload).
    Regular and device sessions: the user's display_name.
    """
    name = getattr(request.state, "display_name", None)
    if name:
        return name
    user = getattr(request.state, "user", None)
    if user:
        return getattr(user, "display_name", None)
    return None


def sign_qr_token(incident_id: int, user_id: int) -> str:
    return _qr_signer.dumps({"incident_id": incident_id, "user_id": user_id})


def unsign_qr_token(token: str) -> dict | None:
    try:
        return _qr_signer.loads(token)
    except BadSignature:
        return None


# ── Incident-PIN-Zugangstoken (Gäste ohne Account) ────────────────────────────

# PIN-Zugangstokens laufen nach 24 h ab.
_pin_access_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="incident-pin-access")
PIN_ACCESS_MAX_AGE = 86400  # 24 h


def hash_pin(plain: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_pin(plain: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def sign_pin_access_token(incident_id: int) -> str:
    return _pin_access_signer.dumps({"incident_id": incident_id})


def unsign_pin_access_token(token: str) -> int | None:
    try:
        data = _pin_access_signer.loads(token, max_age=PIN_ACCESS_MAX_AGE)
        return data.get("incident_id")
    except (BadSignature, SignatureExpired):
        return None
