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
# Lage-QR: same pattern as _qr_signer but separate salt so tokens can't be cross-used
_lage_qr_signer = URLSafeSerializer(settings.SECRET_KEY, salt="lage-qr-token")


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
    return "ec_" + secrets.token_urlsafe(32)


def generate_sms_gateway_token() -> str:
    return "smsgw_" + secrets.token_urlsafe(32)


def generate_weather_station_token() -> str:
    """Push-Token für eine lokale Wetterstation (Meteobridge → Cloud)."""
    return "wxst_" + secrets.token_urlsafe(32)


def generate_weather_dashboard_token() -> str:
    """Öffentlicher Infoscreen-Token für das Wetter-Dashboard einer Org."""
    return "wxdb_" + secrets.token_urlsafe(32)


def generate_gateway_token() -> str:
    """Langlebiges Device-Token für den Print & Alarm Gateway-Container (nach Pairing)."""
    return "ecpg_" + secrets.token_urlsafe(32)


def generate_pairing_code() -> str:
    """Kurzlebiger Einmal-Code fürs Gateway-Pairing (gut abtippbar)."""
    # 8 Zeichen, keine leicht verwechselbaren (0/O, 1/I/l)
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


# ── ECPG: kurzlebige signierte Artifact-URLs (PDF-Download durchs Gateway) ─────
# Das Gateway lädt fertige PDFs über eine signierte, kurzlebige URL – kein
# generischer API-Zugriff auf Einsatzdaten.
_artifact_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="ecpg-artifact")
ARTIFACT_TOKEN_MAX_AGE = 300  # 5 min


def sign_artifact_token(job_id: int, org_id: int) -> str:
    return _artifact_signer.dumps({"j": job_id, "o": org_id})


def unsign_artifact_token(token: str) -> tuple[int, int] | None:
    """Returns (job_id, org_id) or None (ungültig/abgelaufen/manipuliert)."""
    try:
        data = _artifact_signer.loads(token, max_age=ARTIFACT_TOKEN_MAX_AGE)
        return (data["j"], data["o"])
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None


def sign_session(
    user_id: int,
    *,
    qr: bool = False,
    device: bool = False,
    remember: bool = False,
    incident_id: int | None = None,
    lage_id: int | None = None,
    display_name: str | None = None,
) -> str:
    if qr:
        payload: dict | int = {"u": user_id, "qr": 1}
        if incident_id is not None:
            payload["i"] = incident_id  # type: ignore[index]
        if lage_id is not None:
            payload["l"] = lage_id  # type: ignore[index]
        if display_name:
            payload["n"] = display_name  # type: ignore[index]
    elif device:
        payload = {"u": user_id, "d": 1}
    elif remember:
        # "Login merken": längeres, gleitendes Fenster (siehe unsign_session).
        payload = {"u": user_id, "r": 1}
    else:
        payload = user_id
    return _signer.dumps(payload)


def unsign_session(
    token: str,
) -> tuple[int, bool, int | None, bool, str | None, int | None, bool] | None:
    """Returns (user_id, is_qr, qr_incident_id, is_device, display_name, qr_lage_id, is_remember) or None."""
    try:
        # Load without max_age so we can check expiry manually per session type.
        data, ts = _signer.loads(token, max_age=None, return_timestamp=True)
        now = datetime.now(UTC)
        age_s = (now - ts.replace(tzinfo=UTC)).total_seconds()

        if isinstance(data, dict) and data.get("d"):
            # Device session: no timeout whatsoever.
            return (data["u"], False, None, True, None, None, False)

        if isinstance(data, dict) and data.get("qr"):
            # QR session: enforce absolute max_age only (DB controls incident/lage validity).
            if age_s > settings.SESSION_MAX_AGE_SECONDS:
                return None
            return (data["u"], True, data.get("i"), False, data.get("n"), data.get("l"), False)

        if isinstance(data, dict) and data.get("r"):
            # "Login merken": längeres, gleitendes Fenster. Die Middleware re-signt bei
            # jedem Request (Sliding Window), daher age ≈ Zeit seit letzter Aktivität.
            if age_s > settings.SESSION_REMEMBER_MAX_AGE_SECONDS:
                return None
            if age_s > settings.SESSION_REMEMBER_INACTIVITY_SECONDS:
                return None
            return (data["u"], False, None, False, None, None, True)

        if isinstance(data, int):
            # Regular user session: enforce absolute max_age AND inactivity window.
            # The middleware re-signs on each request (sliding window), so age ≈ time
            # since last activity.
            if age_s > settings.SESSION_MAX_AGE_SECONDS:
                return None
            if age_s > settings.SESSION_INACTIVITY_SECONDS:
                return None
            return (data, False, None, False, None, None, False)

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


def sign_lage_qr_token(lage_id: int, user_id: int) -> str:
    return _lage_qr_signer.dumps({"l": lage_id, "u": user_id})


def unsign_lage_qr_token(token: str) -> dict | None:
    try:
        return _lage_qr_signer.loads(token)
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
