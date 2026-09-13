"""Integrationstests fuer das zentrale Kontakte-Modul Phase 2."""

from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.kontakt import Kontakt, KontaktKategorie
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt, ObjektKontakt
from app.models.user import Role, User, UserRole
from app.services import kontakt_service
from app.services.kontakt_transfer_service import apply_preview, export_xlsx, parse_import, preview_import, save_preview


def _rolle(db, code: str) -> Role:
    rolle = db.query(Role).filter(Role.code == code).first()
    assert rolle is not None
    return rolle


def _setup_user(username: str, rolle: str, *, org_id: int = 1) -> User:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            org_id=org_id,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, rolle).id))
        system = db.get(SystemSettings, "kontakte_module_enabled")
        if system is None:
            db.add(SystemSettings(key="kontakte_module_enabled", value="true"))
        else:
            system.value = "true"
        settings = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if settings is None:
            settings = OrgSettings(org_id=org_id)
            db.add(settings)
        settings.kontakte_module_enabled = True
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()


def _login(client, username: str) -> None:
    client.cookies.clear()
    client.get("/login")
    client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )


def _post_data(**werte):
    return werte


def test_crud_multitelefon_kategorien_und_konflikt(client):
    user = _setup_user("kontakte_crud", "kontakt_verwalter")
    _login(client, user.username)
    csrf = client.cookies.get("ec_csrf")
    payload = _post_data(
        _csrf=csrf,
        typ="person",
        anzeigename="Anna Beispiel",
        organisation="FF Beispiel",
        nummer=["00 43 664 123456", "+43 555 777"],
        telefon_label=["Mobil", "Dienst"],
        bevorzugt=["1"],
        sms_eignung=["0"],
        kategorien="Einsatzleitung, Atemschutz",
    )
    response = client.post("/kontakte/", data=payload, follow_redirects=False)
    assert response.status_code == 303
    kontakt_id = int(response.headers["location"].rsplit("/", 1)[1])

    db = SessionLocal()
    set_tenant_context(db, 1)
    try:
        kontakt = kontakt_service.get_kontakt(db, kontakt_id)
        assert kontakt is not None
        assert [telefon.nummer_normalisiert for telefon in kontakt.telefone] == ["+43664123456", "+43555777"]
        assert kontakt.telefone[1].bevorzugt and kontakt.telefone[0].sms_eignung
        assert {z.kategorie.name for z in kontakt.kategorien} == {"Einsatzleitung", "Atemschutz"}
        version = kontakt.version
    finally:
        db.close()

    update = _post_data(
        _csrf=csrf,
        version=str(version),
        typ="person",
        anzeigename="Anna Aktualisiert",
        nummer=["+43 555 777"],
        telefon_label=["Dienst"],
        bevorzugt=["0"],
        sms_eignung=["0"],
        kategorien="Einsatzleitung",
    )
    assert client.post(f"/kontakte/{kontakt_id}", data=update, follow_redirects=False).status_code == 303
    stale = _post_data(_csrf=csrf, version=str(version), typ="person", anzeigename="Stale")
    conflict = client.post(f"/kontakte/{kontakt_id}", data=stale)
    assert conflict.status_code == 200
    assert "inzwischen geaendert" in conflict.text
    assert "Stale" in conflict.text

    db = SessionLocal()
    set_tenant_context(db, 1)
    try:
        kontakt = kontakt_service.get_kontakt(db, kontakt_id)
        assert kontakt is not None and kontakt.anzeigename == "Anna Aktualisiert"
        assert len(kontakt.telefone) == 1 and kontakt.telefone[0].bevorzugt
        aktuelle_version = kontakt.version
    finally:
        db.close()
    assert (
        client.post(f"/kontakte/{kontakt_id}/archivieren", data={"_csrf": csrf}, follow_redirects=False).status_code
        == 303
    )
    assert "Anna Aktualisiert" not in client.get("/kontakte").text
    assert aktuelle_version > version


