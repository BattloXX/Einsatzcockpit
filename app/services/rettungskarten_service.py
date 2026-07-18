"""Rettungsdatenblaetter (Fahrzeug-Rettungskarten): on-demand fetch + Cache.

Beim ersten Aufruf zu einem Fahrzeugmodell wird das PDF von einer konfigurierten
Freigabe-Quelle (settings.NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE) geladen und
lokal zwischengespeichert (RettungsdatenblattCache + Datei). Danach ist es offline
verfuegbar (SW cache-first, /nachschlagewerk-cache/). KEINE Massen-Spiegelung —
nur Einzelabruf bei Bedarf; ohne konfigurierte Quelle bleiben nur Deep-Links.

Der Abruf ist synchron (httpx.Client) — die aufrufende Route ist ein normales
`def` und laeuft damit im Threadpool (kein Blockieren des Event-Loops).
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.nachschlagewerk import RettungsdatenblattCache

logger = logging.getLogger("einsatzleiter.nachschlagewerk")

_HTTP_TIMEOUT_S = 20.0
_PDF_MAGIC = b"%PDF-"


def _rettungskarten_dir() -> Path:
    return Path(settings.NACHSCHLAGEWERK_DATA_DIR) / "rettungskarten"


def absolute_pfad(eintrag: RettungsdatenblattCache) -> Path | None:
    """Absoluter Pfad der PDF-Datei (oder None, wenn nur Deep-Link bekannt)."""
    if not eintrag.pfad:
        return None
    return Path(settings.NACHSCHLAGEWERK_DATA_DIR) / eintrag.pfad


def _norm(text: str | None) -> str:
    return (text or "").strip()


def deep_links(hersteller: str, modell: str) -> list[dict]:
    """Immer erzeugbare Deep-Links auf externe Freigabe-Quellen (kein Hosting).

    Euro Rescue (Euro NCAP/CTIF) und ADAC sind SPAs/Landingpages ohne stabile
    Suchparameter, daher wird auf die offiziellen Einstiegsseiten verlinkt.
    """
    if not (_norm(hersteller) or _norm(modell)):
        return []
    return [
        {"label": "Euro Rescue (Euro NCAP/CTIF)", "url": "https://rescue.euroncap.com/"},
        {"label": "ADAC Rettungskarten",
         "url": "https://www.adac.de/rund-ums-fahrzeug/unfall-schaden-panne/rettungskarte/"},
    ]


def suche(db: Session, q: str, limit: int = 30) -> list[RettungsdatenblattCache]:
    """Volltext-artige Suche im Cache ueber Hersteller/Modell (LIKE)."""
    q = _norm(q)
    query = db.query(RettungsdatenblattCache)
    if q:
        like = f"%{q}%"
        from sqlalchemy import or_
        query = query.filter(or_(
            RettungsdatenblattCache.hersteller.like(like),
            RettungsdatenblattCache.modell.like(like),
        ))
    return (
        query.order_by(RettungsdatenblattCache.hersteller, RettungsdatenblattCache.modell)
        .limit(max(0, limit))
        .all()
    )


def finde(
    db: Session, hersteller: str, modell: str, baujahr_von: int | None = None,
) -> RettungsdatenblattCache | None:
    """Exakter Cache-Treffer zu Hersteller/Modell(/Baujahr)."""
    return (
        db.query(RettungsdatenblattCache)
        .filter(
            RettungsdatenblattCache.hersteller == _norm(hersteller),
            RettungsdatenblattCache.modell == _norm(modell),
            RettungsdatenblattCache.baujahr_von == baujahr_von,
        )
        .first()
    )


def _quell_url(hersteller: str, modell: str) -> str | None:
    tpl = (settings.NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE or "").strip()
    if not tpl:
        return None
    return tpl.format(hersteller=quote_plus(_norm(hersteller)),
                      modell=quote_plus(_norm(modell)))


def _speichere_pdf(daten: bytes) -> tuple[str, str]:
    """Legt das PDF unter rettungskarten/{uuid}/original.pdf ab. Rueckgabe (relpfad, sha256)."""
    rel_dir = Path("rettungskarten") / uuid.uuid4().hex
    ziel_dir = Path(settings.NACHSCHLAGEWERK_DATA_DIR) / rel_dir
    ziel_dir.mkdir(parents=True, exist_ok=True)
    ziel = ziel_dir / "original.pdf"
    ziel.write_bytes(daten)
    sha = hashlib.sha256(daten).hexdigest()
    return (str(rel_dir / "original.pdf").replace("\\", "/"), sha)


def _hole_und_cache(
    db: Session,
    hersteller: str,
    modell: str,
    url: str,
    baujahr_von: int | None = None,
    baujahr_bis: int | None = None,
    kraftstoff: str | None = None,
) -> RettungsdatenblattCache | None:
    """Laedt das PDF unter `url`, validiert und legt es im Cache ab (oder None)."""
    try:
        with httpx.Client(
            headers={"User-Agent": "Einsatzcockpit/2.x (+https://einsatzcockpit.com)"},
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            daten = resp.content
    except httpx.TimeoutException:
        logger.warning("Rettungskarten: Timeout beim Abruf von %s", url)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("Rettungskarten: HTTP %s von %s", exc.response.status_code, url)
        return None
    except Exception:
        logger.exception("Rettungskarten: Abruf fehlgeschlagen (%s)", url)
        return None

    if not daten or not daten.startswith(_PDF_MAGIC):
        logger.warning("Rettungskarten: Antwort ist kein PDF (%s)", url)
        return None
    if len(daten) > settings.NACHSCHLAGEWERK_RETTUNGSKARTEN_MAX_BYTES:
        logger.warning("Rettungskarten: PDF zu gross (%d Bytes, %s)", len(daten), url)
        return None

    try:
        rel_pfad, sha = _speichere_pdf(daten)
    except Exception:
        logger.exception("Rettungskarten: Speichern fehlgeschlagen (%s %s)", hersteller, modell)
        return None

    eintrag = RettungsdatenblattCache(
        hersteller=hersteller,
        modell=modell,
        baujahr_von=baujahr_von,
        baujahr_bis=baujahr_bis,
        kraftstoff=_norm(kraftstoff) or None,
        quelle=url,
        pfad=rel_pfad,
        bytes=len(daten),
        sha256=sha,
    )
    db.add(eintrag)
    db.commit()
    db.refresh(eintrag)
    logger.info("Rettungskarten: %s %s gecacht (%d Bytes).", hersteller, modell, len(daten))
    return eintrag


def finde_oder_hole(
    db: Session,
    hersteller: str,
    modell: str,
    baujahr_von: int | None = None,
    baujahr_bis: int | None = None,
    kraftstoff: str | None = None,
) -> tuple[RettungsdatenblattCache | None, list[dict]]:
    """Cache-Treffer liefern oder on-demand holen.

    Rueckgabe (eintrag_oder_None, deep_links). eintrag ist None, wenn kein PDF
    beschafft werden konnte (dann traegt die UI die Deep-Links an).
    """
    hersteller, modell = _norm(hersteller), _norm(modell)
    if not hersteller or not modell:
        return (None, [])

    treffer = finde(db, hersteller, modell, baujahr_von)
    links = deep_links(hersteller, modell)
    if treffer is not None:
        return (treffer, links)

    url = _quell_url(hersteller, modell)
    if not url:
        logger.info("Rettungskarten: keine Quelle konfiguriert (%s %s) - nur Deep-Links.",
                    hersteller, modell)
        return (None, links)

    eintrag = _hole_und_cache(db, hersteller, modell, url,
                              baujahr_von=baujahr_von, baujahr_bis=baujahr_bis,
                              kraftstoff=kraftstoff)
    return (eintrag, links)


def hole_aus_katalog(db: Session, katalog) -> RettungsdatenblattCache | None:
    """Oeffnet einen Katalog-Eintrag: Cache-Treffer liefern oder das PDF des
    hinterlegten Links laden und offline cachen.

    `katalog` ist eine RettungskartenKatalog-Zeile (mit pdf_url). Rueckgabe der
    (ggf. neu erzeugten) Cache-Zeile oder None, wenn kein PDF beschafft werden konnte.
    """
    hersteller, modell = _norm(katalog.hersteller), _norm(katalog.modell)
    if not hersteller or not modell:
        return None
    treffer = finde(db, hersteller, modell, katalog.baujahr_von)
    if treffer is not None:
        return treffer
    if not (katalog.pdf_url or "").strip():
        return None
    return _hole_und_cache(
        db, hersteller, modell, katalog.pdf_url.strip(),
        baujahr_von=katalog.baujahr_von, baujahr_bis=katalog.baujahr_bis,
        kraftstoff=katalog.antrieb)
