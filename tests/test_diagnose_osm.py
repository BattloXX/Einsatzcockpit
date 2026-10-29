"""OSM-Bestandsbefunde hinter der SELECT-Sperre ohne Fachdatenmutationen."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.admin_security import utcnow
from app.models.osm import OsmImportLauf
from app.services.diagnose.registry import CHECKS, execute_check
from tests import test_diagnose_checks as fixtures
from tests.test_diagnose_checks import insert, snapshot

diag_engine = fixtures.diag_engine
factory = fixtures.factory


@pytest.mark.parametrize("fall,kern,schweregrad", [
    ("fehlt", "bestand_fehlt", "fail"), ("alt", "bestand_veraltet", "warn"),
    ("unbekannt", "bestand_veraltet", "warn"), ("pruefung", "pruefung", "warn"),
    ("fehler", "importfehler", "warn"), ("ok", None, None), ("alter_fehler", None, None),
])
def test_bestand_findings(diag_engine, factory, monkeypatch, fall, kern, schweregrad):
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_QUELLEN", "region=https://osm.test/privat.pbf?lat=47.12345")
    with factory() as db:
        if fall != "fehlt":
            datum = None if fall == "unbekannt" else utcnow() - timedelta(days=40 if fall == "alt" else 1)
            insert(db, "osm_import_lauf", id=1, quelle="region", status="aktiv", datenstand=datum,
                   gestartet_am=utcnow() - timedelta(days=2))
        if fall in ("pruefung", "fehler", "alter_fehler"):
            insert(db, "osm_import_lauf", id=2, quelle="region", status="pruefung" if fall == "pruefung" else "fehler",
                   gestartet_am=utcnow() - timedelta(days=3 if fall == "alter_fehler" else 0),
                   fehler_code="OSM_DOWNLOAD_HTTP", fehler_text="GEHEIM lat=47.12345")
    vorher = snapshot(diag_engine)
    check = CHECKS["osm.gebaeude_bestand"]
    assert check.modul == "osm" and check.kosten == "leicht" and check.read_only
    ergebnis = execute_check(diag_engine, check)
    assert snapshot(diag_engine) == vorher
    assert ergebnis.vollstaendig
    if kern:
        assert len(ergebnis.findings) == 1
        befund = ergebnis.findings[0]
        assert befund.kern == ["region", kern] and befund.schweregrad == schweregrad
        assert "osm-gebaeude-" in befund.empfehlung
        assert "47.12345" not in str(befund) and "GEHEIM" not in str(befund)
    else:
        assert not ergebnis.findings
    with factory() as db:
        assert list(db.scalars(select(OsmImportLauf))) or fall == "fehlt"
