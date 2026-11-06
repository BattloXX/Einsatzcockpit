"""An Benutzer und Einsatz gebundene App-Überwachungssitzungen."""
import json
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models.breathing import BreathingGeraetSitzung
from app.models.incident import Incident
from app.models.user import FcmToken, User
from app.services.breathing_service import breathing_effective_enabled
from app.services.push_service import fcm_token_has_capability
from app.services.update_service import parse_calver

HEARTBEAT_INTERVAL_S = 15
HEARTBEAT_MAX_AGE_S = 60
SITZUNG_MAX_STUNDEN = 24


class SitzungFehler(Exception):
    def __init__(self, code: str, nachricht: str, http_status: int):
        super().__init__(nachricht)
        self.code = code
        self.nachricht = nachricht
        self.http_status = http_status


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _selbsttest_json(selbsttest: dict | None) -> str | None:
    """Selbsttest als JSON begrenzen, damit die Sitzungszeile klein bleibt."""
    if selbsttest is None:
        return None
    text = json.dumps(selbsttest, ensure_ascii=False)
    if len(text) > 4000:
        raise SitzungFehler("selbsttest_zu_gross", "Der Selbsttest ist zu groß.", 422)
    return text


def _frisch(sitzung: BreathingGeraetSitzung, now: datetime) -> bool:
    return (
        sitzung.beendet_at is None
        and sitzung.letzter_heartbeat_at >= now - timedelta(seconds=HEARTBEAT_MAX_AGE_S)
        and sitzung.gestartet_at >= now - timedelta(hours=SITZUNG_MAX_STUNDEN)
    )


def client_platform(header: str | None, fallback: str | None = None) -> str:
    """Fehlender Header bleibt mit registrierten Android-Geraeten kompatibel."""
    value = header if header is not None else fallback
    return value if value in ("android", "ios") else "web"


def _selbsttest_gruen(sitzung: BreathingGeraetSitzung) -> bool:
    test = json.loads(sitzung.selbsttest) if sitzung.selbsttest else None
    return bool(test) and bewerte_selbsttest(test, sitzung.akku_prozent, sitzung.laedt)["ampel"] == "gruen"


def monitoring_fehler(
    db: Session, sitzung: BreathingGeraetSitzung, now: datetime | None = None,
) -> SitzungFehler | None:
    """iOS darf nur mit gruenem Selbsttest und frischem Android-Mitueberwacher schreiben."""
    if sitzung.platform == "web":
        return SitzungFehler("app_erforderlich", "Die Atemschutzüberwachung läuft nur in der Einsatzcockpit-App.", 403)
    if sitzung.platform != "ios":
        return None
    if not _selbsttest_gruen(sitzung):
        return SitzungFehler(
            "ios_selbsttest", "iOS bleibt nur lesend: Selbsttest muss grün sein. "
            "Bitte App-Gerätecheck durchführen.", 409,
        )
    now = now or _now()
    android = db.query(BreathingGeraetSitzung).join(
        Incident, Incident.id == BreathingGeraetSitzung.incident_id,
    ).join(User, User.id == BreathingGeraetSitzung.user_id).filter(
        BreathingGeraetSitzung.incident_id == sitzung.incident_id,
        BreathingGeraetSitzung.platform == "android", Incident.primary_org_id == User.org_id,
    ).all()
    if not any(row.id != sitzung.id and _frisch(row, now) and _selbsttest_gruen(row) for row in android):
        return SitzungFehler(
            "ios_android_erforderlich", "iOS bleibt nur lesend: Ein Android-Gerät mit grünem Selbsttest "
            "muss denselben Einsatz in der App mitüberwachen.", 409,
        )
    return None


def geraete_check(db: Session, sitzung: BreathingGeraetSitzung, now: datetime) -> dict:
    fehler = monitoring_fehler(db, sitzung, now)
    test = json.loads(sitzung.selbsttest) if sitzung.selbsttest else None
    return {
        **bewerte_selbsttest(test, sitzung.akku_prozent, sitzung.laedt),
        "platform": sitzung.platform, "monitoring_erlaubt": fehler is None,
        "plattform_hinweis": ("iOS: Überwachung nur mit grünem Selbsttest und aktivem "
                             "Android-Mitüberwacher mit grünem Selbsttest.")
        if sitzung.platform == "ios" else "",
        **({"error": fehler.code, "message": fehler.nachricht} if fehler else {}),
    }


