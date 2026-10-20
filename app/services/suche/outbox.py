"""Transaktionale Aenderungserfassung und Leader-Worker fuer den Suchindex."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Table, delete, event, insert, inspect, or_, select, tuple_, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.katplan import KatplanDokument, KatplanDokumentSeite, KatplanPlan, KatplanVersion
from app.models.master import OrgSettings
from app.models.objekt import (
    Objekt,
    ObjektBMA,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektGefahr,
    ObjektMerkmal,
    ObjektZusatzadresse,
)
from app.models.suche import SearchDocument, SearchOutbox
from app.services.suche.indexer import indexiere, neuaufbau
from app.services.suche.quellen import ADAPTER, adapter_fuer, lade_adapter

logger = logging.getLogger(__name__)
_OFFEN = ("pending", "retry")


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def plane_neuaufbau(db: Session, org_id: int) -> int:
    """Plant pro Indexquelle einen Auftrag, ohne die Transaktion zu committen."""
    lade_adapter()
    db.flush()
    vorhanden = set(db.scalars(select(SearchOutbox.quell_id).where(
        SearchOutbox.org_id == org_id, SearchOutbox.quelle == "neuaufbau",
        SearchOutbox.status.in_(_OFFEN),
    )))
    jetzt = _jetzt()
    quellen = set(ADAPTER) - vorhanden
    for quelle in sorted(quellen):
        db.add(SearchOutbox(org_id=org_id, quelle="neuaufbau", quell_id=quelle,
                           status="pending", versuche=0, naechster_versuch_am=jetzt, erstellt_am=jetzt))
    return len(quellen)


def _wert(instanz: Any, name: str) -> Any:
    # Niemals getattr: abgelaufene/deferred Attribute duerfen keinen Lazy-Load ausloesen.
    zustand = inspect(instanz)
    if name == "id" and zustand.identity:
        return zustand.identity[0]
    return zustand.dict.get(name)


def _paar(quelle: str, feld: str = "id") -> Callable[[Any], list[tuple[str, str]]]:
    def schluessel(instanz: Any) -> list[tuple[str, str]]:
        ident = _wert(instanz, feld)
        return [(quelle, str(ident))] if ident is not None else []
    return schluessel


def _objekt(instanz: Any) -> list[tuple[str, str]]:
    zustand = inspect(instanz)
    if "entwurf_von_id" not in zustand.dict and not zustand.pending:
        return []
    if _wert(instanz, "entwurf_von_id") is not None:
        return []
    return _paar("objekt")(instanz)


_MODELL_SCHLUESSEL = {
    Objekt: _objekt,
    ObjektMerkmal: _paar("objekt", "objekt_id"),
    ObjektGefahr: _paar("objekt", "objekt_id"),
    ObjektBMA: _paar("objekt", "objekt_id"),
    ObjektZusatzadresse: _paar("objekt", "objekt_id"),
    ObjektDokument: _paar("objekt_dokument"),
    ObjektDokumentSeite: _paar("objekt_dokument_seite"),
    KatplanPlan: _paar("katplan_plan"),
    KatplanVersion: _paar("katplan_plan", "plan_id"),
    KatplanDokument: _paar("katplan_dokument"),
    KatplanDokumentSeite: _paar("katplan_seite"),
}


def _nach_flush(session: Session, flush_context: Any) -> None:
    try:
        if not settings.SEARCH_ENABLED:
            return
        kandidaten = set(session.new) | set(session.deleted) | {
            obj for obj in session.dirty if session.is_modified(obj, include_collections=False)
        }
        schluessel: set[tuple[int, str, str]] = set()
        for obj in kandidaten:
            ableiten = _MODELL_SCHLUESSEL.get(type(obj))
            org_id = _wert(obj, "org_id") if ableiten else None
            if org_id is not None and ableiten is not None:
                schluessel.update((org_id, quelle, ident) for quelle, ident in ableiten(obj))
        if not schluessel:
            return
        conn = session.connection()
        # Connection-Savepoint statt Session.begin_nested (wuerde erneut flushen).
        # Auch ein SQL-Fehler darf die umgebende Fachtransaktion nicht vergiften.
        with conn.begin_nested():
            t = cast(Table, SearchOutbox.__table__)
            vorhanden = set(conn.execute(select(t.c.org_id, t.c.quelle, t.c.quell_id).where(
                t.c.status.in_(_OFFEN), tuple_(t.c.org_id, t.c.quelle, t.c.quell_id).in_(schluessel),
            )).all())
            jetzt = _jetzt()
            zeilen = [dict(org_id=org, quelle=quelle, quell_id=ident, status="pending", versuche=0,
                           naechster_versuch_am=jetzt + timedelta(seconds=2), erstellt_am=jetzt)
                      for org, quelle, ident in sorted(schluessel - vorhanden)]
            if zeilen:
                conn.execute(insert(t), zeilen)
    except Exception:
        logger.exception("Such-Outbox: Aenderungserfassung fehlgeschlagen")


def registriere_listener() -> None:
    """Registriert beim App-Import genau einen Listener auf der globalen Session."""
    if not event.contains(Session, "after_flush", _nach_flush):
        event.listen(Session, "after_flush", _nach_flush)


def _index_ids(db: Session, org_id: int, quelle: str, feld: str, ident: str) -> set[str]:
    ids = set()
    # Nur ID und Metadaten laden, nicht Titel/Keywords aller Index-Dokumente der Org.
    for quell_id, meta_json in db.execute(select(SearchDocument.quell_id, SearchDocument.meta_json).where(
        SearchDocument.org_id == org_id, SearchDocument.quelle == quelle,
    )):
        meta = json.loads(meta_json or "{}")
        if isinstance(meta, dict) and str(meta.get(feld)) == ident:
            ids.add(quell_id)
    return ids


def loese_auf(db: Session, org_id: int, quelle: str, quell_id: str) -> list[tuple[str, str]]:
    """Liefert deduplizierte Adapter-Paare, ohne Index oder Outbox zu veraendern."""
    if quelle == "neuaufbau":
        adapter_fuer(quell_id)  # Unbekannte Quellen werden als fehlerhaft behandelt.
        return [("neuaufbau", quell_id)]
    paare: set[tuple[str, str]] = set()
    if quelle in ("objekt", "objekt_dokument_seite", "katplan_seite", "katplan_element"):
        paare.add((quelle, quell_id))
        if quelle != "objekt":
            return sorted(paare)
    modell: type[ObjektDokumentSeite] | type[KatplanDokumentSeite]
    if quelle in ("objekt", "objekt_dokument"):
        modell = ObjektDokumentSeite
        ziel = "objekt_dokument_seite"
        feld = "objekt_id" if quelle == "objekt" else "dokument_id"
    elif quelle in ("katplan_plan", "katplan_dokument"):
        modell = KatplanDokumentSeite
        ziel = "katplan_seite"
        feld = "plan_id" if quelle == "katplan_plan" else "dokument_id"
        if quelle == "katplan_plan":
            praefix = f"{quell_id}:"
            ids = {ident for ident in adapter_fuer("katplan_element").alle_ids(db, org_id)
                   if ident.startswith(praefix)}
            ids.update(db.scalars(select(SearchDocument.quell_id).where(
                SearchDocument.org_id == org_id, SearchDocument.quelle == "katplan_element",
                SearchDocument.quell_id.startswith(praefix),
            )))
            paare.update(("katplan_element", ident) for ident in ids)
    else:
        raise ValueError(f"Unbekannte Outbox-Quelle: {quelle}")
    seiten = {str(ident) for ident in db.scalars(select(modell.id).where(
        modell.org_id == org_id, getattr(modell, feld) == quell_id,
    ))}
    seiten.update(_index_ids(db, org_id, ziel, feld, quell_id))
    paare.update((ziel, ident) for ident in seiten)
    return sorted(paare)


def _claim(db: Session, job: SearchOutbox, jetzt: datetime) -> bool:
    result = db.execute(update(SearchOutbox).where(
        SearchOutbox.id == job.id, SearchOutbox.org_id == job.org_id,
        SearchOutbox.status.in_(_OFFEN), SearchOutbox.naechster_versuch_am <= jetzt,
        or_(SearchOutbox.gesperrt_bis.is_(None), SearchOutbox.gesperrt_bis < jetzt),
    ).values(gesperrt_bis=jetzt + timedelta(minutes=2), versuche=SearchOutbox.versuche + 1))
    db.commit()
    return getattr(result, "rowcount", 0) == 1


def verarbeite_faellige(db: Session, limit: int = 50) -> int:
    set_tenant_context(db, None)
    jetzt = _jetzt()
    ids = db.scalars(select(SearchOutbox.id).where(
        SearchOutbox.status.in_(_OFFEN), SearchOutbox.naechster_versuch_am <= jetzt,
        or_(SearchOutbox.gesperrt_bis.is_(None), SearchOutbox.gesperrt_bis < jetzt),
    ).order_by(SearchOutbox.naechster_versuch_am, SearchOutbox.id).limit(limit)).all()
    verarbeitet = 0
    for ident in ids:
        job = db.get(SearchOutbox, ident)
        if job is None or not _claim(db, job, _jetzt()):
            continue
        verarbeitet += 1
        try:
            aktiv = db.scalar(select(OrgSettings.suche_aktiv).where(OrgSettings.org_id == job.org_id))
            if aktiv and settings.SEARCH_INDEX_WORKER_ENABLED:
                for quelle, quell_id in loese_auf(db, job.org_id, job.quelle, job.quell_id):
                    if quelle == "neuaufbau":
                        neuaufbau(db, job.org_id, quell_id)
                    else:
                        indexiere(db, job.org_id, quelle, quell_id)
            job.status = "done"
            job.gesperrt_bis = None
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(SearchOutbox, ident)
            if job is None:
                logger.exception("Such-Outbox-Zeile nach Fehler verschwunden (id=%s)", ident)
                continue
            job.letzter_fehler = f"{type(exc).__name__}: {exc}"[:500]
            job.status = "failed" if job.versuche >= 8 else "retry"
            job.naechster_versuch_am = _jetzt() + timedelta(seconds=min(3600, 30 * 2 ** (job.versuche - 1)))
            job.gesperrt_bis = None
            db.commit()
            logger.exception("Such-Outbox: Indexierung fehlgeschlagen (id=%s)", ident)
    return verarbeitet


def raeume_auf(db: Session) -> None:
    set_tenant_context(db, None)
    # Bewusst org-uebergreifender Bulk-Delete im Systemmodus, nur alte done-Zeilen.
    db.execute(delete(SearchOutbox).where(
        SearchOutbox.status == "done", SearchOutbox.erstellt_am < _jetzt() - timedelta(days=7),
    ))
    db.commit()


def abgleich(db: Session) -> None:
    set_tenant_context(db, None)
    org_ids = db.scalars(select(OrgSettings.org_id).where(OrgSettings.suche_aktiv.is_(True))).all()
    for org_id in org_ids:
        neuaufbau(db, org_id)


def _iteration(mit_abgleich: bool) -> None:
    # Session wird im selben Thread erstellt, benutzt und auch bei Fehlern geschlossen.
    with SessionLocal() as db:
        verarbeite_faellige(db)
        if mit_abgleich:
            abgleich(db)
            raeume_auf(db)


async def suche_outbox_loop() -> None:
    letzter_abgleich: float | None = None
    while True:
        try:
            if settings.SEARCH_ENABLED and settings.SEARCH_INDEX_WORKER_ENABLED:
                jetzt = time.monotonic()
                faellig = letzter_abgleich is None or jetzt - letzter_abgleich >= settings.SEARCH_ABGLEICH_MINUTEN * 60
                await asyncio.to_thread(_iteration, faellig)
                if faellig:
                    letzter_abgleich = jetzt
        except Exception:
            logger.exception("Such-Outbox-Iteration fehlgeschlagen")
        await asyncio.sleep(10)
