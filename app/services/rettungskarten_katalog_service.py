"""Katalog verfuegbarer Rettungskarten (Euro NCAP / CTIF "Euro Rescue").

Euro NCAP stellt zusammen mit CTIF alle Rettungsblaetter frei fuer Einsatzkraefte
bereit ("Free Downloadable Rescue Information for First Responders") — inklusive
einer offenen JSON-Katalog-API (settings.NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL,
Default api.rescue.euroncap.com). Dieser Service spiegelt NUR das Verzeichnis
(Hersteller/Modell/Baujahr/Antrieb + direkter PDF-Link je Modell) in die lokale
Tabelle `rettungskarten_katalog`, damit Einsatzkraefte offlinefaehig nach Modell
suchen koennen. Das eigentliche PDF wird erst beim Oeffnen on-demand geladen und
gecacht (rettungskarten_service) — keine Massen-Spiegelung der Dokumente.

Der Sync ist synchron (httpx.Client) und wird aus dem Nachschlagewerk-Loop via
asyncio.to_thread aufgerufen (kein Blockieren des Event-Loops). Best-effort: bei
Fehlern/unplausibler Antwort bleibt der bestehende Katalog unangetastet.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.nachschlagewerk import RettungskartenKatalog

logger = logging.getLogger("einsatzleiter.nachschlagewerk")

_HTTP_TIMEOUT_S = 60.0
# Plausibilitaets-Untergrenze: weniger Modelle -> Quelle vermutlich kaputt/leer,
# den bestehenden Katalog dann NICHT ersetzen.
_MIN_KATALOG = 100
# Sprachpraeferenz fuer das Rettungsblatt (Oesterreich/DACH -> Deutsch zuerst).
_SPRACH_RANG = {"DE": 0, "EN": 1}


def _liste_aus_antwort(data: object) -> list[dict]:
    """Findet die Variantenliste in der API-Antwort (bare Liste oder Dict mit Liste)."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return [x for x in value if isinstance(x, dict)]
    return []


def _jahr(wert: object) -> int | None:
    text = str(wert or "").strip()
    if text.isdigit():
        return int(text)
    return None


def _int_oder_none(wert: object) -> int | None:
    text = str(wert or "").strip()
    return int(text) if text.isdigit() else None


def _bestes_dokument(docs: list[dict]) -> tuple[str | None, str | None]:
    """Waehlt den PDF-Link: Rettungsblatt vor Leitfaden, Deutsch vor Englisch.

    Rueckgabe (url, sprachkuerzel) oder (None, None), wenn keins vorhanden.
    """
    if not docs:
        return (None, None)

    def rang(d: dict) -> tuple[int, int]:
        typ = 0 if (d.get("type") or "") == "Rescue Sheet" else 1  # Sheet vor Guide
        spr = _SPRACH_RANG.get((d.get("language") or "").upper(), 9)
        return (typ, spr)

    mit_url = [d for d in docs if isinstance(d, dict) and (d.get("url") or "").strip()]
    if not mit_url:
        return (None, None)
    best = min(mit_url, key=rang)
    return (best["url"].strip(), (best.get("language") or "").upper() or None)


def parse_variants(data: object) -> list[dict]:
    """Normalisiert die API-Antwort zu Katalog-Zeilen-Dicts (ohne DB-Zugriff)."""
    eintraege: list[dict] = []
    gesehen: set[str] = set()
    for v in _liste_aus_antwort(data):
        quelle_id = str(v.get("id") or "").strip()
        hersteller = str(v.get("make_name") or "").strip()
        modell = str(v.get("name") or v.get("model_name") or "").strip()
        if not quelle_id or not hersteller or not modell or quelle_id in gesehen:
            continue
        gesehen.add(quelle_id)
        pdf_url, pdf_sprache = _bestes_dokument(v.get("documents") or [])
        eintraege.append({
            "quelle_id": quelle_id[:40],
            "hersteller": hersteller[:100],
            "modell": modell[:150],
            "karosserie": (str(v.get("body_type") or "").strip() or None),
            "baujahr_von": _jahr(v.get("build_year_from")),
            "baujahr_bis": _jahr(v.get("build_year_until")),
            "tueren": _int_oder_none(v.get("doors")),
            "antrieb": (str(v.get("powertrain") or "").strip() or None),
            "pdf_url": pdf_url,
            "pdf_sprache": pdf_sprache,
            "bild_url": (str(v.get("picture_url") or "").strip() or None),
        })
    return eintraege


