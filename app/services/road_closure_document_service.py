"""PDF-Verordnungen zu Strassensperren."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.models.road_closure import RESTRICTION_TYPES, RoadClosure, RoadClosureDocument
from app.services import road_closure_service


def extract_text(data: bytes) -> tuple[int, str]:
    try:
        reader = PdfReader(BytesIO(data))
        return len(reader.pages), "\n".join(page.extract_text() or "" for page in reader.pages)[:200_000]
    except Exception as exc:
        raise ValueError("PDF konnte nicht gelesen werden.") from exc


def _root() -> Path:
    return (Path(settings.OBJEKT_MEDIA_DIR) / "strassensperren").resolve()


def absolute_path(document: RoadClosureDocument) -> Path:
    path = (Path(settings.OBJEKT_MEDIA_DIR) / document.storage_path).resolve()
    if _root() not in path.parents:
        raise ValueError("Ungültiger Dokumentpfad.")
    return path


def store_document(
    db: Session, closure: RoadClosure, data: bytes, filename: str, user_id: int | None, source: str
) -> RoadClosureDocument:
    if not data.startswith(b"%PDF"):
        raise ValueError("Nur PDF-Dateien erlaubt.")
    if len(data) > settings.MCP_MAX_UPLOAD_BYTES:
        raise ValueError(f"Datei zu gross (max. {settings.MCP_MAX_UPLOAD_BYTES} Bytes).")
    pages, text = extract_text(data)
    name = Path(filename or "verordnung.pdf").name[:255] or "verordnung.pdf"
    relative = f"strassensperren/{closure.org_id}/{closure.id}/{uuid4().hex}.pdf"
    path = Path(settings.OBJEKT_MEDIA_DIR) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(data)
        document = RoadClosureDocument(
            org_id=closure.org_id, road_closure_id=closure.id, filename=name, storage_path=relative,
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), page_count=pages,
            extracted_text=text, source=source, uploaded_by_user_id=user_id,
        )
        db.add(document)
        db.flush()
        road_closure_service._change(db, closure, "document_added", user_id, source, None,
                                     after={"document_id": document.id, "filename": name})
        write_audit(db, "road_closure.document_added", org_id=closure.org_id, user_id=user_id,
                    entity_type="road_closure_document", entity_id=document.id,
                    payload={"road_closure_id": closure.id, "filename": name})
        return document
    except Exception:
        path.unlink(missing_ok=True)
        raise


def delete_document(
    db: Session, closure: RoadClosure, document: RoadClosureDocument, user_id: int | None, source: str
) -> None:
    path = absolute_path(document)
    path.unlink(missing_ok=True)
    road_closure_service._change(db, closure, "document_removed", user_id, source, None,
                                 before={"document_id": document.id, "filename": document.filename})
    write_audit(db, "road_closure.document_removed", org_id=closure.org_id, user_id=user_id,
                entity_type="road_closure_document", entity_id=document.id,
                payload={"road_closure_id": closure.id, "filename": document.filename})
    db.delete(document)


_MONTHS = {
    "januar": 1, "jänner": 1, "jaenner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE = re.compile(
    rf"(?P<tag>\d{{1,2}})\.\s*(?:(?P<monat>\d{{1,2}})\.|(?P<monatsname>{_MONTH_NAMES}))\s*(?P<jahr>\d{{4}})",
    re.I,
)
_TIME = re.compile(r"(?P<stunde>\d{1,2})[.:](?P<minute>\d{2})\s*Uhr|(?P<stunde2>\d{1,2})\s*Uhr", re.I)
_INTERVAL = re.compile(r"\bvon\s+(?P<von>.{1,80}?)\s+bis\s+(?P<bis>.{1,80}?)(?:[;\n]|\.\s|$)", re.I | re.S)
_STREET = re.compile(
    r"\b(?:(?:Am|An der|Auf der|In der|Untere|Obere|Alte|Neue)\s)?"
    r"(?:[A-ZÄÖÜ][a-zäöüß]*-)*[A-ZÄÖÜ][a-zäöüß]*-?(?i:straße|strasse|gasse|weg|platz|allee|ring|steig)\b"
    r"|\b[A-ZÄÖÜ][a-zäöüß]+er\s(?:Straße|Strasse|Gasse|Weg|Platz|Allee)\b"
    r"|\b[LB]\s?\d{1,4}\b"
)
_TYPE_PATTERNS = (
    ("closed", r"Vollsperre|Fahrverbot|für den gesamten (?:Fahrzeug)?verkehr|gesperrt|Sperre\b"),
    ("partial", r"halbseitig|einspurig|Fahrstreifen"),
    ("weight_limit", r"Gewichtsbeschränkung|höchstzulässige[ns]? Gesamtgewicht|\d\s*t\b"),
    ("height_limit", r"Höhenbeschränkung|\d\s*m\s*(?:Höhe|hoch)"),
    ("width_limit", r"Breitenbeschränkung|\d\s*m\s*(?:Breite|breit)"),
    ("one_way", r"Einbahn"),
    ("construction", r"Baustelle|Bauarbeiten"),
    ("residents_only", r"Anrainer"),
)
_EXCEPTION = re.compile(r"ausgenommen|ausgen\.|Anrainer|Einsatzfahrzeug|Linienbus|Rettung", re.I)
_REFERENCE = re.compile(r"(?:Geschäftszahl|Aktenzeichen|Zahl|Zl\.|GZ|AZ)\s*[:.]?\s*([A-Za-z0-9][^\n,;]{2,80})")
_AUTHORITY = re.compile(r"Bezirkshauptmannschaft|Marktgemeinde|Gemeinde|Stadt\s|Amt der|Land Vorarlberg|Polizei")


def _date(value: str, end: bool) -> tuple[str, str] | None:
    """Liefert (ISO-Datum mit Zeit, Fundstelle) für den ersten Datumswert in value."""
    match = _DATE.search(value)
    if match is None:
        return None
    day = int(match.group("tag"))
    year = int(match.group("jahr"))
    if match.group("monat"):
        month = int(match.group("monat"))
    else:
        month = _MONTHS[match.group("monatsname").casefold()]
    # Die Uhrzeit nur hinter dem Datum suchen, sonst würde "06.10" als 06:10 gelesen.
    time = _TIME.search(value, match.end())
    if time is not None and time.group("stunde") is not None:
        hour, minute = int(time.group("stunde")), int(time.group("minute"))
    elif time is not None:
        hour, minute = int(time.group("stunde2")), 0
    else:
        hour, minute = (23, 59) if end else (0, 0)
    try:
        parsed = datetime(year, month, day, hour, minute)
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M"), value[match.start():time.end() if time else match.end()].strip()


def entwurf_aus_text(text: str) -> dict[str, object]:
    """Heuristischer, ungeprüfter Entwurf der Sperrfelder aus dem Text einer Verordnung."""
    text = text or ""
    reasons: dict[str, str] = {}
    result: dict[str, object] = {
        "title_vorschlag": None,
        "valid_from": None,
        "valid_until": None,
        "reference_number": None,
        "authority": None,
        "restriction_type": None,
        "max_weight_t": None,
        "max_height_m": None,
        "max_width_m": None,
        "exceptions": None,
        "strassen": [],
        "hinweis": "Automatischer Entwurf aus dem PDF-Text – bitte alle Felder prüfen.",
        "begruendung": reasons,
    }
    for interval in _INTERVAL.finditer(text):
        start = _date(interval.group("von"), end=False)
        finish = _date(interval.group("bis"), end=True)
        if start and finish:
            result["valid_from"], reasons["valid_from"] = start
            result["valid_until"], reasons["valid_until"] = finish
            break
    if result["valid_from"] is None:
        for key, end, word in (("valid_from", False, r"(?:ab|ab dem|beginnend mit)"), ("valid_until", True, r"bis")):
            for match in re.finditer(rf"\b{word}\s+(.{{1,60}})", text, re.I):
                parsed = _date(match.group(1), end)
                if parsed:
                    result[key], reasons[key] = parsed
                    break
    reference = _REFERENCE.search(text)
    if reference:
        result["reference_number"] = reference.group(1).strip()[:120]
        reasons["reference_number"] = reference.group(0).strip()
    for line in text.splitlines():
        if _AUTHORITY.search(line):
            result["authority"] = line.strip()[:200]
            reasons["authority"] = line.strip()
            break
    for kind, pattern in _TYPE_PATTERNS:
        type_match = re.search(pattern, text, re.I)
        if type_match:
            result["restriction_type"] = kind
            reasons["restriction_type"] = type_match.group(0)
            break
    measures = (
        ("max_weight_t", r"(\d+(?:[,.]\d+)?)\s*(?:t\b|Tonnen)"),
        ("max_height_m", r"(\d+(?:[,.]\d+)?)\s*m\s*(?:Höhe|hoch)"),
        ("max_width_m", r"(\d+(?:[,.]\d+)?)\s*m\s*(?:Breite|breit)"),
    )
    for field, pattern in measures:
        measure = re.search(pattern, text, re.I)
        if measure:
            result[field] = float(measure.group(1).replace(",", "."))
            reasons[field] = measure.group(0)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n", text) if _EXCEPTION.search(part)]
    if sentences:
        result["exceptions"] = " ".join(sentences)[:500]
        reasons["exceptions"] = sentences[0][:500]
    streets: list[str] = []
    for match in _STREET.finditer(text):
        candidate = re.sub(r"\s+", " ", match.group(0)).strip()
        if candidate.casefold() not in {item.casefold() for item in streets}:
            streets.append(candidate)
    result["strassen"] = streets[:10]
    if streets:
        label = RESTRICTION_TYPES.get(str(result["restriction_type"]), "Einschränkung")
        result["title_vorschlag"] = f"{label} {streets[0]}"
    return result
