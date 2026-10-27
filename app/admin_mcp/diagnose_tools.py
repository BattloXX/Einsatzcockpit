"""Vier begrenzte Diagnosewerkzeuge; ausschliesslich Start schreibt einen Lauf."""
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from app.models.admin_security import utcnow
from app.models.diagnose import DiagFinding, DiagRun
from app.models.user import User
from app.services.diagnose.registry import CHECKS

SEVERITY = ("kritisch", "fail", "warn", "info")


def collect_diagnose(db, name: str, user_id: int, correlation: str, **args) -> dict:
    evidence: dict
    if name == "admin_diagnose_starten":
        umfang = args.get("umfang", "schnell")
        checks = args.get("checks")
        if umfang not in ("schnell", "voll") or (checks is not None and (
                not isinstance(checks, list) or not checks or len(checks) > len(CHECKS)
                or any(not isinstance(c, str) or c not in CHECKS for c in checks))):
            raise ValueError("Ungueltiger Umfang oder Checks")
        # Benutzer-Lock serialisiert das Stundenkontingent; unique active_slot
        # schuetzt zusaetzlich alle Benutzer/Hosts, auch bei leerer Warteschlange.
        db.scalar(select(User.id).where(User.id == user_id).with_for_update())
        active = db.scalar(select(DiagRun.id).where(DiagRun.active_slot == 1).limit(1))
        starts = db.scalar(select(func.count()).select_from(DiagRun).where(
            DiagRun.angefordert_von == user_id, DiagRun.created_at >= utcnow() - timedelta(hours=1))) or 0
        if active is not None or starts >= 6:
            db.rollback()
            return {"status": "warn", "evidence": {"fehler": "Rate-Limit erreicht"}}
        selected = list(dict.fromkeys(checks)) if checks else [c.id for c in CHECKS.values()
            if umfang == "voll" or c.kosten == "leicht"]
        row = DiagRun(checks=selected, umfang={"typ": umfang}, angefordert_von=user_id,
                      quelle="mcp", korrelations_id=correlation, active_slot=1,
                      fortschritt={"gesamt": len(selected), "erledigt": 0})
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return {"status": "warn", "evidence": {"fehler": "Rate-Limit erreicht"}}
        evidence = {"run_id": row.id, "korrelations_id": row.korrelations_id}
    elif name == "admin_diagnose_status":
        run_id = args.get("run_id")
        if run_id is not None and (type(run_id) is not int or run_id < 1):
            raise ValueError("Ungueltige Lauf-ID")
        query = select(DiagRun)
        if run_id is not None:
            query = query.where(DiagRun.id == run_id)
        run = db.scalar(query.order_by(DiagRun.id.desc()).limit(1))
        evidence = {"lauf": None} if run is None else {
            "run_id": run.id, "status": run.status, "fortschritt": run.fortschritt,
            "dauer_s": max(0, ((run.finished_at or utcnow()) - (run.started_at or run.created_at)).total_seconds()),
            "schweregrade": run.fortschritt.get("schweregrade", {}),
            "uebersprungene_checks": run.fortschritt.get("uebersprungen", {}),
            "korrelations_id": run.korrelations_id, "fehlertext": run.fehlertext}
    elif name == "admin_fehler_liste":
        limit = args.get("limit", 50)
        cursor = args.get("cursor")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Ungueltiges Limit")
        query = select(DiagFinding)
        for key, values in (("modul", {c.modul for c in CHECKS.values()}),
                            ("schweregrad", set(SEVERITY)), ("status", {"offen", "behoben", "unterdrueckt"})):
            value = args.get(key)
            if value is not None:
                if value not in values:
                    raise ValueError("Ungueltiger Filter")
                query = query.where(getattr(DiagFinding, key) == value)
        if args.get("seit"):
            try:
                since = datetime.fromisoformat(args["seit"].replace("Z", "+00:00"))
                if since.tzinfo is not None:
                    from datetime import UTC
                    since = since.astimezone(UTC).replace(tzinfo=None)
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError("Ungueltiger Zeitpunkt") from exc
            query = query.where(DiagFinding.last_seen_at >= since)
        rank = case({s: i for i, s in enumerate(SEVERITY)}, value=DiagFinding.schweregrad, else_=4)
        if cursor is not None:
            try:
                rank_value, timestamp, ident = cursor.split("|")  # kein unmaskierter Dateninhalt im Cursor
                seen = datetime.fromisoformat(timestamp)
                rank_value, ident = int(rank_value), int(ident)
                if rank_value not in range(4) or ident < 1 or seen.tzinfo is not None:
                    raise ValueError
            except (ValueError, AttributeError) as exc:
                raise ValueError("Ungueltiger Cursor") from exc
            from sqlalchemy import and_, or_
            query = query.where(or_(rank > rank_value, and_(rank == rank_value, DiagFinding.last_seen_at < seen),
                and_(rank == rank_value, DiagFinding.last_seen_at == seen, DiagFinding.id < ident)))
        rows = list(db.scalars(query.order_by(
            rank, DiagFinding.last_seen_at.desc(), DiagFinding.id.desc()).limit(limit + 1)))
        page = rows[:limit]
        last = page[-1] if page else None
        evidence = {"befunde": [{"id": r.id, "finding_key": r.finding_key, "check_id": r.check_id,
            "modul": r.modul, "schweregrad": r.schweregrad, "status": r.status, "titel": r.titel,
            "last_seen_at": r.last_seen_at.isoformat() + "Z"} for r in page], "cursor":
            f"{SEVERITY.index(last.schweregrad)}|{last.last_seen_at.isoformat()}|{last.id}"
            if len(rows) > limit and last else None}
    else:
        ident, finding_key = args.get("id"), args.get("finding_key")
        if (ident is None) == (finding_key is None) or (ident is not None and (type(ident) is not int or ident < 1)):
            raise ValueError("Genau eine Befund-ID angeben")
        if finding_key is not None and (not isinstance(finding_key, str) or len(finding_key) != 64
                                or any(c not in "0123456789abcdef" for c in finding_key)):
            raise ValueError("Ungueltiger Befundschluessel")
        condition = DiagFinding.id == ident if ident is not None else DiagFinding.finding_key == finding_key
        row = db.scalar(select(DiagFinding).where(condition))
        evidence = {"befund": None} if row is None else {"id": row.id, "finding_key": row.finding_key,
            "check_id": row.check_id, "modul": row.modul, "schweregrad": row.schweregrad,
            "titel": row.titel, "status": row.status, "evidenz": row.evidenz,
            "betroffene_ids": {k: v[:50] for k, v in row.betroffene_ids.items()},
            "hypothese": "Hypothese: " + row.ursache_hypothese.removeprefix("Hypothese: "),
            "empfehlung": row.empfehlung, "first_seen_at": row.first_seen_at.isoformat() + "Z",
            "last_seen_at": row.last_seen_at.isoformat() + "Z", "occurrences": row.occurrences}
    return {"status": "ok", "evidence": evidence}