def start_sitzung(
    db: Session, user: User, incident_id: int, fcm_token: str,
    app_version: str | None, selbsttest: dict | None, akku_prozent: int | None,
    laedt: bool | None, geraet_label: str | None, now: datetime | None = None,
    platform: str | None = None,
) -> BreathingGeraetSitzung:
    now = now or _now()
    incident = db.query(Incident).filter(
        Incident.id == incident_id, Incident.primary_org_id == user.org_id,
    ).first() if user.org_id is not None else None
    if incident is None:
        raise SitzungFehler("nicht_gefunden", "Einsatz nicht gefunden.", 404)
    if incident.status != "active":
        raise SitzungFehler("einsatz_inaktiv", "Der Einsatz ist nicht aktiv.", 409)
    if not breathing_effective_enabled(user.org_id, db):
        raise SitzungFehler("modul_inaktiv", "Atemschutzüberwachung ist nicht aktiviert.", 403)
    token = db.query(FcmToken).filter(FcmToken.token == fcm_token, FcmToken.user_id == user.id).first()
    if token is None or not fcm_token_has_capability(token, "asuew"):
        raise SitzungFehler("app_erforderlich", "Die Atemschutzüberwachung läuft nur in der Einsatzcockpit-App.", 403)
    if settings.ASUEW_MIN_APP_VERSION:
        minimum = parse_calver(settings.ASUEW_MIN_APP_VERSION)
        version = parse_calver(app_version or "")
        # Ein fehlender Tages-Suffix entspricht .0, wie im Update-Service.
        if minimum is None or version is None or (*version[:3], version[3] if len(version) == 4 else 0) < (
            *minimum[:3], minimum[3] if len(minimum) == 4 else 0
        ):
            raise SitzungFehler("app_veraltet", "Bitte die App aktualisieren.", 426)
    for vorher in db.query(BreathingGeraetSitzung).filter(
        BreathingGeraetSitzung.incident_id == incident_id,
        BreathingGeraetSitzung.user_id == user.id, BreathingGeraetSitzung.beendet_at.is_(None),
    ).all():
        vorher.beendet_at, vorher.ende_grund = now, "ersetzt"
    sitzung = BreathingGeraetSitzung(
        incident_id=incident_id, user_id=user.id, fcm_token_id=token.id,
        session_key=secrets.token_urlsafe(32), app_version=app_version,
        platform=client_platform(platform, token.platform), geraet_label=geraet_label,
        selbsttest=_selbsttest_json(selbsttest),
        akku_prozent=akku_prozent, laedt=laedt, gestartet_at=now, letzter_heartbeat_at=now,
    )
    db.add(sitzung)
    db.flush()
    return sitzung


def _eigene_sitzung(db: Session, session_key: str, user: User) -> BreathingGeraetSitzung:
    sitzung = db.query(BreathingGeraetSitzung).join(
        Incident, Incident.id == BreathingGeraetSitzung.incident_id,
    ).filter(
        BreathingGeraetSitzung.session_key == session_key,
        BreathingGeraetSitzung.user_id == user.id, Incident.primary_org_id == user.org_id,
    ).first() if user.org_id is not None else None
    if sitzung is None:
        raise SitzungFehler("sitzung_beendet", "Die Überwachungssitzung ist beendet oder abgelaufen.", 409)
    return sitzung


def heartbeat(
    db: Session, session_key: str, user: User, akku_prozent: int | None,
    laedt: bool | None, selbsttest: dict | None, now: datetime | None = None,
    platform: str | None = None,
) -> BreathingGeraetSitzung:
    now = now or _now()
    sitzung = _eigene_sitzung(db, session_key, user)
    if not _frisch(sitzung, now):
        raise SitzungFehler("sitzung_beendet", "Die Überwachungssitzung ist beendet oder abgelaufen.", 409)
    if platform is not None:
        sitzung.platform = client_platform(platform)
    sitzung.letzter_heartbeat_at = now
    if akku_prozent is not None:
        sitzung.akku_prozent = akku_prozent
    if laedt is not None:
        sitzung.laedt = laedt
    if selbsttest is not None:
        sitzung.selbsttest = _selbsttest_json(selbsttest)
    db.flush()
    return sitzung


