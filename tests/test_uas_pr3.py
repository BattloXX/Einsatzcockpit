"""PR 3: UAS-Einsatz-Modell, Rollen, Mindestbesetzungs-Validierung."""
from types import SimpleNamespace

import pytest

from app.models.uas import (
    UASEinsatz,
    UASEinsatzRolle,
    UASEinsatzRolleEintrag,
    UASEinsatzStatus,
)
from app.routers.ui_uas import _validate_mindestbesetzung


# ── Modell-Import ──────────────────────────────────────────────────────────────

def test_uas_einsatz_importable():
    assert UASEinsatz.__tablename__ == "uas_einsatz"


def test_uas_einsatz_rolle_eintrag_importable():
    assert UASEinsatzRolleEintrag.__tablename__ == "uas_einsatz_rolle"


def test_status_enum_values():
    values = {s.value for s in UASEinsatzStatus}
    assert "alarmiert" in values
    assert "im_einsatz" in values
    assert "abgeschlossen" in values


def test_rolle_enum_values():
    values = {r.value for r in UASEinsatzRolle}
    assert "teamleiter" in values
    assert "pilot" in values
    assert "luftraumbeobachter" in values
    assert len(values) == 7


# ── Mindestbesetzungs-Validierung (RL 6.1) ────────────────────────────────────

def _rolle(rolle: str) -> SimpleNamespace:
    return SimpleNamespace(rolle=rolle)


def test_mindestbesetzung_ok():
    rollen = [_rolle("pilot"), _rolle("luftraumbeobachter")]
    assert _validate_mindestbesetzung(rollen) == []


def test_mindestbesetzung_kein_pilot():
    rollen = [_rolle("luftraumbeobachter"), _rolle("operator")]
    fehlt = _validate_mindestbesetzung(rollen)
    assert any("Pilot" in f for f in fehlt)


def test_mindestbesetzung_kein_luftraumbeobachter():
    rollen = [_rolle("pilot"), _rolle("operator")]
    fehlt = _validate_mindestbesetzung(rollen)
    assert any("Luftraumbeobachter" in f for f in fehlt)


def test_mindestbesetzung_zu_wenig_personen():
    rollen = [_rolle("pilot")]
    fehlt = _validate_mindestbesetzung(rollen)
    assert len(fehlt) > 0


def test_mindestbesetzung_leer():
    fehlt = _validate_mindestbesetzung([])
    assert len(fehlt) >= 2  # Pilot + Luftraumbeobachter + Anzahl


# ── DB-Roundtrip ──────────────────────────────────────────────────────────────

@pytest.fixture
def db_ctx():
    from app.core.tenant import set_tenant_context
    from tests.conftest import TestingSession
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.close()


def test_uas_einsatz_db_create(setup_db, db_ctx):
    from datetime import UTC, datetime

    e = UASEinsatz(
        org_id=1,
        incident_id=999,  # SQLite: kein FK-Check
        status=UASEinsatzStatus.alarmiert.value,
        alarmierung_at=datetime.now(UTC),
        tetra_rufname="FDWOL-DT1",
        betreibernummer="AT-12345",
        einsatzgrund="Personensuche",
        datenschutz_bestaetigt=True,
    )
    db_ctx.add(e)
    db_ctx.commit()
    db_ctx.refresh(e)

    assert e.id is not None
    assert e.status == "alarmiert"
    assert e.tetra_rufname == "FDWOL-DT1"
    assert e.datenschutz_bestaetigt is True


def test_uas_einsatz_rolle_eintrag_db_create(setup_db, db_ctx):
    from datetime import UTC, datetime

    einsatz = UASEinsatz(
        org_id=1,
        incident_id=998,
        status=UASEinsatzStatus.alarmiert.value,
        alarmierung_at=datetime.now(UTC),
    )
    db_ctx.add(einsatz)
    db_ctx.flush()

    r = UASEinsatzRolleEintrag(
        org_id=1,
        uas_einsatz_id=einsatz.id,
        pilot_id=None,
        helfer_name="Max Mustermann",
        rolle=UASEinsatzRolle.teamleiter.value,
    )
    db_ctx.add(r)
    db_ctx.commit()
    db_ctx.refresh(r)

    assert r.id is not None
    assert r.rolle == "teamleiter"
    assert r.helfer_name == "Max Mustermann"


def test_tenant_tables_enthalten_einsatz():
    from app.core.tenant import _TENANT_TABLE_NAMES
    assert "uas_einsatz" in _TENANT_TABLE_NAMES
    assert "uas_einsatz_rolle" in _TENANT_TABLE_NAMES
