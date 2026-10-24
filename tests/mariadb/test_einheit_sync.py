"""Zwei echte Transaktionen dürfen aus derselben UUID keine falschen Quittungen erzeugen."""

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
    EinheitAktion,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    MajorIncident,
    SiteLogEntry,
)
from app.models.master import FireDept
from app.routers.ui_einheit import fuehre_aktion_aus
from app.services.einheit_service import EinheitKontext


@pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL nicht gesetzt")
@pytest.mark.parametrize("zweiter", ["gleich", "andere_einheit", "andere_aktion"])
def test_parallel_replay_ist_an_die_originalaktion_gebunden(zweiter):
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    org_id = lage_id = None
    try:
        with Session(engine) as db:
            set_tenant_context(db, None)
            org = FireDept(slug="einheit-sync-" + uuid4().hex, name="Sync-Test")
            db.add(org)
            db.flush()
            lage = MajorIncident(org_id=org.id, name="Sync-Test")
            db.add(lage)
            db.flush()
            site = IncidentSite(org_id=org.id, major_incident_id=lage.id, bezeichnung="Schule")
            einheiten = [LageEinheit(lage_id=lage.id, label=name) for name in ("A", "B")]
            db.add_all([site, *einheiten])
            db.flush()
            dispatches = [EinheitSiteDispatch(einheit_id=e.id, site_id=site.id, dispatched_at=datetime.now(UTC))
                          for e in einheiten]
            db.add_all(dispatches)
            db.commit()
            org_id, lage_id, site_id = org.id, lage.id, site.id
            einheit_ids, dispatch_ids = [e.id for e in einheiten], [d.id for d in dispatches]
        bereit = Barrier(2)
        key = str(uuid4())

        def schreiben(index):
            ziel = index if zweiter == "andere_einheit" else 0
            art = "massnahmen" if index and zweiter == "andere_aktion" else "note"
            with Session(engine) as db:
                set_tenant_context(db, org_id)
                ctx = EinheitKontext(None, None, db.get(LageEinheit, einheit_ids[ziel]),
                                     db.get(MajorIncident, lage_id), org_id, "simulation")
                def anwenden():
                    bereit.wait(timeout=10)  # Beide UUID-Prüfungen sahen noch keinen Eintrag.
                    log = SiteLogEntry(incident_site_id=site_id, einheit_id=einheit_ids[ziel],
                                       kind=art, text=f"Transaktion {index}")
                    db.add(log)
                    db.flush()
                    return {"ok": True, "entity_id": log.id, "entity_type": "site_log"}
                return fuehre_aktion_aus(db, ctx, client_uuid=key, aktion=art, erfasst_at=datetime.now(UTC),
                                         dispatch_id=dispatch_ids[ziel], ausfuehren=anwenden)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(schreiben, i) for i in range(2)]
            results = [f.result(timeout=20) for f in futures]
        assert sorted(status for _, status in results) == ([200, 200] if zweiter == "gleich" else [200, 409])
        if zweiter == "gleich":
            assert results[0][0] == results[1][0]
        else:
            assert next(body for body, status in results if status == 409) == {"code": "client_uuid_vergeben"}
        with Session(engine) as db:
            set_tenant_context(db, None)
            assert db.query(EinheitAktion).filter_by(client_uuid=key).count() == 1
            assert db.query(SiteLogEntry).filter_by(incident_site_id=site_id).count() == 1
    finally:
        if org_id is not None:
            with Session(engine) as db:
                set_tenant_context(db, None)
                db.query(EinheitAktion).filter_by(org_id=org_id).delete()
                site_ids = db.query(IncidentSite.id).filter_by(major_incident_id=lage_id)
                db.query(SiteLogEntry).filter(SiteLogEntry.incident_site_id.in_(site_ids)).delete()
                db.query(EinheitSiteDispatch).filter(EinheitSiteDispatch.site_id.in_(site_ids)).delete()
                db.query(IncidentSite).filter_by(major_incident_id=lage_id).delete()
                db.query(LageEinheit).filter_by(lage_id=lage_id).delete()
                db.query(MajorIncident).filter_by(id=lage_id).delete()
                db.query(FireDept).filter_by(id=org_id).delete()
                db.commit()
        engine.dispose()
