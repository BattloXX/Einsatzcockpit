"""Gemeinsame, rein lesende Auswertung fuer Systemdiagnose und MCP.

Der Aufrufer setzt den Tenant-Kontext; dieser Service erweitert ihn niemals.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.tenant import TenantContextMissing
from app.models.admin_security import utcnow
from app.models.app_geraet import AppGeraet, AppGeraetFehler
from app.models.master import FireDept, VehicleMaster
from app.models.user import DeviceToken, User
from app.services import app_download_service

# Code, Titel, Schweregrad, Hypothese, Empfehlung; gemeinsame Regeln fuer UI und Check.
REGELN = {
    "sdk": (
        "SDK < 24",
        "fail",
        "Android ist fuer den App-Betrieb zu alt.",
        "Android aktualisieren oder Geraet ersetzen.",
    ),
    "patch": (
        "Sicherheitspatch aelter als 12 Monate",
        "warn",
        "Sicherheitsupdates fehlen.",
        "Systemupdates in Android pruefen.",
    ),
    "webview": (
        "WebView fehlt oder Hauptversion < 100",
        "fail",
        "WebView ist unvollstaendig oder veraltet.",
        "Android System WebView aktualisieren.",
    ),
    "uhr": (
        "Uhrabweichung > 120 Sekunden",
        "warn",
        "Die Geraetezeit weicht von der Serverzeit ab.",
        "Automatische Uhrzeit aktivieren.",
    ),
    "speicher": (
        "Weniger als 200 MB frei",
        "warn",
        "Der Geraetespeicher ist fast voll.",
        "Speicher auf dem Geraet freigeben.",
    ),
    "version": (
        "Veraltete App-Version",
        "warn",
        "Ein neueres Release ist verfuegbar.",
        "App auf die Soll-Version aktualisieren.",
    ),
    "benachrichtigungen": (
        "Benachrichtigungen deaktiviert",
        "fail",
        "Android unterdrueckt App-Benachrichtigungen.",
        "Berechtigung in Android aktivieren.",
    ),
    "akku_optimierung_aus": (
        "Akku-Optimierung aktiv",
        "warn",
        "Android kann Hintergrunddienste beenden.",
        "App von der Akku-Optimierung ausnehmen.",
    ),
    "vollbild_intent": (
        "Vollbild-Intent deaktiviert",
        "warn",
        "Vollbild-Alarmierung ist eingeschraenkt.",
        "Vollbild-Berechtigung in Android pruefen.",
    ),
    "fcm": (
        "FCM nicht registriert",
        "fail",
        "Die Push-Registrierung fehlt trotz Geraetezugang.",
        "Netz und Push-Registrierung in der App pruefen.",
    ),
    "fehler": (
        "Wiederkehrende Fehler in den letzten 7 Tagen",
        "warn",
        "Eine App-Komponente meldet wiederholt Fehler.",
        "Fehlerdetails und App-Version pruefen.",
    ),
    "bericht": (
        "Kein Bericht seit mehr als 48 Stunden bei aktivem Dienst",
        "warn",
        "Der aktive Geraetezugang meldet keine Diagnose.",
        "App, Verbindung und Hintergrundbetrieb am Geraet pruefen.",
    ),
}


def calver(value: str | None) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"v?(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.(\d+))?", (value or "").strip(), re.I)
    if not match:
        return None
    year, month, day, revision = (int(v or 0) for v in match.groups())
    try:
        date(year, month, day)
    except ValueError:
        return None
    return year, month, day, revision


def soll_version() -> str | None:
    version = app_download_service.android_release_version()
    return version if calver(version) else None


def ampel(version: str | None, soll: str | None) -> str:
    ist, ziel = calver(version), calver(soll)
    if ist is None or ziel is None:
        return "unbekannt"
    return "veraltet" if ist < ziel else "aktuell"


def befunde(geraet, soll: str | None, *, token=None, fehler=(), jetzt: datetime | None = None) -> list[str]:
    now = jetzt or utcnow()
    details = geraet.geraet_details or {}
    codes = []
    if geraet.sdk_int is not None and geraet.sdk_int < 24:
        codes.append("sdk")
    try:
        patch = date.fromisoformat(geraet.sicherheitspatch or "")
        # Kalendermonate, einschliesslich Schaltjahr.
        grenze = now.date().replace(year=now.year - 1, day=min(now.day, monthrange(now.year - 1, now.month)[1]))
        if patch < grenze:
            codes.append("patch")
    except ValueError:
        pass  # Ein fehlendes Patch-Datum belegt kein Alter.
    webview = re.match(r"^(\d+)", geraet.webview_version or "")
    if webview is None or int(webview[1]) < 100:
        codes.append("webview")
    abweichung = details.get("locale", {}).get("uhr_abweichung_s")
    if isinstance(abweichung, (int, float)) and abs(abweichung) > 120:
        codes.append("uhr")
    frei = details.get("geraet", {}).get("speicher_mb_free")
    if isinstance(frei, (int, float)) and frei < 200:
        codes.append("speicher")
    if ampel(geraet.app_version, soll) == "veraltet":
        codes.append("version")
    codes.extend(
        key
        for key in ("benachrichtigungen", "akku_optimierung_aus", "vollbild_intent")
        if (geraet.berechtigungen or {}).get(key) is False
    )
    if geraet.device_token_id is not None and (geraet.dienste or {}).get("fcm_registriert") is False:
        codes.append("fcm")
    if any(f.anzahl >= 5 and f.last_at >= now - timedelta(days=7) for f in fehler):
        codes.append("fehler")
    if (
        token is not None
        and token.revoked_at is None
        and token.duty_active
        and geraet.last_report_at < now - timedelta(hours=48)
    ):
        codes.append("bericht")
    return codes


def gesundheit(codes: list[str]) -> str:
    return "fail" if any(REGELN[c][1] == "fail" for c in codes) else "warn" if codes else "ok"


def _scope(db: Session, query):
    """B1-Modelle besitzen kein TenantScoped-Mixin: explizit einschraenken."""
    if "current_org_id" not in db.info:
        raise TenantContextMissing("App-Geraete-Auswertung braucht einen expliziten Tenant-Kontext")
    org_id = db.info["current_org_id"]
    return query.where(AppGeraet.org_id == org_id) if org_id is not None else query


def verteilung(db: Session) -> dict[str, dict]:
    return {
        name: dict(
            db.execute(
                _scope(db, select(getattr(AppGeraet, name), func.count()).group_by(getattr(AppGeraet, name)))
            ).all()
        )
        for name in ("app_version", "android_version", "sdk_int", "hersteller", "geraeteklasse")
    }


def liste(
    db: Session,
    *,
    org_id: int | None = None,
    version: str | None = None,
    sdk_max: int | None = None,
    android_version: str | None = None,
    geraeteklasse: str | None = None,
    hersteller: str | None = None,
    nur_probleme: bool = False,
    lange_nicht_gesehen: bool = False,
    page: int = 0,
    page_size: int | None = 25,
    geraet_id: int | None = None,
) -> dict:
    # None liefert die vollstaendige Auswahl fuer lesende Aggregate.
    soll = soll_version()
    query = (
        select(AppGeraet, User, FireDept, DeviceToken, VehicleMaster)
        .join(User, User.id == AppGeraet.user_id)
        .outerjoin(FireDept, FireDept.id == AppGeraet.org_id)
        .outerjoin(DeviceToken, DeviceToken.id == AppGeraet.device_token_id)
        .outerjoin(VehicleMaster, VehicleMaster.id == DeviceToken.vehicle_master_id)
    )
    query = _scope(db, query)
    for name, value in (
        ("id", geraet_id),
        ("org_id", org_id),
        ("android_version", android_version),
        ("geraeteklasse", geraeteklasse),
        ("hersteller", hersteller),
    ):
        if value is not None and value != "":
            query = query.where(getattr(AppGeraet, name) == value)
    if sdk_max is not None:
        query = query.where(AppGeraet.sdk_int <= sdk_max)
    if version and version != "veraltet":
        query = query.where(AppGeraet.app_version == version)
    now = utcnow()
    # Fehler gebuendelt laden; keine N+1-Abfragen. Keine Tenant-Erweiterung.
    errors: dict[int, list] = {}
    for error in db.scalars(
        select(AppGeraetFehler).where(AppGeraetFehler.geraet_id.in_(query.with_only_columns(AppGeraet.id)))
    ):
        errors.setdefault(error.geraet_id, []).append(error)
    rows = []
    for geraet, user, org, token, fahrzeug in db.execute(
        query.order_by(AppGeraet.last_report_at.desc(), AppGeraet.id)
    ).unique():
        fehler = errors.get(geraet.id, [])
        codes = befunde(geraet, soll, token=token, fehler=fehler, jetzt=now)
        status = ampel(geraet.app_version, soll)
        if (
            (version == "veraltet" and status != "veraltet")
            or (nur_probleme and not codes)
            or (lange_nicht_gesehen and geraet.last_report_at >= now - timedelta(hours=48))
        ):
            continue
        rows.append(
            dict(
                geraet=geraet,
                benutzer=user,
                org=org,
                fahrzeug=fahrzeug,
                ampel=status,
                gesundheit=gesundheit(codes),
                befunde=codes,
                fehlerzahl=sum(f.anzahl for f in fehler),
                fehler=fehler,
            )
        )
    size = max(1, min(page_size, 100)) if page_size is not None else max(1, len(rows))
    page = max(0, page)
    return dict(
        geraete=rows[page * size : (page + 1) * size], gesamt=len(rows), page=page, page_size=size, soll_version=soll
    )
