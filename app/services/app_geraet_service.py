"""Whitelist, Datenschutz und Speicherung eingehender Diagnoseberichte."""
import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.app_geraet import AppGeraet, AppGeraetFehler
from app.models.user import DeviceToken, User

# Regeln: String-Laenge, bool, numerischer Bereich, Enum oder Spezialvalidator.
REGELN: dict[str, dict[str, Any]] = {
    "app": {"version_name": 30, "version_code": (0, 2147483647),
            "build_variant": {"auto_update", "standard"}, "target_sdk": (1, 200), "min_sdk": (1, 200),
            "first_install_at": "zeit", "last_update_at": "zeit"},
    "geraet": {"hersteller": 80, "marke": 80, "modell": 80, "geraetename": 80,
               "klasse": {"handy", "tablet"}, "foldable": bool, "abi": 80,
               "ram_mb_total": (0, 2147483647), "ram_mb_free": (0, 2147483647),
               "speicher_mb_free": (0, 2147483647), "akku_prozent": (0, 100), "laedt": bool,
               "akkusparer": bool, "sim_anzahl": (0, 10), "nfc": bool, "kamera": bool, "telefonie": bool},
    "android": {"version": 40, "sdk_int": (1, 200), "sicherheitspatch": "datum",
                "build_id": 80, "oberflaeche": 80},
    "display": {"breite_px": (1, 32000), "hoehe_px": (1, 32000), "dpi": (1, 5000),
                "dichte_bucket": 20, "breite_dp": (1, 32000), "hz": (1, 1000, "float"),
                "ausrichtung": {"hoch", "quer"}, "schrift_skala": (0.1, 10, "float"),
                "anzeige_skala": (0.1, 10, "float")},
    "webview": {"paket": 120, "version": 80},
    "locale": {"sprache": 40, "zeitzone": 80, "uhr_abweichung_s": (-2147483647, 2147483647)},
    "einstellungen": {"el_alarm_tone": 120, "el_alarm_volume_percent": (0, 100),
                      "el_alarm_override_silent": bool, "el_live_enabled": bool,
                      "el_gateway_receive_enabled": bool, "auto_update": bool, "base_url_host": "host"},
    "berechtigungen": {"benachrichtigungen": bool, "akku_optimierung_aus": bool, "vollbild_intent": bool,
                       "dnd_zugriff": bool, "standort": {"nein", "vordergrund", "immer"},
                       "sms": bool, "netz": {"wlan", "mobil", "keins"}},
    "dienste": {"poller_laeuft": bool, "keepalive": bool, "gateway_verbunden": bool,
                "fcm_registriert": bool, "fcm_faehigkeiten": "liste", "device_token_vorhanden": bool,
                "gateway_token_vorhanden": bool, "letzter_poll_ok_at": "zeit",
                "letzte_push_bestaetigung_at": "zeit", "letzter_kontaktsync_at": "zeit"},
}


def _zeit(wert: Any) -> datetime | None:
    if not isinstance(wert, str):
        return None
    try:
        zeit = datetime.fromisoformat(wert)
        if zeit.tzinfo is not None:
            return zeit.astimezone(UTC).replace(tzinfo=None)
    except (ValueError, OverflowError):
        return None
    return None


def maskiere_text(text: str) -> str:
    """Vor dem Kuerzen maskieren, damit abgeschnittene Geheimnisse nicht entkommen."""
    text = text.encode("utf-8", errors="replace").decode("utf-8")
    text = re.sub(r"[a-z][a-z0-9+.-]*://[^\s<>]+", lambda m: _url_host(m[0]), text, flags=re.I)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[E-Mail]", text)
    text = re.sub(r"(?i)\b(?:bearer\s+\S+|(?:token|password|secret|api[_-]?key)\s*[:=]\s*\S+)",
                  "[Token]", text)
    text = re.sub(r"(?<!\w)[A-Za-z0-9_+/=-]{32,}(?!\w)", "[Token]", text)
    text = re.sub(r"(?<!\w)\+?\d(?:[\d ()/.-]*\d){6,}(?!\w)", "[Telefon]", text)
    return text


