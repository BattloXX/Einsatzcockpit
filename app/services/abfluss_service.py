"""Abfluss-Service: Pegelmessstationen von vowis.vorarlberg.at scrapen + in-memory Ring-Buffer.

Daten: Werte werden aus der HTML-Seite der jeweiligen Messstation extrahiert.
Cache: Aktueller Wert + HQ-Referenzwerte werden je Station im Arbeitsspeicher gehalten
       (keine DB-Persistenz, kein Neustart-Recovery).
TTL:   Scrape alle 10 Minuten, HQ-Werte alle 24 Stunden neu laden.
"""
import asyncio
import logging
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

logger = logging.getLogger("einsatzleiter.abfluss")

_VOWIS_BASE = "https://vowis.vorarlberg.at/stationsInfo/_Abfluss/ofwStation.aspx"
_FETCH_TTL_S = 600     # 10 Minuten zwischen Scrapes
_HQ_TTL_S   = 86_400  # 24 Stunden HQ-Referenzwerte-Cache
_MAX_VERLAUF = 96      # 96 Einträge * 15 min Polling-Intervall = 24 h Verlauf


@dataclass
class HQWerte:
    hq1:    float | None = None
    hq5:    float | None = None
    hq10:   float | None = None
    hq30:   float | None = None
    hq100:  float | None = None
    hq300:  float | None = None
    hq1000: float | None = None


@dataclass
class AbflussMessung:
    zeitstempel: datetime
    wert_m3s:    float


@dataclass
class _StationState:
    hzbnr:              str
    name:               str
    beschreibung:       str = ""
    verlauf:            deque = field(default_factory=lambda: deque(maxlen=_MAX_VERLAUF))
    aktuell:            AbflussMessung | None = None
    hq_werte:           HQWerte = field(default_factory=HQWerte)
    hq_abfragezeit:     datetime | None = None
    letzter_fehler:     str | None = None
    last_fetched:       datetime | None = None
    last_persisted_ts:  datetime | None = None


# { org_id: { hzbnr: _StationState } }
_store: dict[int, dict[str, _StationState]] = {}


# ── Alarm-Level ───────────────────────────────────────────────────────────────

def alarm_stufe(wert: float, hq: HQWerte) -> tuple[int, str, str]:
    """Returns (stufe 0–4, label_de, css_color)."""
    if hq.hq100 and wert >= hq.hq100:
        return 4, ">= HQ100", "var(--red)"
    if hq.hq30 and wert >= hq.hq30:
        return 3, ">= HQ30",  "#ef4444"
    if hq.hq10 and wert >= hq.hq10:
        return 2, ">= HQ10",  "#f97316"
    if hq.hq1 and wert >= hq.hq1:
        return 1, ">= HQ1",   "#eab308"
    return 0, "Normal",       "var(--ampel-green, #22c55e)"


# ── HTML-Parsing ──────────────────────────────────────────────────────────────

