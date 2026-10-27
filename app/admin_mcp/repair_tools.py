"""Fünf begrenzte Reparaturwerkzeuge; ausschließlich feste Aktions-IDs."""

from uuid import uuid4

from sqlalchemy import select

from app.models.admin_repair import AdminRepairRun
from app.models.admin_security import utcnow
from app.models.diagnose import DiagFinding
from app.services.repair.registry import ACTIONS, digest, simulate
from app.services.repair.runner import (
    audit,
    execute,
    gates,
    get_run,
    public_run,
    rate_limit,
    requirement,
)

FINDING_ACTIONS = {
    "gateway.jobs": ("outbox.haengende_claims_freigeben",),
    "suche.rueckstand": ("suche.rueckstand_anstossen",),
}


def collect_repair(
    db, name: str, user_id: int, epoch: int, correlation: str, authorize=None, web=False, **args
) -> dict:
    run_id = args.get("run_id")
    try:
        gates(db)
        rate_limit(db, user_id, correlation)
        if name == "admin_reparatur_vorschlagen":
            ident, key = args.get("id"), args.get("finding_key")
            if (ident is None) == (key is None):
                raise ValueError("Genau eine Befund-ID angeben")
            finding = db.scalar(
                select(DiagFinding).where(
                    DiagFinding.id == ident if ident is not None else DiagFinding.finding_key == key
                )
            )
            if finding is None:
                raise ValueError("Befund nicht gefunden")
            runs = []
            for action_id in FINDING_ACTIONS.get(finding.check_id, ()):
                action = ACTIONS[action_id]
                row = AdminRepairRun(
                    aktion_id=action_id,
                    risiko=action.risiko,
                    diag_finding_id=finding.id,
                    scope={},
                    angefordert_von=user_id,
                    korrelations_id=("web:" + uuid4().hex) if web else str(uuid4()),
                )
                db.add(row)
                db.flush()
                audit(db, "vorgeschlagen", user_id, correlation, row.id)
                runs.append(
                    {
                        "run_id": row.id,
                        "aktion_id": action_id,
                        "risiko": action.risiko,
                        "scope_schema": action.scope_schema.model_json_schema(),
                    }
                )
            db.commit()
            return {"status": "ok", "evidence": {"vorschlaege": runs}}
        if name == "admin_reparatur_simulieren":
            if run_id is not None:
                run = get_run(db, run_id, user_id)
                if run.status not in ("vorgeschlagen", "simuliert", "freigegeben"):
                    raise ValueError("Lauf nicht mehr simulierbar")
                if args.get("aktion_id") is not None:
                    raise ValueError("Nur run_id oder aktion_id angeben")
                action = ACTIONS[run.aktion_id]
                raw_scope = args.get("scope") if args.get("scope") is not None else run.scope
            else:
                action_id = args.get("aktion_id", "")
                if action_id not in ACTIONS:
                    raise ValueError("Unbekannte Aktion")
                action = ACTIONS[action_id]
                raw_scope = args.get("scope")
                run = None
            scope = action.scope_schema.model_validate(raw_scope)
            # Kein Run und kein Audit wird innerhalb des schreibgesperrten Dry-Runs verändert.
            preview = simulate(db, action, scope)
            if run is None:
                run = AdminRepairRun(
                    aktion_id=action.id, risiko=action.risiko, angefordert_von=user_id, korrelations_id=correlation
                )
                db.add(run)
            run.scope, run.vorschau = scope.model_dump(), preview.public()
            run.simulation_hash = digest([action.id, run.scope, run.vorschau])
            run.status, run.simulated_at = "simuliert", utcnow()
            db.flush()
            audit(db, "simuliert", user_id, correlation, run.id)
            answer = {**public_run(run), "benoetigte_freigabe": requirement(db, action)}
            db.commit()
            return {"status": "ok", "evidence": answer}
        if type(run_id) is not int or run_id < 1:
            raise ValueError("Gueltige Lauf-ID erforderlich")
        if name == "admin_reparatur_ausfuehren":
            answer = execute(
                db,
                run_id,
                user_id,
                epoch,
                correlation,
                args.get("simulation_hash", ""),
                args.get("idempotency_key", ""),
                args.get("bestaetigt", False),
                args.get("approval_token"),
                authorize,
                web=web,
            )
            return {"status": "ok", "evidence": answer}
        run = get_run(db, run_id, user_id)
        if name == "admin_reparatur_verifizieren":
            action = ACTIONS[run.aktion_id]
            scope = action.scope_schema.model_validate(run.scope)
            from app.services.repair.registry import readonly

            with readonly(db) as reader:
                result = action.impl.verifizieren(reader, scope)
            run.verifikation = result.public()
            audit(db, "verifiziert", user_id, correlation, run.id)
            db.commit()
            if args.get("folgediagnose") and run.diag_finding_id:
                from app.admin_mcp.diagnose_tools import collect_diagnose

                finding = db.get(DiagFinding, run.diag_finding_id)
                if finding is not None:
                    follow = collect_diagnose(
                        db, "admin_diagnose_starten", user_id, correlation, checks=[finding.check_id], umfang="voll"
                    )
                    return {"status": "ok", "evidence": {**public_run(run), "folgediagnose": follow["evidence"]}}
        return {"status": "ok", "evidence": public_run(run)}
    except ValueError, KeyError, PermissionError:
        db.rollback()
        # Ausschließlich kontrollierte Gründe ausgeben; Pydantic-Fehler enthalten Rohargumente.
        import sys

        exc = sys.exception()
        reason = str(exc) if type(exc) is ValueError else "Ungueltige Parameter oder Simulation gesperrt"
        audit(db, "abgelehnt", user_id, correlation, run_id, reason)
        db.commit()
        return {"status": "warn", "evidence": {"fehler": reason}}

    except Exception:
        db.rollback()
        audit(db, "fehlgeschlagen", user_id, correlation, run_id, "interner_fehler")
        db.commit()
        return {"status": "fail", "evidence": {"fehler": "Reparatur fehlgeschlagen; Status pruefen"}}
