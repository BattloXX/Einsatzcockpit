"""Regressionstest fuer POST /atemschutz-pruefung.

Der mypy-Cleanup (Cluster H: form.get() -> str | UploadFile | None) hat den lokalen
`_form_str()`-Helper eingefuehrt und 6 Formularfeld-Zugriffe in `pruefung_speichern`
darauf umgestellt (geraet_id, traeger_member_id, traeger_free_text, incident_id,
defekt_info, ort_text). Dieser Endpoint hatte zuvor keine Testabdeckung -- dieser Test
deckt den kompletten Speicher-Pfad ab, um sicherzustellen, dass der Umbau das
Verhalten nicht veraendert hat.
"""
from datetime import date

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.atemschutz_pruefung import AtemschutzGeraet, AtemschutzPruefung
from app.models.user import Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup(username):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="AP-Test", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "incident_leader").id))

        geraet = AtemschutzGeraet(org_id=ORG_ID, nummer="PA-42", bezeichnung="Testgeraet", aktiv=True)
        db.add(geraet)
        db.commit()
        return user.id, geraet.id
    finally:
        db.close()


def test_pruefung_speichern_mit_freitext_traeger_und_ort():
    _, geraet_id = _setup("ap_formstr_user")

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r_login = _login(client, "ap_formstr_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post("/atemschutz-pruefung", data={
        "_csrf": csrf,
        "geraet_id": str(geraet_id),
        "traeger_free_text": "Max Mustermann",
        "eingesetzt_am": date.today().isoformat(),
        "einsatz_art": "uebung",
        "ort_text": "  Uebungsplatz  ",
        "flaschendruck_bar": "300",
        "druckabfall_bar": "5",
        "rueckzugssignal_bar": "55",
        "sichtpruefung_ok": "ok",
        "geraet_einsatzbereit_ok": "ok",
        "defekt_info": "",
    }, follow_redirects=False)
    assert r.status_code == 200, r.text[:500]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        pruefung = (
            db.query(AtemschutzPruefung)
            .filter(AtemschutzPruefung.geraet_id == geraet_id)
            .order_by(AtemschutzPruefung.id.desc())
            .first()
        )
        assert pruefung is not None, "Pruefung wurde nicht gespeichert"
        assert pruefung.traeger_free_text == "Max Mustermann"
        assert pruefung.ort_text == "Uebungsplatz"  # .strip() muss weiterhin greifen
        assert pruefung.defekt_info is None  # leerer String -> None
        assert pruefung.flaschendruck_bar == 300
    finally:
        db.close()


def test_pruefung_speichern_ohne_geraet_zeigt_fehler():
    _setup("ap_formstr_fehler_user")

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "ap_formstr_fehler_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post("/atemschutz-pruefung", data={
        "_csrf": csrf,
        "geraet_id": "999999",
        "eingesetzt_am": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 422
    assert "gültiges Atemschutzgerät" in r.text