def _parse_html(html: str) -> tuple[float | None, datetime | None, HQWerte | None]:
    """Parse Abfluss-Wert, Zeitstempel und HQ-Referenzwerte aus der vowis HTML-Seite."""
    # Aktueller Wert: "Abfluss: 6,96 m³/s" oder "Abfluss:\n</td><td>7,11 m³/s"
    # HTML-Tags zwischen Label und Wert werden übersprungen.
    wert: float | None = None
    m = re.search(r"Abfluss:\s*(?:<[^>]+>\s*)*([\d,\.]+)\s*m", html, re.IGNORECASE)
    if m:
        try:
            wert = float(m.group(1).replace(",", "."))
        except ValueError:
            pass

    # Zeitstempel: "22.06.2026 14:15" oder "22.06. 14:15" (HTML-Tags möglich dazwischen)
    zeitstempel: datetime | None = None
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s*(?:<[^>]+>\s*)*(\d{2}):(\d{2})", html)
    if m:
        try:
            zeitstempel = datetime(
                int(m.group(3)), int(m.group(2)), int(m.group(1)),
                int(m.group(4)), int(m.group(5)), tzinfo=UTC,
            )
        except ValueError:
            pass
    if not zeitstempel:
        m = re.search(r"(\d{2})\.(\d{2})\.\s*(?:<[^>]+>\s*)*(\d{2}):(\d{2})", html)
        if m:
            now = datetime.now(UTC)
            try:
                zeitstempel = datetime(
                    now.year, int(m.group(2)), int(m.group(1)),
                    int(m.group(3)), int(m.group(4)), tzinfo=UTC,
                )
            except ValueError:
                pass

    # HQ-Referenzwerte: "HQ1: 480 m³/s" — HTML-Tags zwischen Label und Wert möglich
    hq = HQWerte()
    for key, attr in [
        ("HQ1:",    "hq1"),
        ("HQ5:",    "hq5"),
        ("HQ10:",   "hq10"),
        ("HQ30:",   "hq30"),
        ("HQ100:",  "hq100"),
        ("HQ300:",  "hq300"),
        ("HQ1000:", "hq1000"),
    ]:
        mm = re.search(re.escape(key) + r"\s*(?:<[^>]+>\s*)*([\d,\.]+)", html, re.IGNORECASE)
        if mm:
            try:
                setattr(hq, attr, float(mm.group(1).replace(",", ".")))
            except ValueError:
                pass

    return wert, zeitstempel, hq


# ── HTTP-Fetch ────────────────────────────────────────────────────────────────