def beende_sitzung(db: Session, session_key: str, user: User, now: datetime | None = None) -> None:
    sitzung = _eigene_sitzung(db, session_key, user)
    if sitzung.beendet_at is None:
        sitzung.beendet_at, sitzung.ende_grund = now or _now(), "beendet"
    db.flush()


def validiere_sitzung(
    db: Session, session_key: str, incident_id: int, user_id: int, now: datetime | None = None,
) -> BreathingGeraetSitzung | None:
    sitzung = db.query(BreathingGeraetSitzung).join(
        Incident, Incident.id == BreathingGeraetSitzung.incident_id,
    ).join(User, User.id == BreathingGeraetSitzung.user_id).filter(
        BreathingGeraetSitzung.session_key == session_key,
        BreathingGeraetSitzung.incident_id == incident_id, BreathingGeraetSitzung.user_id == user_id,
        Incident.primary_org_id == User.org_id,
    ).first()
    return sitzung if (sitzung is not None and _frisch(sitzung, now or _now())
                       and monitoring_fehler(db, sitzung, now) is None) else None


def hat_frischen_heartbeat(db: Session, incident_id: int, now: datetime | None = None) -> bool:
    now = now or _now()
    sitzungen = db.query(BreathingGeraetSitzung).join(
        Incident, Incident.id == BreathingGeraetSitzung.incident_id,
    ).join(User, User.id == BreathingGeraetSitzung.user_id).filter(
        Incident.primary_org_id == User.org_id,
        BreathingGeraetSitzung.incident_id == incident_id, BreathingGeraetSitzung.beendet_at.is_(None),
        BreathingGeraetSitzung.letzter_heartbeat_at >= now - timedelta(seconds=HEARTBEAT_MAX_AGE_S),
        BreathingGeraetSitzung.gestartet_at >= now - timedelta(hours=SITZUNG_MAX_STUNDEN),
    ).all()
    return any(monitoring_fehler(db, sitzung, now) is None for sitzung in sitzungen)


def bewerte_selbsttest(selbsttest: dict | None, akku_prozent: int | None, laedt: bool | None) -> dict:
    test = selbsttest or {}
    probleme: list[str] = []
    stufe = 0

    def problem(text: str, schwere: int) -> None:
        nonlocal stufe
        probleme.append(text)
        stufe = max(stufe, schwere)

    if test.get("benachrichtigung") == "denied":
        problem("Benachrichtigungen sind nicht erlaubt.", 2)
    elif test.get("benachrichtigung") == "default":
        problem("Benachrichtigungen wurden noch nicht erlaubt.", 1)
    if test.get("online") is False:
        problem("Keine Verbindung zum Server.", 2)
    abweichung = test.get("uhr_abweichung_s")
    if isinstance(abweichung, (int, float)) and not isinstance(abweichung, bool):
        if abs(abweichung) > 30:
            problem("Die Uhr weicht um mehr als 30 Sekunden ab.", 2)
        elif abs(abweichung) > 5:
            problem("Die Uhr weicht um mehr als 5 Sekunden ab.", 1)
    for key, text in (
        ("alarmton_getestet", "Der Alarmton wurde noch nicht getestet."),
        ("exakte_wecker", "Exakte Wecker sind nicht erlaubt."),
        ("vollbild", "Vollbildalarme sind nicht erlaubt."),
        ("akku_optimierung_ausgenommen", "Die App ist nicht von der Akku-Optimierung ausgenommen."),
    ):
        if test.get(key) is False:
            problem(text, 1)
    lautstaerke = test.get("alarm_lautstaerke_prozent")
    if isinstance(lautstaerke, (int, float)) and not isinstance(lautstaerke, bool):
        if lautstaerke == 0:
            problem("Die Alarmlautstärke ist auf 0 gestellt.", 2)
        elif 1 <= lautstaerke < 30:
            problem("Die Alarmlautstärke ist sehr niedrig.", 1)
    if akku_prozent is not None and not laedt:
        if akku_prozent < 10:
            problem("Akku unter 10 Prozent. Bitte Ladegerät anschließen.", 2)
        elif akku_prozent < 20:
            problem("Akku unter 20 Prozent. Bitte Ladegerät anschließen.", 1)
    return {"ampel": ("gruen", "gelb", "rot")[stufe], "probleme": probleme}