def test_rolle_suche_kategorie_idempotent_und_tenant_isolation(client):
    verwalter = _setup_user("kontakte_objekt", "objekt_verwalter")
    readonly = _setup_user("kontakte_readonly", "readonly")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b = FireDept(slug="kontakte-org-b", name="Kontakte Org B", color="#112233", bos="FW")
        db.add(org_b)
        db.flush()
        org_b_id = org_b.id
        db.commit()
    finally:
        db.close()
    _setup_user("kontakte_org_b", "kontakt_verwalter", org_id=org_b_id)

    _login(client, verwalter.username)
    csrf = client.cookies.get("ec_csrf")
    for name in ("Org A Kontakt", "Org A Zweitkontakt"):
        assert (
            client.post(
                "/kontakte/",
                data=_post_data(
                    _csrf=csrf,
                    typ="person",
                    anzeigename=name,
                    nummer=["00 43 664 123456"],
                    telefon_label=["Mobil"],
                    kategorien="Gemeinsam",
                    duplikate_bestaetigt="1" if name == "Org A Zweitkontakt" else "",
                ),
                follow_redirects=False,
            ).status_code
            == 303
        )
    db = SessionLocal()
    set_tenant_context(db, 1)
    try:
        assert db.query(KontaktKategorie).filter(KontaktKategorie.name == "Gemeinsam").count() == 1
        kontakt_id = db.query(Kontakt).filter(Kontakt.anzeigename == "Org A Kontakt").one().id
    finally:
        db.close()
    assert "Org A Kontakt" in client.get("/kontakte/liste?q=%2B43664123456").text

    _login(client, readonly.username)
    assert client.get("/kontakte").status_code == 200
    assert client.get("/kontakte/liste").status_code == 200
    assert client.get(f"/kontakte/{kontakt_id}").status_code == 200
    assert client.post("/kontakte/", data={"_csrf": client.cookies.get("ec_csrf")}).status_code == 403

    _login(client, "kontakte_org_b")
    assert client.get(f"/kontakte/{kontakt_id}").status_code == 404
    assert "Org A Kontakt" not in client.get("/kontakte/liste?q=Org%20A%20Kontakt").text


def test_kontakte_module_toggle_returns_404(client):
    user = _setup_user("kontakte_toggle", "kontakt_verwalter")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter_by(org_id=user.org_id).one().kontakte_module_enabled = False
        db.commit()
    finally:
        db.close()
    _login(client, user.username)
    assert client.get("/kontakte").status_code == 404
    assert client.get("/kontakte/liste").status_code == 404
    assert client.post("/kontakte/", data={"_csrf": client.cookies.get("ec_csrf")}).status_code == 404


def test_kontakt_export_ist_org_gebunden_und_enthaelt_alle_xlsx_blaetter(client):
    user = _setup_user("kontakte_export", "kontakt_verwalter")
    _login(client, user.username)
    csrf = client.cookies.get("ec_csrf")
    assert (
        client.post(
            "/kontakte/",
            data=_post_data(_csrf=csrf, typ="person", anzeigename="Export Kontakt", nummer=["+43 664 1"]),
            follow_redirects=False,
        ).status_code
        == 303
    )
    csv_response = client.get("/kontakte/export.csv")
    assert csv_response.status_code == 200
    assert "Export Kontakt" in csv_response.content.decode("utf-8-sig")
    xlsx_response = client.get("/kontakte/export.xlsx")
    assert xlsx_response.status_code == 200
    workbook = load_workbook(BytesIO(xlsx_response.content), read_only=True)
    assert workbook.sheetnames == ["Kontakte", "Telefonnummern", "Objektzuordnungen", "Anleitung"]