def _url_host(url: str) -> str:
    try:
        return urlsplit(url).hostname or "[URL]"
    except ValueError:
        return "[URL]"


def _wert(wert: Any, regel: Any) -> Any:
    if regel is bool:
        return wert if type(wert) is bool else None
    if isinstance(regel, int):
        return maskiere_text(wert.strip())[:regel] if isinstance(wert, str) else None
    if isinstance(regel, set):
        return wert if isinstance(wert, str) and wert in regel else None
    if isinstance(regel, tuple):
        typen = (int, float) if len(regel) == 3 else (int,)
        if type(wert) in typen and regel[0] <= wert <= regel[1] and math.isfinite(wert):
            return wert
        return None
    if regel == "zeit":
        zeit = _zeit(wert)
        return zeit.isoformat() + "Z" if zeit else None
    if regel == "datum" and isinstance(wert, str):
        try:
            return date.fromisoformat(wert).isoformat()
        except ValueError:
            return None
    if regel == "host" and isinstance(wert, str):
        # Ausschliesslich Hostnamen, nie Credentials, Pfade oder Query speichern.
        if re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", wert) and not wert.startswith("."):
            return wert.lower()
    if regel == "liste" and isinstance(wert, list):
        return [v for v in wert[:50] if isinstance(v, str) and re.fullmatch(r"[a-z0-9_]{1,40}", v)]
    return None


def validiere_bericht(data: Any) -> dict:
    """Nur Schema und Installations-ID sind Pflicht; kaputte Einzelfelder entfallen."""
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise ValueError("schema muss 1 sein")
    try:
        install_id = UUID(data.get("install_id", ""))
        if install_id.version != 4:
            raise ValueError("install_id muss UUID4 sein")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("install_id muss UUID4 sein") from exc
    result: dict[str, Any] = {"install_id": str(install_id)}
    if sent_at := _wert(data.get("sent_at"), "zeit"):
        result["sent_at"] = sent_at
    for gruppe, regeln in REGELN.items():
        quelle = data.get(gruppe)
        result[gruppe] = {}
        if isinstance(quelle, dict):
            for key, regel in regeln.items():
                wert = _wert(quelle.get(key), regel)
                if wert is not None:
                    result[gruppe][key] = wert
                elif gruppe == "locale" and key in quelle and quelle[key] is None:
                    result[gruppe][key] = None
    result["fehler"] = []
    fehler = data.get("fehler")
    if isinstance(fehler, list):
        for item in fehler[:50]:
            if not isinstance(item, dict):
                continue
            ts = _zeit(item.get("ts"))
            komponente = _wert(item.get("komponente"), 40)
            code = _wert(item.get("code"), 80)
            if ts and komponente and code:
                result["fehler"].append({
                    "ts": ts, "komponente": komponente, "code": code,
                    "text": _wert(item.get("text"), 200) or "",
                    "anzahl": _wert(item.get("anzahl"), (1, 1000000)) or 1,
                })
    return result


class BerichtLimitErreicht(Exception):
    """Mehr als 30 Berichte im rollenden Stundenfenster."""


