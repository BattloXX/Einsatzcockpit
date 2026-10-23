"""GSL-Gesamt-PDF im Einsatzarchiv."""
from __future__ import annotations

import io
from datetime import datetime
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import IncidentSite, MajorIncident
from app.models.master import FireDept
from app.models.user import Role, User, UserRole

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("weasyprint")


def _login(client, username):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf},
                       follow_redirects=False)


def _org(db, name):
    k = uuid4().hex[:8]
    org = FireDept(slug=f"{name}-{k}", name=name, color="#123456", bos="Feuerwehr")
    db.add(org)
    db.flush()
    rolle = db.query(Role).filter(Role.code == "incident_leader").first() or Role(
        code="incident_leader", name="incident_leader")
    db.add(rolle)
    db.flush()
    user = User(username=f"{name}-{k}", password_hash=hash_password("Test1234!"), display_name=name,
                org_id=org.id, active=True)
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=rolle.id))
    lage = MajorIncident(org_id=org.id, name=f"Lage {name}", started_at=datetime.now())
    db.add(lage)
    db.flush()
    db.add(IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung=f"Stelle {name}"))
    return user.username, lage.id


@pytest.fixture()
def daten():
    with SessionLocal() as db:
        set_tenant_context(db, None)
        a = _org(db, "Alpha")
        b = _org(db, "Beta")
        db.commit()
        return a, b


def test_archiv_listet_lagen_und_liefert_gesamt_pdf(client, daten):
    (user_a, lage_a), _ = daten
    _login(client, user_a)
    seite = client.get("/archiv?jahr=alle")
    assert seite.status_code == 200
    assert f"/archiv/gsl/{lage_a}/pdf" in seite.text and "Gesamt-PDF" in seite.text
    pdf = client.get(f"/archiv/gsl/{lage_a}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    text = " ".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Stelle Alpha" in text and "LAGE ALPHA" in text.upper()


def test_fremde_lage_ist_nicht_sichtbar_und_nicht_abrufbar(client, daten):
    (user_a, _), (_, lage_b) = daten
    _login(client, user_a)
    assert f"/archiv/gsl/{lage_b}/pdf" not in client.get("/archiv?jahr=alle").text
    assert client.get(f"/archiv/gsl/{lage_b}/pdf").status_code == 404


def test_gsl_pdf_braucht_login(client, daten):
    (_, lage_a), _ = daten
    client.cookies.clear()
    r = client.get(f"/archiv/gsl/{lage_a}/pdf", follow_redirects=False)
    assert r.status_code in (302, 303, 401)
