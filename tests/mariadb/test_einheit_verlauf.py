"""Keyset-Seiten und historische Fotozuordnung unter echtem MariaDB-JSON-Verhalten."""
import json
import os
from datetime import UTC, datetime
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
    SiteMedia,
)
from app.models.master import FireDept
from app.services.einheit_service import EinheitKontext
from app.services.einheit_verlauf_service import meldungsverlauf


@pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL nicht gesetzt")
def test_verlauf_keyset_und_alte_fotoantwort():
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    with Session(engine) as db:
        set_tenant_context(db, None)
        try:
            org = FireDept(slug="verlauf-" + uuid4().hex, name="Verlauf-Test")
            db.add(org)
            db.flush()
            lage = MajorIncident(org_id=org.id, name="Verlauf")
            db.add(lage)
            db.flush()
            unit = LageEinheit(lage_id=lage.id, label="Gruppe")
            site = IncidentSite(org_id=org.id, major_incident_id=lage.id, bezeichnung="Schule")
            db.add_all([unit, site])
            db.flush()
            dispatch = EinheitSiteDispatch(einheit_id=unit.id, site_id=site.id, dispatched_at=datetime.now(UTC))
            foto = SiteMedia(org_id=org.id, incident_site_id=site.id, einheit_id=unit.id,
                             original_filename="foto.jpg", stored_filename="foto.jpg", media_type="image")
            db.add_all([dispatch, foto])
            db.flush()
            key = str(uuid4())
            db.add(EinheitAktion(org_id=org.id, client_uuid=key, einheit_id=unit.id, dispatch_id=dispatch.id,
                                 quelle="tablet", aktion="foto", ergebnis="ok", entity_type="site_media",
                                 entity_id=None,
                                 antwort_json=json.dumps({"entity_id": foto.id}), empfangen_at=datetime.now(UTC)))
            for i in range(5):
                db.add(SiteLogEntry(incident_site_id=site.id, einheit_id=unit.id, kind="note",
                                    text=f"Meldung {i}", ts=datetime(2026, 1, 1, tzinfo=UTC)))
            db.flush()
            # Wie im HTTP-Aufruf aus der Datenbank lesen: MariaDB liefert UTC ohne tzinfo.
            db.expire_all()
            ctx = EinheitKontext(None, None, unit, lage, org.id, quelle="tablet")
            rows, cursor = [], None
            while True:
                result = meldungsverlauf(db, ctx, limit=2, cursor=cursor)
                rows.extend(result["eintraege"])
                cursor = result["cursor"]
                if not cursor:
                    break
            assert len(rows) == len({r["id"] for r in rows}) == 7
            bild = next(r for r in rows if r["art"] == "foto")
            assert bild["client_uuid"] == key and bild["dispatch_id"] == dispatch.id
        finally:
            db.rollback()  # Testdaten bleiben in dieser Transaktion und werden vollständig zurückgenommen.
    engine.dispose()
