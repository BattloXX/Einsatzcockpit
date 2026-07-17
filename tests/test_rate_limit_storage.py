"""Rate-Limit-Storage-Auswahl (Audit A2): Redis bei REDIS_URL, sonst In-Memory.

Wichtig bei -w 2+: Nur mit Redis zählen alle Worker gegen denselben Bucket;
In-Memory hieße effektiv Limit × Workerzahl.
"""
from app.config import settings
from app.core.rate_limit import _build_limiter


def _storage_name(limiter) -> str:
    return type(limiter._storage).__name__


def test_ohne_redis_url_in_memory(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    limiter = _build_limiter()
    assert limiter is not None
    assert _storage_name(limiter) == "MemoryStorage"


def test_mit_redis_url_redis_storage_und_fallback(monkeypatch):
    # Lazy connect: die Storage wird ohne laufenden Redis-Server erzeugt.
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:6379/0")
    limiter = _build_limiter()
    assert limiter is not None
    assert _storage_name(limiter) == "RedisStorage"
    # In-Memory-Fallback muss aktiv sein: faellt Redis zur Laufzeit aus,
    # antwortet die App weiter (fail-open je Worker) statt mit 500ern.
    assert limiter._in_memory_fallback_enabled is True


def test_kaputte_storage_uri_faellt_auf_memory_zurueck(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "quatsch://nicht-existent")
    limiter = _build_limiter()
    assert limiter is not None
    assert _storage_name(limiter) == "MemoryStorage"
