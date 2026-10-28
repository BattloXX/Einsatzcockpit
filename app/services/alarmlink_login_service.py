"""Einsatzweite PIN-Fehlversuche im gemeinsamen Rate-Limit-Speicher."""
from limits.storage import MemoryStorage

from app.core.rate_limit import limiter

_fallback = MemoryStorage()
SPERRHINWEIS = "Zu viele falsche PIN-Eingaben für diesen Einsatz. Bitte in 15 Minuten erneut versuchen."


def _storage():
    return limiter.limiter.storage if limiter is not None else _fallback


def _key(incident_id: int) -> str:
    return f"alarmlink:pin-failures:{incident_id}"


def gesperrt(incident_id: int) -> bool:
    return _storage().get(_key(incident_id) + ":locked") > 0


def fehlversuch(incident_id: int) -> bool:
    # Eigenes Sperrfenster: Auch ein spaeter 20. Fehler sperrt volle 15 Minuten.
    storage = _storage()
    if storage.incr(_key(incident_id), 900) >= 20:
        storage.incr(_key(incident_id) + ":locked", 900)
        return True
    return False