def test_import_vorschau_uebernimmt_telefon_und_liefert_ergebnis_csv(client):
    user = _setup_user("kontakte_import", "kontakt_verwalter")
    _login(client, user.username)
    csrf = client.cookies.get("ec_csrf")
    csv_data = (
        "version;id;typ;anzeigename;organisation;funktion;email;erreichbarkeit\n"
        "1;;person;Import Kontakt;Import GmbH;Bereitschaft;import@example.test;tagsueber\n"
    )
    response = client.post(
        "/kontakte/import/vorschau",
        data={"_csrf": csrf},
        files={"datei": ("kontakte.csv", csv_data, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    preview_url = response.headers["location"]
    assert client.get(preview_url).status_code == 200
    assert client.post(f"{preview_url}/uebernehmen", data={"_csrf": csrf}, follow_redirects=False).status_code == 303
    result = client.get(f"{preview_url}/ergebnis.csv")
    assert result.status_code == 200
    assert "uebernommen" in result.content.decode("utf-8-sig")
    assert client.get("/kontakte/vorlage.xlsx").status_code == 200
    assert client.get("/kontakte/vorlage.csv?beispiel=1").status_code == 200


def test_xlsx_roundtrip_uebernimmt_telefone_und_objektzuordnungen(client):
    user = _setup_user("kontakte_xlsx_roundtrip", "kontakt_verwalter")
    _login(client, user.username)
    csrf = client.cookies.get("ec_csrf")
    assert (
        client.post(
            "/kontakte/",
            data=_post_data(_csrf=csrf, typ="person", anzeigename="Roundtrip Kontakt", nummer=["+43 664 111"]),
            follow_redirects=False,
        ).status_code
        == 303
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kontakt = db.query(Kontakt).filter_by(org_id=user.org_id, anzeigename="Roundtrip Kontakt").one()
        objekt = Objekt(org_id=user.org_id, nummer=981234, name="Roundtrip Objekt")
        db.add(objekt)
        db.flush()
        db.add(ObjektKontakt(org_id=user.org_id, objekt_id=objekt.id, kontakt_id=kontakt.id))
        db.commit()
        rows = parse_import(export_xlsx(db, user.org_id), "kontakte.xlsx")
        row = next(row for row in rows if row["id"] == str(kontakt.id))
        assert row["_telefone"][0]["nummer"] == kontakt.telefone[0].nummer
        assert row["_zuordnungen"][0]["objekt_id"] == str(objekt.id)
        row["funktion"] = "Aktualisiert"
        row["_telefone"][0]["label"] = "Mobil"
        row["_zuordnungen"][0]["rolle"] = "betreiber"
        preview = preview_import(db, user.org_id, rows)
        entry = save_preview(db, user.org_id, user.id, preview)
        assert apply_preview(db, user.org_id, user.id, entry.id) == 1
        db.expire_all()
        assert db.get(Kontakt, kontakt.id).telefone[0].label == "Mobil"
        assert db.query(ObjektKontakt).filter_by(objekt_id=objekt.id, kontakt_id=kontakt.id).one().art == "betreiber"
    finally:
        db.close()


def test_import_markiert_doppelte_oder_fremde_ids_als_konflikt_oder_fehler(client):
    user = _setup_user("kontakte_import_konflikt", "kontakt_verwalter")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        foreign_org = FireDept(slug="kontakte-import-fremd", name="Import Fremd", color="#123456", bos="FW")
        db.add(foreign_org)
        db.flush()
        foreign = Kontakt(org_id=foreign_org.id, anzeigename="Fremder Kontakt")
        db.add(foreign)
        db.commit()
        duplicate = preview_import(
            db,
            user.org_id,
            [{"id": "77", "anzeigename": "A"}, {"id": "77", "anzeigename": "B"}],
        )
        foreign_row = preview_import(db, user.org_id, [{"id": str(foreign.id), "anzeigename": "Fremd"}])
        assert [item["status"] for item in duplicate] == ["konflikt", "konflikt"]
        assert foreign_row[0]["status"] == "fehler"
    finally:
        db.close()
