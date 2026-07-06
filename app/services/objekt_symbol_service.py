"""Objektverwaltung: Karten-Symbol-Katalog (pflegbar je Org, inkl. Bild-Upload).

Der Symbolkatalog ersetzt die frueher hartcodierte Liste. Das Rendering lebt weiter
client-seitig (app/static/js/objekt_karte.js), gespeist aus /objekte/karten-symbole.json.
Bildsymbole (SVG/PNG) werden geschuetzt ueber /objekt-medien/symbol/{id} ausgeliefert.
Symbolbilder zaehlen NICHT zur Speicher-Quota (Org-Konfig, klein und gedeckelt).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models.objekt import OBJEKT_SYMBOL_TYPEN, SYMBOL_STILE, ObjektSymbol

logger = logging.getLogger("einsatzleiter.objekt_symbol")

# Seed-Darstellung je Code: (text, stil) — identisch zum bisherigen JS-SYMBOLE-Fallback.
_SEED_DARSTELLUNG: dict[str, tuple[str, str]] = {
    "fsd": ("FSD", "box"),
    "schluesselbox": ("BOX", "box"),
    "bsp": ("BSP", "box"),
    "bmz": ("BMZ", "box"),
    "fbf": ("FBF", "box"),
    "dlk_stellplatz": ("DLK", "box"),
    "objektfunk": ("FUNK", "box"),
    "sammelplatz": ("SP", "gruen"),
    "feuerloescher": ("FL", "rot"),
    "hauptzugang": ("➜", "pfeil-voll"),
    "nebenzugang": ("➜", "pfeil-leer"),
    "stiege": ("ST", "gruen"),
    "aufzug": ("AZ", "box"),
    "gefahr_ex": ("EX", "dreieck"),
    "gefahr_gas": ("GAS", "dreieck"),
    "gefahr_chemie": ("CHE", "dreieck"),
    "gefahr_strom": ("kV", "dreieck"),
    "gefahr_pv": ("PV", "dreieck"),
    "hydrant_ueberflur": ("H", "hydrant"),
    "hydrant_unterflur": ("UH", "hydrant"),
}

_ERLAUBTE_ENDUNGEN = {"svg", "png"}


def seed_objekt_symbole(db: Session, org_id: int) -> None:
    """Legt die Standard-Karten-Symbole fuer eine Org an (idempotent, system=True)."""
    for i, (code, name) in enumerate(OBJEKT_SYMBOL_TYPEN.items(), start=1):
        text, stil = _SEED_DARSTELLUNG.get(code, (code[:4].upper(), "box"))
        exists = (
            db.query(ObjektSymbol)
            .filter(ObjektSymbol.org_id == org_id, ObjektSymbol.code == code)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if not exists:
            db.add(ObjektSymbol(
                org_id=org_id, code=code, name=name, stil=stil, text=text,
                sort=i, aktiv=True, system=True,
            ))
    db.flush()


def lade_symbol_katalog(db: Session, org_id: int | None, *, nur_aktive: bool = True) -> list[ObjektSymbol]:
    """Symbolkatalog einer Org (sort-geordnet)."""
    q = (
        db.query(ObjektSymbol)
        .filter(ObjektSymbol.org_id == org_id)
        .execution_options(include_all_tenants=True)
    )
    if nur_aktive:
        q = q.filter(ObjektSymbol.aktiv.is_(True))
    return q.order_by(ObjektSymbol.sort, ObjektSymbol.name).all()


def lade_symbol_labels(db: Session, org_id: int | None) -> dict[str, str]:
    """{code: name} der aktiven Symbole fuer Palette/Legende. Fallback = Konstante."""
    symbole = lade_symbol_katalog(db, org_id, nur_aktive=True)
    if symbole:
        return {s.code: s.name for s in symbole}
    return dict(OBJEKT_SYMBOL_TYPEN)


def symbol_bild_url(sym: ObjektSymbol) -> str | None:
    """Geschuetzte Auslieferungs-URL fuer ein Bildsymbol."""
    if sym.stil == "bild" and sym.bild_pfad:
        return f"/objekt-medien/symbol/{sym.id}"
    return None


def symbol_dict(sym: ObjektSymbol) -> dict:
    """JSON-Darstellung fuer den Client (objekt_karte.js)."""
    return {
        "code": sym.code,
        "name": sym.name,
        "stil": sym.stil,
        "text": sym.text or "",
        "bild": symbol_bild_url(sym),
    }


def symbol_katalog_json(db: Session, org_id: int | None) -> list[dict]:
    """Vollstaendiger Katalog als JSON-Liste (aktive Symbole)."""
    return [symbol_dict(s) for s in lade_symbol_katalog(db, org_id, nur_aktive=True)]


# ── Bild-Upload / -Auslieferung ─────────────────────────────────────────────────

_SVG_SCRIPT_RE = re.compile(rb"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_SVG_FOREIGN_RE = re.compile(rb"<foreignObject\b[^>]*>.*?</foreignObject\s*>", re.IGNORECASE | re.DOTALL)
# on*-Event-Attribute (onclick, onload, ...) und javascript:-URIs entfernen
_SVG_ON_ATTR_RE = re.compile(rb"\son[a-zA-Z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_SVG_JS_URI_RE = re.compile(rb"(href|xlink:href)\s*=\s*(\"|')\s*javascript:[^\"']*(\"|')", re.IGNORECASE)


def sanitize_svg(data: bytes) -> bytes:
    """Entfernt aktive Inhalte aus hochgeladenen SVGs (Script/foreignObject/on*/js-URIs).

    Zusaetzlich wird die Datei als <img> geladen (kein Skript-Kontext) und mit
    restriktivem CSP-Header ausgeliefert — Defense in Depth.
    """
    data = _SVG_SCRIPT_RE.sub(b"", data)
    data = _SVG_FOREIGN_RE.sub(b"", data)
    data = _SVG_ON_ATTR_RE.sub(b"", data)
    data = _SVG_JS_URI_RE.sub(rb"\1=\2#\3", data)
    return data


def _storage_root() -> Path:
    root = Path(settings.OBJEKT_MEDIA_DIR) / "symbole"
    root.mkdir(parents=True, exist_ok=True)
    return root


def symbol_bild_absolut(rel_pfad: str) -> Path:
    return Path(settings.OBJEKT_MEDIA_DIR) / rel_pfad.replace("\\", "/")


def store_symbol_bild(org_id: int, symbol_id: int, dateiname: str, data: bytes) -> str:
    """Speichert ein Symbolbild (SVG sanitisiert) und gibt den relativen Pfad zurueck.

    Raises ValueError bei falschem Typ/zu grosser Datei.
    """
    endung = (dateiname.rsplit(".", 1)[-1] if "." in dateiname else "").lower()
    if endung not in _ERLAUBTE_ENDUNGEN:
        raise ValueError("Nur SVG- oder PNG-Dateien sind erlaubt.")
    if len(data) > settings.OBJEKT_SYMBOL_MAX_BYTES:
        raise ValueError("Symbolbild ist zu gross.")
    if endung == "svg":
        data = sanitize_svg(data)
    ziel_dir = _storage_root() / str(org_id)
    ziel_dir.mkdir(parents=True, exist_ok=True)
    # Alte Datei(en) desselben Symbols aufraeumen (Endung kann wechseln)
    for alt in ziel_dir.glob(f"{symbol_id}.*"):
        try:
            alt.unlink()
        except OSError:
            pass
    ziel = ziel_dir / f"{symbol_id}.{endung}"
    ziel.write_bytes(data)
    return f"symbole/{org_id}/{symbol_id}.{endung}"


def delete_symbol_bild(rel_pfad: str | None) -> None:
    if not rel_pfad:
        return
    try:
        symbol_bild_absolut(rel_pfad).unlink()
    except OSError:
        pass


def bild_media_type(rel_pfad: str) -> str:
    return "image/svg+xml" if rel_pfad.lower().endswith(".svg") else "image/png"


def stil_gueltig(stil: str) -> bool:
    return stil in SYMBOL_STILE