def speichere_bericht(
    db: Session, user: User, data: dict, device_token: DeviceToken | None = None,
) -> AppGeraet:
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    row = db.query(AppGeraet).filter_by(user_id=user.id, install_id=data["install_id"]).first()
    if row is None:
        row = AppGeraet(user_id=user.id, org_id=user.org_id, install_id=data["install_id"],
                       first_seen_at=jetzt, last_report_at=jetzt, last_seen_at=jetzt, report_zeiten=[])
        try:
            # Der Unique-Constraint (user_id, install_id) serialisiert parallele Erstberichte.
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.query(AppGeraet).filter_by(user_id=user.id, install_id=data["install_id"]).one()
    zeiten = [ts for ts in row.report_zeiten if ts > (jetzt.replace(tzinfo=UTC) - timedelta(hours=1)).timestamp()]
    if len(zeiten) >= 30:
        raise BerichtLimitErreicht
    # UTC-Epoch ohne Abhaengigkeit von der lokalen Server-Zeitzone.
    row.report_zeiten = [*zeiten, jetzt.replace(tzinfo=UTC).timestamp()]
    row.org_id = user.org_id
    row.device_token_id = device_token.id if device_token else None
    row.last_report_at = row.last_seen_at = jetzt
    for gruppe, mapping in {
        "app": {"version_name": "app_version", "version_code": "version_code", "build_variant": "build_variant"},
        "geraet": {"hersteller": "hersteller", "modell": "modell", "klasse": "geraeteklasse", "abi": "abi"},
        "android": {"version": "android_version", "sdk_int": "sdk_int", "sicherheitspatch": "sicherheitspatch"},
        "webview": {"version": "webview_version"}, "display": {"dpi": "dichte_dpi"},
    }.items():
        for key, spalte in mapping.items():
            if key in data[gruppe]:
                setattr(row, spalte, data[gruppe][key])
    display = data["display"]
    if "breite_px" in display and "hoehe_px" in display:
        row.aufloesung = f'{display["breite_px"]}x{display["hoehe_px"]}'
    for gruppe in ("einstellungen", "berechtigungen", "dienste"):
        setattr(row, gruppe, data[gruppe])
    row.geraet_details = {key: data[key] for key in ("app", "geraet", "android", "display", "webview", "locale")}
    if "sent_at" in data:
        row.geraet_details["sent_at"] = data["sent_at"]
    if device_token:
        device_token.platform = "android"
        for quelle, ziel in (("app_version", "app_version"), ("android_version", "os_version"),
                            ("modell", "device_model")):
            if (wert := getattr(row, quelle)) is not None:
                setattr(device_token, ziel, wert)
    db.flush()
    for item in sorted(data["fehler"], key=lambda f: f["ts"]):
        ts = item["ts"]
        if ts < jetzt - timedelta(days=90) or ts > jetzt + timedelta(minutes=5):
            continue
        fehler = db.query(AppGeraetFehler).filter_by(
            geraet_id=row.id, komponente=item["komponente"], code=item["code"],
        ).first()
        if fehler is None:
            fehler = AppGeraetFehler(geraet_id=row.id, komponente=item["komponente"], code=item["code"],
                                    text=item["text"], anzahl=item["anzahl"], first_at=ts, last_at=ts,
                                    app_version=row.app_version)
            db.add(fehler)
        elif ts > fehler.last_at:
            # Die App meldet die seit Installation kumulierte Anzahl; nicht addieren.
            fehler.anzahl = max(fehler.anzahl, item["anzahl"])
            fehler.last_at, fehler.text, fehler.app_version = ts, item["text"], row.app_version
        db.flush()
    for alt in db.query(AppGeraetFehler).filter_by(geraet_id=row.id).order_by(
        AppGeraetFehler.last_at.desc(), AppGeraetFehler.id.desc(),
    ).offset(200).all():
        db.delete(alt)
    db.flush()
    return row


def bereinige_alte_berichte(db: Session, jetzt: datetime | None = None) -> int:
    """Systemweite Retention: Fehler und zuletzt vor 90 Tagen gemeldete Geraete."""
    grenze = (jetzt or datetime.now(UTC).replace(tzinfo=None)) - timedelta(days=90)
    anzahl = 0
    for fehler in db.query(AppGeraetFehler).filter(AppGeraetFehler.last_at < grenze).all():
        db.delete(fehler)
        anzahl += 1
    db.flush()
    for geraet in db.query(AppGeraet).filter(AppGeraet.last_report_at < grenze).all():
        db.delete(geraet)
        anzahl += 1
    db.flush()
    return anzahl


def purge_app_geraete() -> int:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        anzahl = bereinige_alte_berichte(db)
        db.commit()
        return anzahl
