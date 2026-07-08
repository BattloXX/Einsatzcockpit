"""Gemeinsame Druck-PDF-Route (lokaler Druck): org-Scoping + Dokumenttyp-Prüfung.

Testet die reine Zugriffslogik (_verify_org) ohne echtes PDF-Rendering (WeasyPrint).
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident
from app.models.master import FireDept
from app.routers.ui_druck import _verify_org

_ORG_A = 970001
_ORG_B = 970002


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    set_tenant_context(s, None)
    yield s
    s.close()
    Base.metadata.drop_all(bind=eng)


@pytest.fixture
def incident(db):
    db.add(FireDept(id=_ORG_A, slug="a", name="Org A", color="#f00", bos="Feuerwehr"))
    db.flush()
    inc = Incident(primary_org_id=_ORG_A, alarm_type_code="T1", status="active")
    db.add(inc)
    db.flush()
    return inc


def test_verify_org_own_incident_ok(db, incident):
    # Kein Fehler für die eigene Org
    _verify_org(db, _ORG_A, "einsatzinfo", incident.id, None, None, None)


def test_verify_org_foreign_incident_404(db, incident):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_B, "einsatzinfo", incident.id, None, None, None)
    assert ei.value.status_code == 404


def test_verify_org_missing_incident_404(db, incident):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_A, "einsatzinfo", None, None, None, None)
    assert ei.value.status_code == 404


def test_verify_org_unknown_type_400(db, incident):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_A, "quatsch", incident.id, None, None, None)
    assert ei.value.status_code == 400