async def _fetch_from_vowis(hzbnr: str) -> tuple[float | None, datetime | None, HQWerte | None]:
    """HTTP-Fetch der vowis-Stationsseite. Gibt (None, None, None) bei Fehler zurück."""
    url = f"{_VOWIS_BASE}?hzbnr={hzbnr}"
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Einsatzcockpit/2.x (+https://einsatzcockpit.com)"},
            timeout=12.0,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            wert, zeitstempel, hq = _parse_html(resp.text)
            if wert is None:
                snippet = resp.text[:500].replace("\n", " ").replace("\r", "")
                logger.warning(
                    "Abfluss-Parsing: kein Wert gefunden fuer hzbnr=%s url=%s – HTML-Anfang: %s",
                    hzbnr, url, snippet,
                )
            return wert, zeitstempel, hq
    except httpx.TimeoutException:
        logger.warning("Abfluss-Timeout: hzbnr=%s url=%s", hzbnr, url)
    except httpx.HTTPStatusError as exc:
        logger.warning("Abfluss-HTTP-Fehler %s: hzbnr=%s url=%s", exc.response.status_code, hzbnr, url)
    except Exception as exc:
        logger.warning("Abfluss-Fehler: hzbnr=%s url=%s – %s", hzbnr, url, exc)
    return None, None, None


# ── Persistenz (Wetter-DB) ────────────────────────────────────────────────────

def _persist_abfluss_reading(org_id: int, hzbnr: str, messung: AbflussMessung) -> None:
    """Schreibt eine Pegelmessung best-effort in die Wetter-DB (blockt – via asyncio.to_thread aufrufen)."""
    try:
        from app.db_weather import get_weather_session, weather_db_enabled
        from app.models.weather import AbflussReading
        if not weather_db_enabled():
            return
        session = get_weather_session()
        try:
            session.add(AbflussReading(
                org_id=org_id,
                hzbnr=hzbnr,
                ts=messung.zeitstempel,
                wert_m3s=messung.wert_m3s,
            ))
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.debug("Abfluss-Persist fehlgeschlagen: org=%s hzbnr=%s", org_id, hzbnr)


# ── Öffentliche API ───────────────────────────────────────────────────────────

def _state(org_id: int, hzbnr: str, name: str, beschreibung: str = "") -> _StationState:
    if org_id not in _store:
        _store[org_id] = {}
    if hzbnr not in _store[org_id]:
        _store[org_id][hzbnr] = _StationState(hzbnr=hzbnr, name=name, beschreibung=beschreibung)
    s = _store[org_id][hzbnr]
    s.name = name
    s.beschreibung = beschreibung
    return s


async def refresh_station(org_id: int, hzbnr: str, name: str, beschreibung: str = "") -> _StationState:
    """Daten der Station aktualisieren (TTL beachten). Gecachten State zurückgeben."""
    st = _state(org_id, hzbnr, name, beschreibung)
    now = datetime.now(UTC)

    if st.last_fetched and (now - st.last_fetched).total_seconds() < _FETCH_TTL_S:
        return st

    wert, zeitstempel, hq = await _fetch_from_vowis(hzbnr)
    st.last_fetched = now

    if wert is not None:
        messung = AbflussMessung(zeitstempel=zeitstempel or now, wert_m3s=wert)
        if not st.verlauf or st.verlauf[-1].zeitstempel != messung.zeitstempel:
            st.verlauf.append(messung)
        st.aktuell = messung
        st.letzter_fehler = None
        logger.debug("Abfluss OK: hzbnr=%s name=%r wert=%.3f m³/s ts=%s", hzbnr, name, wert, zeitstempel)
        # Persistenz in Wetter-DB (best-effort, nur neue Messungen)
        if messung.zeitstempel != st.last_persisted_ts:
            try:
                await asyncio.to_thread(_persist_abfluss_reading, org_id, hzbnr, messung)
                st.last_persisted_ts = messung.zeitstempel
            except Exception:
                pass
    else:
        logger.warning("Abfluss kein Wert: hzbnr=%s name=%r org_id=%s", hzbnr, name, org_id)
        st.letzter_fehler = "Keine Daten verfügbar"

    if hq is not None:
        needs_hq_refresh = (
            st.hq_abfragezeit is None
            or (now - st.hq_abfragezeit).total_seconds() >= _HQ_TTL_S
        )
        if needs_hq_refresh:
            st.hq_werte = hq
            st.hq_abfragezeit = now

    return st


async def refresh_all_for_org(org_id: int, stations_cfg: list[dict]) -> list[_StationState]:
    """Alle konfigurierten Stationen einer Org parallel aktualisieren."""
    tasks = [
        refresh_station(
            org_id,
            cfg["hzbnr"],
            cfg.get("name", cfg["hzbnr"]),
            cfg.get("beschreibung", ""),
        )
        for cfg in stations_cfg
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[_StationState] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Abfluss gather-Fehler: %s", r)
        else:
            out.append(r)  # type: ignore[arg-type]
    return out


def sparkline_data(st: _StationState) -> dict:
    """SVG-Polyline-Daten für die Verlaufslinie (letzte 24 h) berechnen."""
    readings = list(st.verlauf)
    if len(readings) < 2:
        return {"points": "", "width": 120, "height": 24}

    values = [r.wert_m3s for r in readings]
    min_v = min(values)
    max_v = max(values)
    span = max_v - min_v or 0.001

    W, H = 120, 24
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = round(i / (n - 1) * W)
        y = H - 1 - round((v - min_v) / span * (H - 4))
        pts.append(f"{x},{y}")

    return {"points": " ".join(pts), "width": W, "height": H}


def get_hq_werte(org_id: int, hzbnr: str) -> HQWerte | None:
    """Gibt gecachte HQ-Referenzwerte einer Station zurück, oder None."""
    st = _store.get(org_id, {}).get(hzbnr)
    return st.hq_werte if st else None


def remove_station(org_id: int, hzbnr: str) -> None:
    """Station aus dem In-Memory-Store entfernen (nach Löschen in Settings)."""
    if org_id in _store and hzbnr in _store[org_id]:
        del _store[org_id][hzbnr]
