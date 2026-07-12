"""Lagedokument PR3: Client-Anbindung (Import-Map + y-quill-Wiring).

Kein echter Browser in diesem Umfeld verfuegbar -- prueft daher serverseitig,
dass die kollaborative Verkabelung (Import-Map, Modul-Script) nur fuer
Bearbeiten-Rollen ausgeliefert wird und die Wortmarke der vendorten Pakete
sowie der feste Yjs-Shared-Type-Name ("content") uebereinstimmen.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import MajorIncident
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _make_user_with_lage(username: str, *, org_slug: str, rolle: str | None) -> tuple[int, int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=f"Org {org_slug}", color="#334455", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Testuser", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        if rolle:
            role = db.query(Role).filter(Role.code == rolle).first()
            db.add(UserRole(user_id=user.id, role_id=role.id))
        lage = MajorIncident(org_id=org.id, name="Testlage")
        db.add(lage)
        db.commit()
        return org.id, lage.id
    finally:
        db.close()


def test_editor_bekommt_importmap_und_collab_modul(client, setup_db):
    _, lage_id = _make_user_with_lage("ld3_edit", org_slug="ld3-edit", rolle="incident_leader")
    _login(client, "ld3_edit", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert 'type="importmap"' in r.text
    assert '"yjs": "/static/js/collab/yjs.mjs"' in r.text
    assert '/static/js/lagedokument-collab.js' in r.text
    assert "initLagedokumentCollab" in r.text


def test_readonly_bekommt_keine_importmap_und_kein_collab_modul(client, setup_db):
    _, lage_id = _make_user_with_lage("ld3_ro", org_slug="ld3-ro", rolle="readonly")
    _login(client, "ld3_ro", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert 'type="importmap"' not in r.text
    assert '/static/js/lagedokument-collab.js' not in r.text


def test_importmap_deckt_alle_bare_specifier_der_vendorten_dateien_ab():
    """Jeder in den vendorten JS-Dateien per bare specifier importierte
    Modulname muss einen Import-Map-Eintrag haben, sonst schlaegt die
    Modulaufloesung im Browser fehl."""
    import re
    from pathlib import Path

    collab_dir = Path("app/static/js/collab")
    specifiers = set()
    pattern = re.compile(r"""(?:from|import)\(?\s*['"]([^'"./][^'"]*)['"]""")
    for js_file in collab_dir.rglob("*.js*"):
        content = js_file.read_text(encoding="utf-8")
        specifiers.update(pattern.findall(content))

    template = Path("app/templates/incident_major/lagedokument.html").read_text(encoding="utf-8")
    missing = [s for s in sorted(specifiers) if f'"{s}"' not in template]
    assert not missing, f"Fehlende Import-Map-Eintraege: {missing}"


def test_glue_js_nutzt_denselben_ytext_namen_wie_der_server():
    from pathlib import Path

    glue = Path("app/static/js/lagedokument-collab.js").read_text(encoding="utf-8")
    server = Path("app/services/lagedokument_collab.py").read_text(encoding="utf-8")
    assert "YTEXT_NAME = 'content'" in glue
    assert 'doc.get("content", type=' in server
