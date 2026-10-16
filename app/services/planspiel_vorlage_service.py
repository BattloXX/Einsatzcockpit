"""Speichern und Verwenden wiederverwendbarer Planspiel-Vorlagen."""

from copy import deepcopy
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.planspiel import PlanspielRun, PlanspielVorlage
from app.services.planspiel_ort_service import GebietFehlt, OrtNichtGefunden
from app.services.planspiel_run_service import EntwurfDaten, entwurf_anlegen


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def aus_run_speichern(
    db: Session, run: PlanspielRun, user: Any, *, titel: str, beschreibung: str | None, gleicher_ablauf: bool
) -> PlanspielVorlage:
    titel = titel.strip()
    if not titel:
        raise ValueError("Bitte einen Titel für die Vorlage angeben")
    if len(titel) > 200:
        raise ValueError("Der Titel darf höchstens 200 Zeichen lang sein")
    if run.org_id is None:
        raise ValueError("Planspiel hat keine Organisation")
    konfig = deepcopy(run.konfig or {})
    konfig.pop("seed", None)
    konfig.pop("leiter_user_id", None)
    vorlage = PlanspielVorlage(
        org_id=run.org_id, titel=titel, beschreibung=(beschreibung or "").strip() or None,
        art=run.art, konfig=konfig, seed_fixiert=run.seed if gleicher_ablauf else None,
        quelle_run_id=run.id, created_by_user_id=user.id,
    )
    db.add(vorlage)
    db.flush()
    write_audit(db, "planspiel.vorlage.angelegt", org_id=run.org_id, user_id=user.id,
                entity_type="planspiel_vorlage", entity_id=vorlage.id)
    return vorlage


def entwurf_aus_vorlage(db: Session, vorlage: PlanspielVorlage, user: Any) -> PlanspielRun:
    if vorlage.org_id is None:
        raise ValueError("Vorlage hat keine Organisation")
    erlaubte_felder = {field.name for field in fields(EntwurfDaten)}
    werte = {key: deepcopy(value) for key, value in (vorlage.konfig or {}).items() if key in erlaubte_felder}
    werte.update(titel=vorlage.titel, art=vorlage.art, seed=vorlage.seed_fixiert, leiter_user_id=user.id)
    daten = EntwurfDaten(**werte)
    try:
        run = entwurf_anlegen(db, vorlage.org_id, user, daten)
    except (OrtNichtGefunden, GebietFehlt):
        raise
    except ValueError as exc:
        if daten.szenario_id is not None and "Szenario ist nicht aktiv" in str(exc):
            raise ValueError("Das Szenario der Vorlage ist nicht mehr aktiv oder nicht mehr verfügbar") from exc
        raise
    vorlage.verwendet_anzahl += 1
    vorlage.zuletzt_verwendet_at = _jetzt()
    write_audit(db, "planspiel.vorlage.verwendet", org_id=vorlage.org_id, user_id=user.id,
                entity_type="planspiel_vorlage", entity_id=vorlage.id, payload={"run_id": run.id})
    return run


def loeschen(db: Session, vorlage: PlanspielVorlage, user: Any) -> None:
    write_audit(db, "planspiel.vorlage.geloescht", org_id=vorlage.org_id, user_id=user.id,
                entity_type="planspiel_vorlage", entity_id=vorlage.id)
    db.delete(vorlage)


def vorlage_laden(db: Session, org_id: int, vorlage_id: int) -> PlanspielVorlage | None:
    return db.query(PlanspielVorlage).filter(
        PlanspielVorlage.id == vorlage_id, PlanspielVorlage.org_id == org_id,
    ).one_or_none()
