"""Zwei echte Transaktionen dürfen dieselbe Einheit nicht gleichzeitig aktivieren."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.tenant import set_tenant_context
from app.models.major_incident import (
    EINHEIT_STATUS_AKTIV,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageJournalEntry,
    MajorIncident,
    SiteLogEntry,
    SitePhase,
)
from app.models.master import FireDept
from app.models.user import AuditLog
from app.services import resource_service
from app.services.einheit_service import EinheitKonflikt, EinheitKontext, auftrag_laden, setze_einheit_status


@pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL nicht gesetzt")
@pytest.mark.parametrize("zweiter", ["tablet", "board", "bestaetigen"])
def test_parallel_ein_aktiver_aber_mehrere_bestaetigte_auftraege(zweiter):
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    org_id = lage_id = einheit_id = None
    try:
        with Session(engine) as db:
            set_tenant_context(db, None)
            org = FireDept(slug="einheit-parallel-" + uuid4().hex, name="Parallel-Test")
            db.add(org)
            db.flush()
            lage = MajorIncident(org_id=org.id, name="Parallel-Test")
            db.add(lage)
            db.flush()
            einheit = LageEinheit(lage_id=lage.id, label="RLF")
            stellen = [IncidentSite(org_id=org.id, major_incident_id=lage.id,
                                   bezeichnung=name, phase=SitePhase.disponiert) for name in ("A", "B")]
            db.add_all([einheit, *stellen])
            db.flush()
            dispatches = [EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id,
                                              dispatched_at=datetime.now(UTC)) for site in stellen]
            db.add_all(dispatches)
            db.commit()
            org_id, lage_id, einheit_id = org.id, lage.id, einheit.id
            dispatch_ids = [d.id for d in dispatches]
        bereit = Barrier(2)

        def schreibe(index):
            with Session(engine) as db:
                set_tenant_context(db, org_id)
                ctx = EinheitKontext(None, None, db.get(LageEinheit, einheit_id),
                                     db.get(MajorIncident, lage_id), org_id, "tablet")
                dispatch = auftrag_laden(db, ctx, dispatch_ids[index])
                # Beide kennen zunächst den alten Stand, wie zwei Tablets im selben Augenblick.
                bereit.wait(timeout=10)
                def anwenden():
                    if index == 1 and zweiter == "board":
                        _, konflikt = resource_service.set_vor_ort_at_site(
                            db, einheit_id, lage_id, dispatch.site_id,
                        )
                        if konflikt:
                            db.rollback()
                            return "konflikt"
                    else:
                        setze_einheit_status(db, ctx, dispatch,
                                            "bestaetigt" if zweiter == "bestaetigen" else "anfahrt",
                                            user_id=None, author_name="Parallel-Test", auftrag_version=1)
                    return "ok"
                try:
                    return resource_service.status_transaktion(db, anwenden)
                except EinheitKonflikt:
                    db.rollback()
                    return "konflikt"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(schreibe, index) for index in (0, 1)]
            ergebnisse = [f.result(timeout=25) for f in futures]
        assert sorted(ergebnisse) == (["ok", "ok"] if zweiter == "bestaetigen" else ["konflikt", "ok"])
        with Session(engine) as db:
            set_tenant_context(db, org_id)
            auftraege = db.query(EinheitSiteDispatch).filter_by(einheit_id=einheit_id).all()
            aktiv = [d for d in auftraege if d.einheit_status in EINHEIT_STATUS_AKTIV]
            if zweiter == "bestaetigen":
                assert not aktiv and all(d.einheit_status == "bestaetigt" for d in auftraege)
                assert db.get(LageEinheit, einheit_id).incident_site_id is None
            else:
                assert len(aktiv) == 1
                assert db.get(LageEinheit, einheit_id).incident_site_id == aktiv[0].site_id
    finally:
        if einheit_id is not None:
            with Session(engine) as db:
                set_tenant_context(db, None)
                db.query(LageEinheit).filter_by(id=einheit_id).update({"incident_site_id": None})
                db.query(EinheitSiteDispatch).filter_by(einheit_id=einheit_id).delete()
                site_ids = db.query(IncidentSite.id).filter_by(major_incident_id=lage_id)
                db.query(SiteLogEntry).filter(SiteLogEntry.incident_site_id.in_(site_ids)).delete()
                db.query(LageJournalEntry).filter_by(major_incident_id=lage_id).delete()
                db.query(IncidentSite).filter_by(major_incident_id=lage_id).delete()
                db.query(LageEinheit).filter_by(id=einheit_id).delete()
                db.query(MajorIncident).filter_by(id=lage_id).delete()
                db.query(AuditLog).filter_by(org_id=org_id).delete()
                db.query(FireDept).filter_by(id=org_id).delete()
                db.commit()
        engine.dispose()
