"""Begrenzte Wartung: ausschließlich Status/Lease, Suchqueue und verwaiste Uploads."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from datetime import UTC, timedelta
from typing import Annotated, Any

from pydantic import Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models.admin_security import utcnow
from app.models.gateway import GatewayMessage, PrintJob
from app.models.incident import IncidentAlarmJob
from app.models.mcp import MCPUpload
from app.models.sms import SmsDispatchAttempt, SmsDispatchJob, SmsLog, SmsLogRecipient
from app.models.suche import SearchOutbox
from app.services.print_agent_service import MAX_AGENT_ATTEMPTS, VOR_UEBERGABE
from app.services.repair.registry import Ergebnis, RepairContext, Scope, Vorschau, digest, register_action

PositiveId = Annotated[int, Field(gt=0)]
SmsId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
TempName = Annotated[str, Field(pattern=r"^[0-9a-f]{32}\.(pdf|upload)$")]


class JobsScope(Scope):
    org_id: PositiveId
    alarm_ids: list[PositiveId] = Field(default_factory=list, max_length=50)
    sms_ids: list[SmsId] = Field(default_factory=list, max_length=50)
    druck_ids: list[PositiveId] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def begrenzen(self):
        if not 1 <= len(self.alarm_ids) + len(self.sms_ids) + len(self.druck_ids) <= 50:
            raise ValueError("1 bis 50 Jobs erforderlich")
        for ids in (self.alarm_ids, self.sms_ids, self.druck_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("Doppelte IDs")
        return self


class SearchScope(Scope):
    org_id: PositiveId
    ids: list[PositiveId] = Field(min_length=1, max_length=50)


class TempScope(Scope):
    org_id: PositiveId
    dateien: list[TempName] = Field(min_length=1, max_length=50)
    max_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)


def _row(db, model, org_id, ident, lock=False):
    query = (
        select(model)
        .where(model.org_id == org_id, model.id == ident)
        .execution_options(include_all_tenants=True, populate_existing=True)
    )
    return db.scalar(query.with_for_update() if lock else query)


def _control(row, fields):
    return {key: getattr(row, key) for key in fields}


@register_action(
    "outbox.haengende_claims_freigeben",
    "Haengende Claims freigeben",
    "medium",
    JobsScope,
    "Nur abgelaufene, nachweislich noch nicht versandte Jobs erneut einreihen.",
)
class OutboxAction:
    def vorbedingungen(self, db: Session, scope: Scope) -> None:
        assert isinstance(scope, JobsScope)

    def _inspect(self, db, scope, lock=False):
        objects, states = [], []
        for kind, model, ids in (
            ("alarm", IncidentAlarmJob, scope.alarm_ids),
            ("sms", SmsDispatchJob, scope.sms_ids),
            ("druck", PrintJob, scope.druck_ids),
        ):
            for ident in sorted(ids):
                row = _row(db, model, scope.org_id, ident, lock)
                reason = "nicht_gefunden"
                state = None
                if row is not None:
                    if kind == "alarm":
                        state = _control(row, ("status", "lease_until", "attempt_count", "finished_at"))
                        # sending hat keinen Fence vor externer I/O. Auch fehlendes
                        # finished_at beweist keinen ausgebliebenen Versand.
                        reason = "versand_nicht_ausschliessbar"
                        if row.status in ("pending", "retry") and row.attempt_count == 0 and row.finished_at is None:
                            reason = (
                                "ok" if row.lease_until and row.lease_until < utcnow() else "lease_nicht_abgelaufen"
                            )
                    elif kind == "sms":
                        state = _control(
                            row,
                            (
                                "status",
                                "generation",
                                "claim_token",
                                "claimed_by",
                                "lease_until",
                                "may_have_sent",
                                "received_at",
                                "attempted_at",
                                "accepted_at",
                                "delivery_status",
                                "finished_at",
                                "expires_at",
                                "provider",
                                "gateway_id",
                                "connection_id",
                            ),
                        )
                        attempts = list(
                            db.scalars(
                                select(SmsDispatchAttempt)
                                .where(SmsDispatchAttempt.org_id == scope.org_id, SmsDispatchAttempt.job_id == ident)
                                .order_by(SmsDispatchAttempt.generation)
                                .limit(6)
                                .execution_options(include_all_tenants=True)
                            )
                        )
                        state["attempts"] = [
                            _control(
                                a,
                                (
                                    "generation",
                                    "outcome",
                                    "provider",
                                    "gateway_id",
                                    "route_id",
                                    "grant_route_id",
                                    "finished_at",
                                ),
                            )
                            for a in attempts
                        ]
                        routes = db.scalar(
                            select(GatewayMessage.id)
                            .where(GatewayMessage.org_id == scope.org_id, GatewayMessage.job_key.like(f"{ident}:%"))
                            .limit(1)
                        )
                        log = db.scalar(
                            select(SmsLogRecipient.id)
                            .join(SmsLog)
                            .where(
                                SmsLog.org_id == scope.org_id,
                                SmsLogRecipient.dispatch_job_id == ident,
                                (SmsLogRecipient.success.is_(True) | SmsLogRecipient.provider.isnot(None)),
                            )
                            .limit(1)
                        )
                        state.update(transport=routes is not None, protokoll=log is not None)
                        reason = "zustellungsindiz"
                        if (
                            row.status == "claimed"
                            and not row.may_have_sent
                            and row.received_at is None
                            and row.attempted_at is None
                            and row.accepted_at is None
                            and row.finished_at is None
                            and row.delivery_status == "unverified"
                            and row.provider is None
                            and row.gateway_id is None
                            and row.connection_id is None
                            and routes is None
                            and log is None
                            and 0 < row.generation < 5
                            and len(attempts) == row.generation
                            and all(
                                a.outcome in ("claimed", "not_dispatched")
                                and a.provider is None
                                and a.gateway_id is None
                                and not a.route_id
                                and not a.grant_route_id
                                for a in attempts
                            )
                            and attempts[-1].generation == row.generation
                            and attempts[-1].outcome == "claimed"
                            and attempts[-1].finished_at is None
                        ):
                            reason = (
                                "ok"
                                if row.lease_until and row.lease_until < utcnow() and row.expires_at > utcnow()
                                else "lease_oder_versandfrist"
                            )
                    else:
                        state = _control(
                            row,
                            (
                                "status",
                                "delivery",
                                "phase",
                                "spool_ref",
                                "lease_generation",
                                "lease_token",
                                "lease_gateway_id",
                                "lease_expires_at",
                                "attempts",
                                "agent_contact_at",
                                "push_route_id",
                                "idempotency_key",
                            ),
                        )
                        reason = "zustellungsindiz"
                        if (
                            row.status == "sent"
                            and row.delivery == "lease"
                            and row.phase in VOR_UEBERGABE
                            and not row.spool_ref
                            and not row.push_route_id
                            and row.idempotency_key
                            and 0 < row.attempts < MAX_AGENT_ATTEMPTS
                            and row.lease_token
                        ):
                            reason = (
                                "ok"
                                if row.lease_expires_at and row.lease_expires_at < utcnow()
                                else "lease_nicht_abgelaufen"
                            )
                objects.append({"typ": kind, "id": ident, "ausfuehrbar": reason == "ok", "grund": reason})
                states.append(state)
        return Vorschau(digest(states), objects)

    def simulieren(self, db: Session, scope: Scope) -> Vorschau:
        assert isinstance(scope, JobsScope)
        return self._inspect(db, scope)

    def ausfuehren(self, db: Session, scope: Scope, ctx: RepairContext) -> Ergebnis:
        assert isinstance(scope, JobsScope)
        preview = self._inspect(db, scope, True)
        if ctx.daten_hash and preview.daten_hash != ctx.daten_hash:
            raise ValueError("Datenbestand geaendert; neu simulieren")
        changed = 0
        for item in preview.objekte:
            if not item["ausfuehrbar"]:
                continue
            kind, ident = item["typ"], item["id"]
            model: Any = {"alarm": IncidentAlarmJob, "sms": SmsDispatchJob, "druck": PrintJob}[kind]
            row = _row(db, model, scope.org_id, ident, True)
            values: dict = {"status": "queued", "lease_until": None}
            conditions = [model.org_id == scope.org_id, model.id == ident, model.status == row.status]
            if kind == "alarm":
                values.update(status="pending", next_attempt_at=utcnow())
                conditions += [
                    IncidentAlarmJob.attempt_count == 0,
                    IncidentAlarmJob.finished_at.is_(None),
                    IncidentAlarmJob.lease_until == row.lease_until,
                ]
            elif kind == "sms":
                values.update(claim_token=None, claimed_by=None, next_attempt_at=utcnow())
                conditions += [
                    SmsDispatchJob.generation == row.generation,
                    SmsDispatchJob.claim_token == row.claim_token,
                    SmsDispatchJob.may_have_sent.is_(False),
                ]
                result = db.execute(update(model).where(*conditions).values(**values))
                if getattr(result, "rowcount", 0) == 1:
                    db.execute(
                        update(SmsDispatchAttempt)
                        .where(
                            SmsDispatchAttempt.org_id == scope.org_id,
                            SmsDispatchAttempt.job_id == ident,
                            SmsDispatchAttempt.generation == row.generation,
                            SmsDispatchAttempt.outcome == "claimed",
                        )
                        .values(outcome="not_dispatched", finished_at=utcnow())
                    )
                    changed += 1
                continue
            else:
                values = {
                    "status": "queued",
                    "phase": None,
                    "lease_token": None,
                    "lease_gateway_id": None,
                    "lease_expires_at": None,
                    "lease_generation": row.lease_generation + 1,
                    "queued_at": utcnow(),
                }
                conditions += [
                    PrintJob.lease_generation == row.lease_generation,
                    PrintJob.lease_token == row.lease_token,
                    PrintJob.phase == row.phase,
                    PrintJob.spool_ref.is_(None),
                    PrintJob.lease_expires_at == row.lease_expires_at,
                ]
            result = db.execute(update(model).where(*conditions).values(**values))
            changed += getattr(result, "rowcount", 0)
        return Ergebnis(changed, len(preview.objekte) - changed)

    def verifizieren(self, db: Session, scope: Scope) -> Ergebnis:
        assert isinstance(scope, JobsScope)
        p = self._inspect(db, scope)
        return Ergebnis(0, sum(x["ausfuehrbar"] for x in p.objekte))


@register_action(
    "suche.rueckstand_anstossen",
    "Such-Rueckstand anstossen",
    "low",
    SearchScope,
    "Begrenzte bestehende Suchauftraege fuer den vorhandenen Indexworker faellig stellen.",
)
class SearchAction:
    def vorbedingungen(self, db: Session, scope: Scope) -> None:
        from app.models.master import OrgSettings

        assert isinstance(scope, SearchScope)
        if not settings.SEARCH_INDEX_WORKER_ENABLED or not db.scalar(
            select(OrgSettings.suche_aktiv).where(OrgSettings.org_id == scope.org_id)
        ):
            raise ValueError("Suchindexierung nicht aktiviert")

    def simulieren(self, db: Session, scope: Scope) -> Vorschau:
        assert isinstance(scope, SearchScope)
        objects, states = [], []
        for ident in sorted(set(scope.ids)):
            row = _row(db, SearchOutbox, scope.org_id, ident)
            safe = bool(
                row
                and row.status in ("pending", "retry")
                and row.versuche < 8
                and (row.gesperrt_bis is None or row.gesperrt_bis < utcnow())
                and row.naechster_versuch_am > utcnow()
            )
            objects.append({"id": ident, "ausfuehrbar": safe, "grund": "ok" if safe else "nicht_anstossbar"})
            states.append(
                _control(row, ("status", "versuche", "gesperrt_bis", "naechster_versuch_am")) if row else None
            )
        return Vorschau(digest(states), objects)

    def ausfuehren(self, db: Session, scope: Scope, ctx: RepairContext) -> Ergebnis:
        from app.services.suche.outbox import stosse_rueckstand_an

        assert isinstance(scope, SearchScope)
        for ident in sorted(set(scope.ids)):
            _row(db, SearchOutbox, scope.org_id, ident, True)
        if ctx.daten_hash and self.simulieren(db, scope).daten_hash != ctx.daten_hash:
            raise ValueError("Datenbestand geaendert; neu simulieren")
        count = stosse_rueckstand_an(db, scope.org_id, scope.ids)
        return Ergebnis(count, len(set(scope.ids)) - count)

    def verifizieren(self, db: Session, scope: Scope) -> Ergebnis:
        p = self.simulieren(db, scope)
        return Ergebnis(0, sum(x["ausfuehrbar"] for x in p.objekte))


@contextmanager
def temp_directory(org_id: int):
    """Dir-FDs verhindern Symlink-Wechsel sämtlicher unzuverlässiger Pfadteile."""
    base = os.open(os.path.realpath(settings.OBJEKT_MEDIA_DIR), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    root = org = None
    try:
        root = os.open("_mcp_uploads", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
        org = os.open(str(org_id), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        yield org
    finally:
        if org is not None:
            os.close(org)
        if root is not None:
            os.close(root)
        os.close(base)


def orphan_state(db, scope, name, directory):
    # Auch noch nicht abgeschlossene Uploads sowie widersprüchliche
    # organisationsfremde Verweise schützen: im Zweifel niemals löschen.
    linked = db.scalar(
        select(MCPUpload.id)
        .where(
            (MCPUpload.upload_id == name.split(".")[0]) | (MCPUpload.pfad == f"_mcp_uploads/{scope.org_id}/{name}"),
        )
        .limit(1).execution_options(include_all_tenants=True)
    )
    if linked is not None:
        return None, "db_referenz"
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None, "nicht_gefunden"
    state = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode]
    if not stat.S_ISREG(info.st_mode):
        return state, "keine_regulaere_datei"
    if info.st_mtime >= (utcnow() - timedelta(hours=24)).replace(tzinfo=UTC).timestamp():
        return state, "juenger_als_24h"
    return state, "ok"


@register_action(
    "dateien.verwaiste_temp_loeschen",
    "Verwaiste Upload-Dateien loeschen",
    "low",
    TempScope,
    "Nur referenzlose MCP-Tempdateien aelter als 24 Stunden, maximal 50 Dateien/100 MiB.",
)
class TempAction:
    def vorbedingungen(self, db: Session, scope: Scope) -> None:
        assert isinstance(scope, TempScope)
        if len(scope.dateien) != len(set(scope.dateien)):
            raise ValueError("Doppelte Dateien")

    def _snapshot(self, db: Session, scope: TempScope) -> tuple[Vorschau, dict]:
        objects, states, total = [], [], 0
        try:
            with temp_directory(scope.org_id) as directory:
                for name in sorted(scope.dateien):
                    state, reason = orphan_state(db, scope, name, directory)
                    if reason == "ok":
                        if total + state[2] > scope.max_bytes:
                            reason = "groessenlimit"
                        else:
                            total += state[2]
                    objects.append({"id": name.split(".")[0], "ausfuehrbar": reason == "ok", "grund": reason})
                    states.append(state)
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise ValueError("Tempverzeichnis fehlt oder ist unzulaessig") from exc
        return Vorschau(digest(states), objects), dict(zip(sorted(scope.dateien), states, strict=True))

    def simulieren(self, db: Session, scope: Scope) -> Vorschau:
        assert isinstance(scope, TempScope)
        return self._snapshot(db, scope)[0]

    def ausfuehren(self, db: Session, scope: Scope, ctx: RepairContext) -> Ergebnis:
        assert isinstance(scope, TempScope)
        preview, versions = self._snapshot(db, scope)
        if ctx.daten_hash and preview.daten_hash != ctx.daten_hash:
            raise ValueError("Datenbestand geaendert; neu simulieren")
        count = total = 0
        with temp_directory(scope.org_id) as directory:
            for name, item in zip(sorted(scope.dateien), preview.objekte, strict=True):
                if not item["ausfuehrbar"]:
                    continue
                state, reason = orphan_state(db, scope, name, directory)
                if reason != "ok" or state != versions[name] or total + state[2] > scope.max_bytes:
                    continue
                # Nie einem Symlink folgen, auch nach Austausch des Eintrags.
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    opened = os.fstat(fd)
                    current, reason = orphan_state(db, scope, name, directory)
                    if (
                        reason != "ok"
                        or current != state
                        or opened.st_ino != state[1]
                        or opened.st_dev != state[0]
                        or not stat.S_ISREG(opened.st_mode)
                    ):
                        continue
                    os.unlink(name, dir_fd=directory)
                    count += 1
                    total += state[2]
                    ctx.progress(Ergebnis(count, len(scope.dateien) - count))
                finally:
                    os.close(fd)
        return Ergebnis(count, len(scope.dateien) - count)

    def verifizieren(self, db: Session, scope: Scope) -> Ergebnis:
        p = self.simulieren(db, scope)
        return Ergebnis(0, sum(x["ausfuehrbar"] for x in p.objekte))
