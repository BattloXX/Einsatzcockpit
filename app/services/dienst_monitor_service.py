"""Pruefungen und persistenter Zustandsautomat der Dienstueberwachung."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.dienst_monitor import DienstStatus


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


@dataclass(frozen=True)
class DienstTeil:
    """Ein einzelnes Geraet eines Dienstes (ein Gateway, eine SMS-Verbindung)."""

    ref: str
    name: str
    state: str
    detail: str


@dataclass(frozen=True)
class DienstCheck:
    key: str
    state: str
    detail: str
    relevant: bool
    teile: tuple[DienstTeil, ...] = ()


@dataclass(frozen=True)
class Entscheidung:
    art: str | None


def _gateway_frisch(gateway, now: datetime) -> bool:
    config = gateway.wut_config or {}
    try:
        interval = max(15, int(config.get("health_interval_s", 60)))
    except (TypeError, ValueError):
        interval = 60
    last_seen = _naive_utc(gateway.last_seen_at)
    return bool(last_seen and now - last_seen <= timedelta(seconds=max(180, 3 * interval)))


def aggregiere(teile: tuple[DienstTeil, ...]) -> str:
    """Dienstzustand aus den Einzelgeraeten: ok | teilweise | down | unknown."""
    if not teile:
        return "unknown"
    down = [t for t in teile if t.state == "down"]
    if not down:
        return "ok" if any(t.state == "ok" for t in teile) else "unknown"
    return "down" if len(down) == len(teile) else "teilweise"


def pruefe_dienste(db: Session, org_id: int, now: datetime | None = None) -> list[DienstCheck]:
    from app.core.timezones import format_local_datetime
    from app.models.dibos import OrgDibosConfig
    from app.models.gateway import Gateway
    from app.models.master import FireDept
    from app.models.user import SmsGatewayToken
    from app.routers.ws import connected_gateway_token_ids
    from app.services.sms_service import resolve_sms_config

    jetzt = _naive_utc(now or datetime.now(UTC))
    assert jetzt is not None
    org = db.get(FireDept, org_id)
    gateways = db.query(Gateway).filter(Gateway.org_id == org_id).execution_options(include_all_tenants=True).all()
    paired = [g for g in gateways if g.device_token_hash is not None]
    if not paired:
        print_check = DienstCheck("print_gateway", "unknown", "Kein Print-Gateway eingerichtet.", False)
    else:
        print_teile = tuple(
            DienstTeil(
                ref=f"gateway:{g.id}",
                name=g.name,
                state="ok" if _gateway_frisch(g, jetzt) else "down",
                detail=(
                    "Frischer Heartbeat."
                    if _gateway_frisch(g, jetzt)
                    else f"Kein frischer Heartbeat ({g.standort or 'ohne Standort'}, zuletzt "
                    f"{format_local_datetime(g.last_seen_at, org) or 'nie'})."
                ),
            )
            for g in paired
        )
        print_state = aggregiere(print_teile)
        print_down = [t for t in print_teile if t.state == "down"]
        if print_state == "ok":
            print_detail = "Alle Print-Gateways melden frische Heartbeats."
        elif print_state == "down":
            namen = ", ".join(t.name for t in print_down)
            print_detail = f"Kein Print-Gateway meldet einen frischen Heartbeat. Betroffen: {namen}."
        else:
            namen = ", ".join(t.name for t in print_down)
            print_detail = (
                f"{len(print_down)} von {len(print_teile)} Print-Gateways ohne frischen Heartbeat: {namen}."
            )
        print_check = DienstCheck("print_gateway", print_state, print_detail, True, print_teile)

    tokens = (
        db.query(SmsGatewayToken).filter(SmsGatewayToken.org_id == org_id, SmsGatewayToken.revoked_at.is_(None)).all()
    )
    ctx = resolve_sms_config(org_id, db)
    eus = "eus" in ctx.chain and ctx.eus is not None
    sms_relevant = bool(tokens or eus)
    if not sms_relevant:
        sms_check = DienstCheck("sms_gateway", "unknown", "Kein SMS-Dienst eingerichtet.", False)
    else:
        connected = connected_gateway_token_ids(org_id)
        sms_teile_liste = []
        for token in tokens:
            heartbeat = _naive_utc(token.last_heartbeat_at)
            frisch = heartbeat is not None and jetzt - heartbeat <= timedelta(minutes=10)
            if token.id in connected or frisch:
                state, detail = "ok", "Verbunden oder Heartbeat innerhalb der letzten 10 Minuten."
            elif heartbeat is None:
                state, detail = "unknown", "Noch nie einen Heartbeat gesendet."
            else:
                state, detail = "down", "Kein Heartbeat innerhalb der letzten 10 Minuten."
            sms_teile_liste.append(DienstTeil(f"sms:{token.id}", token.label, state, detail))
        if eus:
            sms_teile_liste.append(
                DienstTeil("eus", "EUS", "ok", "EUS ist konfiguriert (Erreichbarkeit nicht aktiv geprueft).")
            )
        sms_teile = tuple(sms_teile_liste)
        sms_state = aggregiere(sms_teile)
        sms_down = [t for t in sms_teile if t.state == "down"]
        if sms_state == "ok":
            sms_detail = (
                "EUS ist konfiguriert (Erreichbarkeit nicht aktiv geprueft)."
                if eus
                else "SMS-Gateway ist verbunden oder hat kuerzlich einen Heartbeat gesendet."
            )
        elif sms_state == "unknown":
            sms_detail = "Die vorhandenen SMS-Gateways haben noch nie einen Heartbeat gesendet."
        elif sms_state == "down":
            namen = ", ".join(t.name for t in sms_down)
            sms_detail = f"Kein SMS-Gateway ist erreichbar. Betroffen: {namen}."
        else:
            namen = ", ".join(t.name for t in sms_down)
            sms_detail = f"{len(sms_down)} von {len(sms_teile)} SMS-Gateways nicht erreichbar: {namen}."
        sms_check = DienstCheck("sms_gateway", sms_state, sms_detail, True, sms_teile)

    wut = [g for g in paired if (g.wut_config or {}).get("host")]
    if not wut:
        serial = DienstCheck("alarm_seriell", "unknown", "Kein serieller W&T-Alarm eingerichtet.", False)
    else:
        serial_teile = tuple(
            DienstTeil(
                f"gateway:{g.id}",
                g.name,
                "unknown" if not _gateway_frisch(g, jetzt) else ("down" if g.serial_connected is False else "ok"),
                (
                    "Gateway-Heartbeat veraltet; serieller Status nicht beurteilbar."
                    if not _gateway_frisch(g, jetzt)
                    else (
                        "Serielle Verbindung getrennt."
                        if g.serial_connected is False
                        else "Serielle Verbindung aktiv."
                    )
                ),
            )
            for g in wut
        )
        serial_state = aggregiere(serial_teile)
        serial_down = [t for t in serial_teile if t.state == "down"]
        if serial_state == "ok":
            serial_detail = "Alle eingerichteten W&T-Verbindungen sind aktiv."
        elif serial_state == "unknown":
            serial_detail = "Gateway-Heartbeat veraltet; serieller Status nicht beurteilbar."
        elif serial_state == "down":
            namen = ", ".join(t.name for t in serial_down)
            serial_detail = f"Keine eingerichtete W&T-Verbindung ist aktiv. Betroffen: {namen}."
        else:
            namen = ", ".join(t.name for t in serial_down)
            serial_detail = (
                f"{len(serial_down)} von {len(serial_teile)} W&T-Verbindungen getrennt: {namen}."
            )
        serial = DienstCheck("alarm_seriell", serial_state, serial_detail, True, serial_teile)

    cfg = db.query(OrgDibosConfig).filter(OrgDibosConfig.org_id == org_id).first()
    dibos_relevant = bool(
        cfg
        and cfg.enabled
        and cfg.is_fully_configured
        and (cfg.create_incidents or cfg.enrich_incidents or cfg.auto_trace_on_event)
    )
    probe = (
        db.query(DienstStatus)
        .filter(DienstStatus.org_id == org_id, DienstStatus.key == "alarm_dibos")
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not dibos_relevant:
        dibos = DienstCheck("alarm_dibos", "unknown", "DIBOS-Poll ist nicht eingerichtet.", False)
    else:
        if not probe or probe.last_probe_at is None:
            dibos_state, dibos_detail = "unknown", "DIBOS wurde noch nicht geprueft."
        elif probe.last_probe_ok is False:
            dibos_state, dibos_detail = "down", probe.last_probe_error or "DIBOS-Poll fehlgeschlagen."
        elif jetzt - (_naive_utc(probe.last_probe_at) or jetzt) > timedelta(
            seconds=max(180, 6 * (cfg.poll_interval_seconds if cfg else 5))
        ):
            dibos_state, dibos_detail = "down", "Kein DIBOS-Poll innerhalb des erwarteten Intervalls."
        else:
            dibos_state, dibos_detail = "ok", "DIBOS-Poll erfolgreich."
        dibos_teile = (DienstTeil("dibos", "DIBOS-Poll", dibos_state, dibos_detail),)
        dibos = DienstCheck("alarm_dibos", aggregiere(dibos_teile), dibos_detail, True, dibos_teile)
    return [print_check, sms_check, serial, dibos]


def entscheide(
    check: DienstCheck, row: DienstStatus, karenz_min: int, wiederholung_min: int, now: datetime
) -> Entscheidung:
    jetzt = _naive_utc(now)
    assert jetzt is not None
    if not check.relevant or check.state == "unknown":
        return Entscheidung(None)
    if check.state in ("down", "teilweise"):
        if row.down_since is None:
            row.down_since = jetzt
        row.fail_cycles = (row.fail_cycles or 0) + 1
        row.ok_cycles = 0
        down_since = _naive_utc(row.down_since) or jetzt
        wiederholung_faellig = row.outage_notified_at is None or (
            row.last_repeat_at is not None
            and jetzt - (_naive_utc(row.last_repeat_at) or jetzt) >= timedelta(minutes=wiederholung_min)
        )
        if jetzt - down_since >= timedelta(minutes=karenz_min) and row.fail_cycles >= 2 and wiederholung_faellig:
            return Entscheidung("stoerung")
        return Entscheidung(None)
    row.ok_cycles = (row.ok_cycles or 0) + 1
    row.fail_cycles = 0
    if row.outage_notified_at is not None:
        return Entscheidung("entwarnung")
    row.down_since = None
    row.last_repeat_at = None
    return Entscheidung(None)


def bestaetigt_down(row: DienstStatus | None, karenz_min: int, now: datetime) -> bool:
    """Ausfall gilt als bestaetigt, sobald die Karenzzeit abgelaufen ist.

    Bewusst unabhaengig davon, ob eine Benachrichtigung hinausging: eine Org ohne
    konfigurierte Empfaenger soll im Uptime-Monitor trotzdem 503 melden.
    """
    if row is None or row.down_since is None:
        return False
    down_since = _naive_utc(row.down_since)
    jetzt = _naive_utc(now)
    if down_since is None or jetzt is None:
        return False
    return jetzt - down_since >= timedelta(minutes=karenz_min)


def dienst_zustand(check: DienstCheck, row: DienstStatus | None, karenz_min: int, now: datetime) -> str:
    """nicht_konfiguriert | ok | teilweise | down -- einzige Quelle fuer UI und Uptime-API."""
    if not check.relevant:
        return "nicht_konfiguriert"
    if not bestaetigt_down(row, karenz_min, now):
        return "ok"
    return "teilweise" if check.state == "teilweise" else "down"


def claim_meldung(db: Session, row: DienstStatus, org_id: int, art: str, now: datetime, wiederholung_min: int) -> bool:
    jetzt = _naive_utc(now)
    assert jetzt is not None
    stmt = update(DienstStatus).where(DienstStatus.id == row.id, DienstStatus.org_id == org_id)
    if art == "stoerung":
        cutoff = jetzt - timedelta(minutes=wiederholung_min)
        stmt = stmt.where((DienstStatus.outage_notified_at.is_(None)) | (DienstStatus.last_repeat_at <= cutoff)).values(
            outage_notified_at=jetzt, last_repeat_at=jetzt
        )
    else:
        stmt = stmt.where(DienstStatus.outage_notified_at.is_not(None)).values(outage_notified_at=None)
    result = db.execute(stmt.execution_options(synchronize_session=False))
    db.commit()
    return getattr(result, "rowcount", 0) == 1


def rollback_claim(
    db: Session,
    row_id: int,
    org_id: int,
    art: str,
    now: datetime,
    vorher_outage: datetime | None,
    vorher_repeat: datetime | None,
) -> None:
    jetzt = _naive_utc(now)
    stmt = update(DienstStatus).where(DienstStatus.id == row_id, DienstStatus.org_id == org_id)
    if art == "stoerung":
        stmt = stmt.where(DienstStatus.last_repeat_at == jetzt).values(
            outage_notified_at=vorher_outage, last_repeat_at=vorher_repeat
        )
    else:
        stmt = stmt.where(DienstStatus.outage_notified_at.is_(None)).values(outage_notified_at=vorher_outage)
    db.execute(stmt.execution_options(synchronize_session=False))
    db.commit()


def record_probe(org_id: int, key: str, ok: bool, fehler: str | None = None) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        row = (
            db.query(DienstStatus)
            .filter(DienstStatus.org_id == org_id, DienstStatus.key == key)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if row is None:
            row = DienstStatus(org_id=org_id, key=key, state="unknown")
            db.add(row)
        elif (
            row.last_probe_ok == ok
            and row.last_probe_error == (fehler or None)
            and row.last_probe_at
            and now - (_naive_utc(row.last_probe_at) or now) <= timedelta(seconds=60)
        ):
            return
        row.last_probe_at = now
        row.last_probe_ok = ok
        row.last_probe_error = fehler[:500] if fehler else None
        db.commit()
    finally:
        db.close()
