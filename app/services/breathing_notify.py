"""Persistente ASUEW-Warnungen mit gestuftem Push- und SMS-Versand.

Der Aufrufer committet sync/schliessen; der Versand besitzt eigene Transaktionen.
Alle Fristen sind naive UTC.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from time import monotonic

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.audit import write_incident_change
from app.core.fehlerlog import log_fehler
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.breathing import BreathingTroop, BreathingWarnung, BreathingZustellung
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from app.services.breathing_service import get_eskalation_empfaenger_ids
from app.services.db_transactions import retry_claim
from app.services.loop_utils import iteration_watch
from app.services.push_service import notify_user
from app.services.sms_service import resolve_sms_config, send_sms

logger = logging.getLogger("einsatzleiter.breathing_notify")
# None als Verzoegerung bedeutet die org-spezifische escalation_grace_min.
Stufe = tuple[int, int | None, str, tuple[str, ...], bool]
ESKALATIONSPLAN: dict[str, list[Stufe]] = {
    "one_third_due": [(0, 60, "ueberwacher", ("push",), True),
                      (1, 180, "leitung", ("push",), True),
                      (2, 300, "leitung", ("sms",), False)],
    "two_third_due": [(0, 60, "ueberwacher", ("push",), True),
                      (1, 180, "leitung", ("push",), True),
                      (2, 300, "leitung", ("sms",), False)],
    "withdraw": [(0, 0, "ueberwacher", ("push",), True),
                 (1, 60, "leitung", ("push", "sms"), True)],
    "max_exceeded": [(0, 0, "ueberwacher", ("push",), True),
                     (1, None, "leitung", ("push", "sms"), True)],
    "funk_verloren": [(0, 0, "ueberwacher", ("push",), True),
                      (0, 0, "leitung", ("push", "sms"), True)],
    "notfall": [(0, 0, "ueberwacher", ("push", "sms"), True),
                (0, 0, "leitung", ("push", "sms"), True)],
    "kein_sicherheitstrupp": [(0, 0, "leitung", ("push",), False)],
    "assp": [(0, 0, "leitung", ("push",), False)],
    "kein_heartbeat": [(0, 0, "leitung", ("push",), True),
                       (1, 180, "leitung", ("sms",), False)],
}
TEXTE = {
    "one_third_due": ("Atemschutz: Kontrolle fällig (1/3)", "Kontrolle fällig (1/3)"),
    "two_third_due": ("Atemschutz: Kontrolle fällig (2/3)", "Kontrolle fällig (2/3)"),
    "withdraw": ("Atemschutz: Rückzugsdruck erreicht", "Rückzugsdruck erreicht"),
    "max_exceeded": ("Atemschutz: Maximale Einsatzzeit überschritten", "Maximale Einsatzzeit überschritten"),
    "funk_verloren": ("Atemschutz: Kein Funkkontakt", "Kein Funkkontakt"),
    "notfall": ("ATEMSCHUTZNOTFALL", "Atemschutznotfall"),
    "kein_sicherheitstrupp": ("Atemschutz: Kein Sicherheitstrupp bereit", "Kein Sicherheitstrupp bereit"),
    "assp": ("Atemschutz: ASSP alarmieren", "ASSP alarmieren"),
    "kein_heartbeat": ("Atemschutz: Kein Überwachungsgerät in der App aktiv",
                       "Kein Überwachungsgerät aktiv (Heartbeat fehlt)"),
}
PushSender = Callable[[Session, int, str, str, str, bool, dict[str, str]], bool]
SmsSender = Callable[[int, str, str, str], Awaitable[bool]]


def empfaenger_aufloesen(db: Session, incident: Incident, troop: BreathingTroop, gruppe: str) -> list[User]:
    """Nur aktive Empfaenger der primaeren Einsatzorganisation aufloesen."""
    org_id = incident.primary_org_id
    if org_id is None:
        return []
    ids: list[int] = []
    if gruppe == "ueberwacher":
        user_id = troop.ueberwacher_user_id or incident.incident_leader_user_id
        if user_id is not None:
            ids.append(user_id)
    elif gruppe == "leitung":
        ids, _sms = get_eskalation_empfaenger_ids(db, org_id)
        if incident.incident_leader_user_id is not None:
            ids.append(incident.incident_leader_user_id)
        if not ids:
            return db.query(User).join(UserRole, UserRole.user_id == User.id).join(
                Role, Role.id == UserRole.role_id,
            ).filter(User.org_id == org_id, User.active.is_(True),
                     Role.code == "breathing_supervisor").order_by(User.id).all()
    else:
        raise ValueError(f"Unbekannte Empfaengergruppe: {gruppe}")
    return db.query(User).filter(
        User.id.in_(set(ids)), User.org_id == org_id, User.active.is_(True),
    ).order_by(User.id).all() if ids else []


def _schliesse(db: Session, warnung: BreathingWarnung, now: datetime) -> None:
    warnung.erledigt_at = now
    for zeile in db.query(BreathingZustellung).filter(
        BreathingZustellung.warnung_id == warnung.id, BreathingZustellung.status == "pending",
    ).all():
        zeile.status = "suppressed"
        zeile.fehler = "erledigt"
        zeile.lease_bis = None


def schliesse_warnungen_fuer_trupp(db: Session, troop: BreathingTroop, now: datetime) -> None:
    """Alle offenen Warnungen einschliesslich alter Zyklen schliessen."""
    db.flush()
    for warnung in db.query(BreathingWarnung).filter(
        BreathingWarnung.troop_id == troop.id, BreathingWarnung.erledigt_at.is_(None),
    ).all():
        _schliesse(db, warnung, now)


def sync_warnungen(
    db: Session, incident: Incident, troop: BreathingTroop, aktive_kinds: set[str], now: datetime,
) -> list[BreathingWarnung]:
    """Warnungen und geplante Zustellungen in der aufrufenden Transaktion erzeugen.

    Der Watchdog laeuft ausschliesslich im Leader-Worker und ist der einzige
    Erzeuger. Die Pruefung auf offene Warnungen im selben Durchlauf stellt
    Idempotenz ohne Zeilensperren sicher.
    """
    if troop.incident_id != incident.id:
        raise ValueError("Trupp gehoert nicht zum Einsatz")
    unbekannt = aktive_kinds - ESKALATIONSPLAN.keys()
    if unbekannt:
        raise ValueError(f"Unbekannte Warnungsarten: {sorted(unbekannt)}")
    db.flush()
    offene = db.query(BreathingWarnung).filter(
        BreathingWarnung.troop_id == troop.id, BreathingWarnung.erledigt_at.is_(None),
    ).all()
    vorhanden = set()
    for warnung in offene:
        if warnung.kind not in aktive_kinds or warnung.zyklus_start != troop.entry_at:
            _schliesse(db, warnung, now)
        else:
            vorhanden.add(warnung.kind)
    org = db.get(FireDept, incident.primary_org_id) if incident.primary_org_id is not None else None
    grace = max(0, org.escalation_grace_min if org and org.escalation_grace_min is not None else 3) * 60
    sms_an = (
        get_eskalation_empfaenger_ids(db, incident.primary_org_id)[1]
        if incident.primary_org_id is not None else False
    )
    neue = []
    for kind in sorted(aktive_kinds - vorhanden):
        try:
            with db.begin_nested():
                warnung = BreathingWarnung(
                    incident_id=incident.id, troop_id=troop.id, zyklus_start=troop.entry_at,
                    kind=kind, ausgeloest_at=now,
                )
                db.add(warnung)
                db.flush()
                schluessel = set()
                for stufe, delay, gruppe, kanaele, _alarm in ESKALATIONSPLAN[kind]:
                    for user in empfaenger_aufloesen(db, incident, troop, gruppe):
                        for kanal in kanaele:
                            key = (stufe, kanal, user.id)
                            if key in schluessel or (kanal == "sms" and not sms_an):
                                continue
                            schluessel.add(key)
                            grund = None
                            if kanal == "sms":
                                if incident.is_exercise:
                                    grund = "Übung"
                                elif not user.phone:
                                    grund = "keine Telefonnummer"
                            db.add(BreathingZustellung(
                                warnung_id=warnung.id, stufe=stufe, kanal=kanal,
                                user_id=user.id, empfaenger_label=user.display_name,
                                status="suppressed" if grund else "pending", fehler=grund,
                                naechster_versuch_at=now + timedelta(seconds=grace if delay is None else delay),
                            ))
                db.flush()
            neue.append(warnung)
        except IntegrityError:
            logger.info("ASUEW-Warnung bereits vorhanden (Trupp %s, Art %s)", troop.id, kind)
    return neue


def _texte(incident: Incident, troop: BreathingTroop, kind: str, sms: bool) -> tuple[str, str]:
    titel, kurz = TEXTE[kind]
    nummer = incident.lis_operation_number or f"#{incident.id}"
    prefix = "ÜBUNG: " if incident.is_exercise else ""
    # Keine Mitgliederdaten abfragen oder in externe Nachrichten uebernehmen.
    text = f"{prefix}{troop.name}: {kurz} (Einsatz {nummer})"
    return prefix + titel, text[:160] if sms else text


def _push(db: Session, user_id: int, titel: str, text: str, url: str, alarm: bool, extra: dict[str, str]) -> bool:
    return bool(notify_user(
        db, user_id, titel, text, url=url, source="breathing",
        channel_id="einsatz_alarm" if alarm else None, extra_data=extra,
    ))


async def _sms(org_id: int, phone: str, text: str, job_id: str) -> bool:
    kontext = await asyncio.to_thread(resolve_sms_config, org_id)
    return bool(await send_sms(org_id, phone, text, job_id=job_id, ctx=kontext))


def _faellig(now: datetime) -> ColumnElement[bool]:
    return or_(
        and_(BreathingZustellung.status == "pending", BreathingZustellung.naechster_versuch_at <= now),
        and_(BreathingZustellung.status == "claimed", BreathingZustellung.lease_bis <= now),
    )


def _claim_once(db: Session, zeile_id: int, now: datetime) -> bool:
    result = db.execute(update(BreathingZustellung).where(
        BreathingZustellung.id == zeile_id, _faellig(now),
    ).values(status="claimed", lease_bis=now + timedelta(minutes=2)))
    db.commit()
    return getattr(result, "rowcount", 0) == 1


async def liefere_faellige(
    now: datetime | None = None, push_sender: PushSender | None = None,
    sms_sender: SmsSender | None = None, batch: int = 50,
) -> int:
    """Faellige Zustellungen einzeln claimen; Anzahl erfolgreicher Sendungen liefern."""
    now = now if now is not None else datetime.now(UTC).replace(tzinfo=None)
    push_sender = push_sender or _push
    sms_sender = sms_sender or _sms
    ids = await asyncio.to_thread(_lade_faellige_sync, now, batch)
    gesendet = 0
    for zeile_id in ids:
        auftrag = await asyncio.to_thread(_bereite_zustellung_sync, zeile_id, now, push_sender)
        if auftrag is None:
            continue
        erfolge, sms_daten = auftrag
        gesendet += erfolge
        if sms_daten is not None:
            fehler: Exception | None = None
            try:
                if not await sms_sender(*sms_daten):
                    fehler = RuntimeError("Versand fehlgeschlagen")
            except Exception as exc:
                fehler = exc
            erfolge, incident_id = await asyncio.to_thread(
                _beende_zustellung_sync, zeile_id, now, type(fehler).__name__ if fehler is not None else None,
            )
            gesendet += erfolge
            if incident_id is not None:
                assert fehler is not None
                _log_zustellungsfehler(fehler, zeile_id, incident_id)
    return gesendet


def _lade_faellige_sync(now: datetime, batch: int) -> list[int]:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        ids = [row[0] for row in db.query(BreathingZustellung.id).filter(
            _faellig(now),
        ).order_by(BreathingZustellung.naechster_versuch_at, BreathingZustellung.id).limit(max(0, batch)).all()]
        # Lesetransaktion vor den konkurrierenden Einzel-Claims beenden.
        db.commit()
        return ids


def _bereite_zustellung_sync(zeile_id: int, now: datetime, push_sender: PushSender) -> tuple | None:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        if not retry_claim(db, partial(_claim_once, db, zeile_id, now)):
            return None
        zeile = db.get(BreathingZustellung, zeile_id)
        assert zeile is not None
        warnung = db.get(BreathingWarnung, zeile.warnung_id)
        incident = db.get(Incident, warnung.incident_id) if warnung is not None else None
        troop = db.get(BreathingTroop, warnung.troop_id) if warnung is not None else None
        user = db.get(User, zeile.user_id) if zeile.user_id is not None else None
        grund = None
        if warnung is None or warnung.erledigt_at is not None:
            grund = "erledigt"
        elif incident is None or incident.status != "active":
            grund = "Einsatz nicht aktiv"
        elif incident.primary_org_id is None:
            grund = "keine Organisation zugeordnet"
        elif user is None or not user.active or user.org_id != incident.primary_org_id or troop is None:
            grund = "Empfaenger nicht aktiv oder fremde Organisation"
        elif zeile.kanal == "sms":
            if incident.is_exercise:
                grund = "Übung"
            elif not get_eskalation_empfaenger_ids(db, incident.primary_org_id)[1]:
                grund = "SMS deaktiviert"
            elif not user.phone:
                grund = "keine Telefonnummer"
        if grund:
            zeile.status, zeile.fehler, zeile.lease_bis = "suppressed", grund, None
            db.commit()
            return None
        assert warnung is not None and incident is not None and troop is not None and user is not None
        titel, text = _texte(incident, troop, warnung.kind, zeile.kanal == "sms")
        alarm = any(s == zeile.stufe and a for s, _d, _g, _k, a in ESKALATIONSPLAN[warnung.kind])
        extra = {"incident_id": str(incident.id), "asuew": "1", "kind": warnung.kind}
        if incident.is_exercise:
            extra["is_exercise"] = "1"
        fehler: Exception | None = None
        sms_daten = None
        try:
            if zeile.kanal == "push":
                if not push_sender(db, user.id, titel, text, f"/einsatz/{incident.id}/atemschutz", alarm, extra):
                    fehler = RuntimeError("Versand fehlgeschlagen")
            else:
                sms_daten = (incident.primary_org_id, user.phone, text, f"asuew-{zeile.id}")
        except Exception as exc:
            fehler = exc
        if sms_daten is not None:
            return 0, sms_daten
        # Push und Zustellungsstatus bleiben wie bisher in derselben Transaktion.
        erfolge, endgueltig = _beende_zustellung_db(
            db, zeile, warnung, incident, troop, now, type(fehler).__name__ if fehler is not None else None,
        )
        if endgueltig:
            assert fehler is not None
            _log_zustellungsfehler(fehler, zeile.id, incident.id)
        db.commit()
        return erfolge, None


def _beende_zustellung_sync(
    zeile_id: int, now: datetime, fehlerklasse: str | None,
) -> tuple[int, int | None]:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        zeile = db.get(BreathingZustellung, zeile_id)
        assert zeile is not None
        warnung = db.get(BreathingWarnung, zeile.warnung_id)
        assert warnung is not None
        incident = db.get(Incident, warnung.incident_id)
        troop = db.get(BreathingTroop, warnung.troop_id)
        assert incident is not None and troop is not None
        erfolge, endgueltig = _beende_zustellung_db(db, zeile, warnung, incident, troop, now, fehlerklasse)
        incident_id = incident.id if endgueltig else None
        db.commit()
        return erfolge, incident_id


def _beende_zustellung_db(
    db: Session, zeile: BreathingZustellung, warnung: BreathingWarnung,
    incident: Incident, troop: BreathingTroop, now: datetime, fehlerklasse: str | None,
) -> tuple[int, bool]:
    erfolgreich = False
    endgueltig = False
    zeile.lease_bis = None
    if fehlerklasse is None:
        zeile.status, zeile.gesendet_at, zeile.fehler = "sent", now, None
        write_incident_change(
            db, incident.id, "troop.warnung_zugestellt", "breathing_troop", troop.id,
            before=None, after={"kind": warnung.kind, "stufe": zeile.stufe, "kanal": zeile.kanal},
            user_id=None, ts=now,
        )
        erfolgreich = True
    else:
        zeile.versuche += 1
        zeile.fehler = fehlerklasse + ": Versand fehlgeschlagen"
        if zeile.versuche < 4:
            zeile.status = "pending"
            zeile.naechster_versuch_at = now + timedelta(seconds=30 * 2 ** (zeile.versuche - 1))
        else:
            zeile.status = "failed"
            endgueltig = True
    return int(erfolgreich), endgueltig


def _log_zustellungsfehler(fehler: Exception, zeile_id: int, incident_id: int) -> None:
    log_fehler(logger, "ASUEW-Zustellung fehlgeschlagen", fehler,
               level=logging.ERROR, zustellung_id=zeile_id, incident_id=incident_id)


async def breathing_notify_loop() -> None:
    """Faellige ASUEW-Zustellungen alle fuenf Sekunden ausliefern."""
    last_error_logged: float | None = None
    while True:
        try:
            with iteration_watch(logger, "breathing_notify", 5):
                await liefere_faellige()
        except asyncio.CancelledError:
            raise
        except Exception:
            now = monotonic()
            if last_error_logged is None or now - last_error_logged >= 60:
                logger.exception("Atemschutz-Versand: Fehler in der Zustellung")
                last_error_logged = now
        await asyncio.sleep(5)
