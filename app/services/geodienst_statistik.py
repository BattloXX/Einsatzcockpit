"""Geodienst-Zaehler in Redis mit zeitlich begrenztem lokalem Ausfallbestand."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from app.config import settings
from app.services import shared_cache

logger = logging.getLogger(__name__)
_lokal: dict[str, tuple[dict, float]] = {}
_sperre = threading.RLock()
# Ein Lua-Aufruf verhindert verlorene Updates zwischen Workern, auch beim Hostindex.
_AKTUALISIEREN = """
local tag = cjson.decode(redis.call('get', KEYS[1]) or '{}')
tag[ARGV[1]] = (tag[ARGV[1]] or 0) + 1
redis.call('setex', KEYS[1], ARGV[2], cjson.encode(tag))
if #KEYS > 1 then
    local host = cjson.decode(redis.call('get', KEYS[2]) or '{}')
    local neu = cjson.decode(ARGV[3])
    for k,v in pairs(neu) do host[k] = v end
    redis.call('setex', KEYS[2], ARGV[2], cjson.encode(host))
    local hosts = cjson.decode(redis.call('get', KEYS[3]) or '{}')
    for k,v in pairs(hosts) do
        if v <= tonumber(ARGV[4]) - tonumber(ARGV[2]) then hosts[k] = nil end
    end
    hosts[KEYS[2]] = tonumber(ARGV[4])
    redis.call('setex', KEYS[3], ARGV[2], cjson.encode(hosts))
end
"""


def hostname(wert: str) -> str:
    """Nur Hostnamen uebernehmen, auch wenn versehentlich eine URL uebergeben wird."""
    return urlsplit(wert if "://" in wert else "//" + wert).hostname or "unbekannt"


def _lokal_lesen(key: str) -> dict:
    with _sperre:
        wert, bis = _lokal.get(key, ({}, 0))
        return dict(wert) if bis > time.time() else {}


async def _lesen(key: str) -> dict:
    try:
        client = shared_cache.client()
        if client is not None:
            wert = await client.get(key)
            if wert is not None:
                return json.loads(wert)
    except Exception:
        logger.debug("Geodienst-Statistik nicht aus Redis lesbar", exc_info=False)
    return _lokal_lesen(key)


async def zaehlen(dienst: str, code: str, *, host: str | None = None,
                  http_status: int | None = None, dauer_ms: int = 0) -> None:
    """Auch Cache-Ereignisse und tatsaechliche Endpunktwechsel zaehlen."""
    try:
        jetzt = datetime.now(UTC)
        ttl = settings.GEODIENST_STATISTIK_TAGE * 86400
        keys = [f"geodienst-stat:{jetzt.date().isoformat()}:{dienst}"]
        neu = {}
        if host is not None:
            host = hostname(host)
            keys.extend([f"geodienst-letzt:{dienst}:{hashlib.sha256(host.encode()).hexdigest()[:16]}",
                         f"geodienst-hosts:{dienst}"])
            neu = {"host": host, "code": code, "http_status": http_status, "latenz_ms": dauer_ms,
                   "last_success" if code == "ok" else "last_failure": jetzt.isoformat().replace("+00:00", "Z")}
        with _sperre:
            for key in list(_lokal):
                if _lokal[key][1] <= time.time():
                    del _lokal[key]
            tag = _lokal_lesen(keys[0])
            tag[code] = tag.get(code, 0) + 1
            _lokal[keys[0]] = (tag, time.time() + ttl)
            if neu:
                _lokal[keys[1]] = (_lokal_lesen(keys[1]) | neu, time.time() + ttl)
                hosts = {key: bis for key, bis in _lokal_lesen(keys[2]).items() if bis > time.time()}
                _lokal[keys[2]] = (hosts | {keys[1]: time.time() + ttl}, time.time() + ttl)
        client = shared_cache.client()
        if client is not None:
            await client.eval(_AKTUALISIEREN, len(keys), *keys, code, ttl, json.dumps(neu), time.time() + ttl)
    except Exception:
        # Statistik ist niemals Voraussetzung fuer die eigentliche Fachabfrage.
        logger.debug("Geodienst-Statistik nicht schreibbar", exc_info=False)


async def erfolg(dienst: str, host: str, dauer_ms: int) -> None:
    await zaehlen(dienst, "ok", host=host, dauer_ms=dauer_ms)


async def fehler(dienst: str, host: str, code: str, http_status: int | None, dauer_ms: int) -> None:
    await zaehlen(dienst, code, host=host, http_status=http_status, dauer_ms=dauer_ms)


async def zusammenfassung(dienst: str, tage: int = 7) -> dict:
    zaehler: Counter = Counter()
    for offset in range(max(1, min(tage, settings.GEODIENST_STATISTIK_TAGE))):
        tag = (datetime.now(UTC) - timedelta(days=offset)).date().isoformat()
        zaehler.update(await _lesen(f"geodienst-stat:{tag}:{dienst}"))
    hosts = await _lesen(f"geodienst-hosts:{dienst}")
    letzte = [await _lesen(key) for key, bis in hosts.items() if bis > time.time()]
    letzte = [wert for wert in letzte if wert]
    cache = {code: zaehler.pop(code, 0) for code in ("cache_lokal", "cache_hit", "cache_miss", "negativ_cache")}
    ok, retry = zaehler.pop("ok", 0), zaehler.pop("retry_count", 0)
    nenner = cache["cache_hit"] + cache["cache_miss"]
    return {"errors_by_code": dict(zaehler), "ok": ok, "retry_count": retry,
            "hosts": letzte, "last_success": max((h.get("last_success", "") for h in letzte), default="") or None,
            "last_failure": max((h.get("last_failure", "") for h in letzte), default="") or None,
            **cache, "cache_hit_rate": cache["cache_hit"] / nenner if nenner else None,
            "cache_entries": None}
