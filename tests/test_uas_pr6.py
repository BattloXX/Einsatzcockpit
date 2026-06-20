"""PR 6: Kartenobjekte (Model, Tenant, DB-Roundtrip)."""
import json

import pytest

from app.models.uas import UASKartenobjekt, UASKartenobjektTyp


def test_uas_kartenobjekt_importable():
    assert UASKartenobjekt.__tablename__ == "uas_kartenobjekt"


def test_kartenobjekt_typen():
    values = {t.value for t in UASKartenobjektTyp}
    assert "start_landezone" in values
    assert "pilotenzone" in values
    assert "grb_kreis" in values
    assert "drohnen_position" in values
    assert "fluggebiet" in values
    assert "lagepunkt" in values
    assert len(values) == 6


def test_tenant_tables_kartenobjekt():
    from app.core.tenant import _TENANT_TABLE_NAMES
    assert "uas_kartenobjekt" in _TENANT_TABLE_NAMES


@pytest.fixture
def db_ctx():
    from app.core.tenant import set_tenant_context
    from tests.conftest import TestingSession
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.close()


def test_kartenobjekt_db_create(setup_db, db_ctx):
    from datetime import UTC, datetime
    from app.models.uas import UASEinsatz, UASEinsatzStatus

    einsatz = UASEinsatz(org_id=1, incident_id=996, status=UASEinsatzStatus.alarmiert.value)
    db_ctx.add(einsatz)
    db_ctx.flush()

    geom = json.dumps({"type": "Point", "coordinates": [9.75, 47.35]})
    obj = UASKartenobjekt(
        org_id=1,
        uas_einsatz_id=einsatz.id,
        typ=UASKartenobjektTyp.start_landezone.value,
        geometrie=geom,
        label="Start/Landezone 1",
        hoehe_m=None,
        radius_m=50.0,
    )
    db_ctx.add(obj)
    db_ctx.commit()
    db_ctx.refresh(obj)

    assert obj.id is not None
    assert obj.typ == "start_landezone"
    assert obj.label == "Start/Landezone 1"
    g = json.loads(obj.geometrie)
    assert g["type"] == "Point"
