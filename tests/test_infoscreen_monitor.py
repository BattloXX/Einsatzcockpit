"""Infoscreen: URL-Rotation, Monitor-Matrix, GSL-Vorrang, persistente URL (Phase 4)."""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident
from app.models.major_incident import MajorIncident, MajorIncidentStatus
from app.models.master import FireDept, OrgSettings
from app.models.objekt import AlarmInfoscreenToken, InfoscreenUrl
from app.routers.ui_infoscreen_alarm import infoscreen_daten


@pytest.fixture()
def db_org():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)
    org = FireDept(slug="mon-org", name="Monitor Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, alarm_infoscreen_idle_modus="uhr",
                       alarm_infoscreen_wetter_url="https://wetter.example/screen",
                       alarm_infoscreen_gsl_enabled=True))
    yield db, org
    db.close()
    Base.metadata.drop_all(bind=engine)


def _token(db, org, *, url_ids=None, zeigt_wetter=False):
    t = AlarmInfoscreenToken(org_id=org.id, token_hash=hash_api_key("mon-token"),
                             name="Monitor 1", aktiv=True, zeigt_wetter=zeigt_wetter,
                             url_ids_json=json.dumps(url_ids) if url_ids else None)
    db.add(t)
    db.commit()
    return t


def test_idle_url_rotation_und_wetter(db_org):
    db, org = db_org
    u1 = InfoscreenUrl(org_id=org.id, label="ORF", url="https://orf.at", dwell_sec=20, sort=1)
    u2 = InfoscreenUrl(org_id=org.id, label="News", url="https://news.example", dwell_sec=40, sort=2)
    db.add_all([u1, u2])
    db.flush()
    _token(db, org, url_ids=[u2.id, u1.id], zeigt_wetter=True)  # bewusste Reihenfolge u2, u1

    daten = infoscreen_daten("mon-token", request=None, db=db)  # type: ignore[arg-type]
    assert daten["modus"] == "idle"
    urls = daten["idle_urls"]
    assert [x["url"] for x in urls] == [
        "https://news.example", "https://orf.at", "https://wetter.example/screen"]
    assert urls[1]["dwell_sec"] == 20


def test_gsl_hat_vorrang_vor_einsatz(db_org):
    db, org = db_org
    _token(db, org)
    db.add(Incident(primary_org_id=org.id, alarm_type_code="F14", status="active",
                    started_at=datetime.now(UTC).replace(tzinfo=None)))
    db.add(MajorIncident(org_id=org.id, name="Hochwasser Nord",
                         status=MajorIncidentStatus.active, started_at=datetime.now(UTC)))
    db.commit()
    daten = infoscreen_daten("mon-token", request=None, db=db)  # type: ignore[arg-type]
    assert daten["modus"] == "gsl"
    assert daten["gsl"]["name"] == "Hochwasser Nord"


def test_einsatz_ohne_zeitfenster_solange_aktiv(db_org):
    db, org = db_org
    _token(db, org)
    # Einsatz vor 5 Stunden gestartet, aber noch aktiv → muss weiter angezeigt werden
    alt = datetime.now(UTC).replace(tzinfo=None).replace(microsecond=0)
    from datetime import timedelta
    db.add(Incident(primary_org_id=org.id, alarm_type_code="F14", status="active",
                    started_at=alt - timedelta(hours=5)))
    db.commit()
    daten = infoscreen_daten("mon-token", request=None, db=db)  # type: ignore[arg-type]
    assert daten["modus"] == "alarm"


def test_token_enc_roundtrip():
    token = "geheim-monitor-token-123"
    enc = encrypt_secret(token)
    assert enc != token
    assert decrypt_secret(enc) == token
