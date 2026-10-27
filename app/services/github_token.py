"""Verschluesselter GitHub-Lesezugang und ausschliesslich oeffentliche Metadaten."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.master import SystemSettings

PREFIX = "github_read_token_"


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace(" UTC", "+00:00").replace("Z", "+00:00"))
        return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (ValueError, OverflowError):
        return None


def read(db: Session | None, key: str) -> str:
    if db is None:
        return ""
    row = db.get(SystemSettings, PREFIX + key)
    return (row.value or "") if row else ""


def put(db: Session, key: str, value: str) -> None:
    row = db.get(SystemSettings, PREFIX + key)
    if row is None:
        db.add(SystemSettings(key=PREFIX + key, value=value))
    else:
        row.value = value


def resolve(db: Session | None) -> tuple[str, str, bool]:
    encrypted = read(db, "enc")
    if encrypted:
        try:
            return decrypt_secret(encrypted), "DB", False
        except Exception:
            # Kein Exception-Text: dieser kann Schluessel-/Chiffretext enthalten.
            return "", "DB", True
    return settings.GITHUB_READ_TOKEN, "Umgebungsvariable" if settings.GITHUB_READ_TOKEN else "keine", False


def manual_expiry(value: str) -> datetime | None:
    if not value:
        return None
    parsed = parse_date(value)
    if parsed is None or not now() < parsed <= now() + timedelta(days=400):
        raise ValueError("Ablaufdatum muss in der Zukunft und hoechstens 400 Tage entfernt liegen.")
    return parsed


def save(db: Session, token: str, user_id: int, expiry: str = "") -> bool:
    expiration = manual_expiry(expiry)
    if not token or len(token) > 4096 or any(c.isspace() for c in token) or not token.isascii():
        raise ValueError("Bitte ein gueltiges GitHub-Token eingeben.")
    replaced = bool(read(db, "enc"))
    encrypted = encrypt_secret(token)
    delete(db)
    db.flush()
    for key, value in {
        "enc": encrypted, "set_at": now().isoformat(), "set_by": str(user_id),
        "expires_at": expiration.isoformat() if expiration else "",
        "expiry_source": "manuell" if expiration else "unbekannt",
        "last_check_at": "", "last_status": "nicht_getestet",
    }.items():
        put(db, key, value)
    return replaced


def delete(db: Session) -> None:
    for row in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX, autoescape=True)).all():
        db.delete(row)


def status(db: Session | None, override: str | None = None) -> dict:
    token, source, invalid = resolve(db)
    expiry = parse_date(read(db, "expires_at")) if source != "keine" else None
    fingerprint = read(db, "fingerprint")
    stale = source == "Umgebungsvariable" and fingerprint and fingerprint != hashlib.sha256(token.encode()).hexdigest()
    if stale:
        expiry = None
    days = (expiry - now()).days if expiry else None
    state = override or ("nicht_getestet" if stale else read(db, "last_status")) or "nicht_getestet"
    if source == "keine":
        state = "nicht_gesetzt"
    elif invalid:
        state = "ungueltig"
    elif expiry and expiry <= now():
        state = "abgelaufen"
    elif state == "ungueltig":
        state = "ungueltig"
    elif state == "keine_berechtigung":
        state = "keine_berechtigung"
    elif days is not None and days <= 30:
        state = "laeuft_bald_ab"
    messages = {
        "nicht_gesetzt": "Kein GitHub-Token hinterlegt.",
        "nicht_getestet": "GitHub-Zugang noch nicht getestet.",
        "ok": "GitHub-Zugang verfuegbar.",
        "ungueltig": "GitHub-Token ungueltig (401 oder nicht entschluesselbar).",
        "keine_berechtigung": "GitHub-Berechtigungen fehlen (403/404); Repository-Auswahl und Freigabe pruefen.",
        "eingeschraenkt": "GitHub-Verbindung derzeit eingeschraenkt; Verbindung erneut testen.",
        "abgelaufen": "GitHub-Token abgelaufen seit " + (expiry.strftime("%d.%m.%Y") if expiry else "unbekannt") + ".",
        "laeuft_bald_ab": f"GitHub-Token laeuft bald ab (noch ca. {days} Tage).",
    }
    return {
        "status": state, "meldung": messages.get(state, messages["eingeschraenkt"])
        + (" Bitte unter System > Einstellungen > GitHub-Zugang pruefen." if state != "ok" else ""),
        "restlaufzeit_tage": days, "hinterlegt_am": read(db, "set_at") if source == "DB" else None,
        "ablauf_am": expiry.isoformat() if expiry else None, "quelle": source,
        "hilfe": "/admin/system-einstellungen#github-zugang; https://github.com/"
        + settings.GITHUB_REPOSITORY + "/wiki/Administration-System-Admin-MCP",
        "expiry_source": read(db, "expiry_source") or "unbekannt",
        "letzter_test": (read(db, "last_check_at") or None) if not stale else None,
    }


def observe(db: Session, token: str, http_status: int, header: str | None) -> None:
    """Aenderungen maximal stuendlich; kein Commit der Session des Aufrufers."""
    # DB-Zeilen sperren/auffrischen: parallele Worker duerfen das Stundenfenster
    # nicht mit demselben alten Metadatenstand mehrfach beschreiben.
    (db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX, autoescape=True))
     .populate_existing().with_for_update().all())
    current, _, invalid = resolve(db)
    if invalid or not token or current != token:
        return
    fingerprint = hashlib.sha256(token.encode()).hexdigest()
    last = parse_date(read(db, "last_check_at"))
    if last and now() - last < timedelta(hours=1) and read(db, "fingerprint") == fingerprint:
        return
    state = {401: "ungueltig", 403: "keine_berechtigung", 404: "keine_berechtigung"}.get(
        http_status, "ok" if http_status == 200 else "eingeschraenkt")
    changes = {"last_status": state, "fingerprint": fingerprint}
    expiration = parse_date(header) if http_status in (200, 403, 404) else None
    if expiration:
        changes.update(expires_at=expiration.isoformat(), expiry_source="github")
    elif http_status == 200 and not header and read(db, "expiry_source") != "manuell":
        changes.update(expires_at="", expiry_source="unbekannt")
    if any(read(db, k) != v for k, v in changes.items()):
        for key, value in changes.items():
            put(db, key, value)
        put(db, "last_check_at", now().isoformat())
        db.flush()