def _hole_rohdaten(url: str) -> object | None:
    try:
        with httpx.Client(
            headers={"User-Agent": "Einsatzcockpit/2.x (+https://einsatzcockpit.com)"},
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        logger.warning("Rettungskarten-Katalog: Timeout beim Abruf von %s", url)
    except httpx.HTTPStatusError as exc:
        logger.warning("Rettungskarten-Katalog: HTTP %s von %s", exc.response.status_code, url)
    except Exception:
        logger.exception("Rettungskarten-Katalog: Abruf/Parsing fehlgeschlagen (%s)", url)
    return None


def sync_katalog(db: Session) -> int:
    """Laedt den Euro-Rescue-Katalog und ersetzt die lokale Tabelle atomar.

    Rueckgabe: Zahl uebernommener Modelle, oder -1 wenn nichts geaendert wurde
    (keine URL, Abruf-Fehler, oder unplausibel wenige Zeilen).
    """
    url = (settings.NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL or "").strip()
    if not url:
        logger.info("Rettungskarten-Katalog: keine Quell-URL konfiguriert - kein Sync.")
        return -1

    data = _hole_rohdaten(url)
    if data is None:
        return -1

    zeilen = parse_variants(data)
    if len(zeilen) < _MIN_KATALOG:
        logger.warning(
            "Rettungskarten-Katalog: Antwort unplausibel (%d Modelle < %d) - nicht uebernommen.",
            len(zeilen), _MIN_KATALOG)
        return -1

    jetzt = datetime.now(UTC).replace(tzinfo=None)
    try:
        db.query(RettungskartenKatalog).delete()
        db.bulk_insert_mappings(
            RettungskartenKatalog.__mapper__,
            [{**z, "aktualisiert_am": jetzt} for z in zeilen],
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Rettungskarten-Katalog: Schreiben fehlgeschlagen")
        return -1

    logger.info("Rettungskarten-Katalog: %d Modelle aktualisiert (%s).", len(zeilen), url)
    return len(zeilen)


def suche_katalog(db: Session, q: str, limit: int = 50) -> list[RettungskartenKatalog]:
    """Suche im Katalog ueber Hersteller/Modell (LIKE, alle Begriffe muessen passen)."""
    query = db.query(RettungskartenKatalog)
    for begriff in (q or "").split():
        like = f"%{begriff}%"
        query = query.filter(or_(
            RettungskartenKatalog.hersteller.like(like),
            RettungskartenKatalog.modell.like(like),
        ))
    return (query.order_by(RettungskartenKatalog.hersteller,
                           RettungskartenKatalog.modell,
                           RettungskartenKatalog.baujahr_von)
            .limit(max(0, limit)).all())


def anzahl(db: Session) -> int:
    return db.query(RettungskartenKatalog).count()


def alle_als_dicts(db: Session) -> list[dict]:
    """Kompletter Katalog fuer die Offline-Suche im Browser (index.json)."""
    eintraege = (db.query(RettungskartenKatalog)
                 .order_by(RettungskartenKatalog.hersteller,
                           RettungskartenKatalog.modell).all())
    return [{
        "id": e.id,
        "hersteller": e.hersteller,
        "modell": e.modell,
        "karosserie": e.karosserie,
        "baujahr_von": e.baujahr_von,
        "baujahr_bis": e.baujahr_bis,
        "tueren": e.tueren,
        "antrieb": e.antrieb,
        "pdf_sprache": e.pdf_sprache,
        "hat_pdf": bool(e.pdf_url),
    } for e in eintraege]
