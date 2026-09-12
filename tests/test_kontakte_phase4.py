"""Duplikatpruefung und Zusammenfuehren fuer zentrale Kontakte (Phase 4)."""

from __future__ import annotations

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.kontakt import (
    Kontakt,
    KontaktAnhang,
    KontaktExterneReferenz,
    KontaktKategorie,
    KontaktKategorieZuordnung,
    ObjektKontaktFreigabe,
)
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektKontakt,
    ObjektKontaktBenachrichtigung,
)
from app.models.user import Role, User, UserRole
from app.services import kontakt_service


@pytest.fixture(autouse=True)
def _kein_login_ratelimit():
    from app.core.rate_limit import limiter

    if limiter is None:
        yield
        return
    vorher = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = vorher


def _rolle(db, code: str) -> Role:
    rolle = db.query(Role).filter(Role.code == code).first()
    assert rolle is not None
    return rolle


def _setup_user(username: str, *, org_id: int | None = None) -> User:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        if org_id is None:
            org = db.query(FireDept).first()
            assert org is not None
            org_id = org.id
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            org_id=org_id,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "kontakt_verwalter").id))
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


def _login(client, username: str) -> str:
    client.cookies.clear()
    client.get("/login")
    csrf = str(client.cookies.get("ec_csrf"))
    response = client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    return str(client.cookies.get("ec_csrf"))


def _kontakt(org_id: int, name: str, *, nummern: list[dict] | None = None, **daten) -> Kontakt:
    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        return kontakt_service.create_kontakt(
            db,
            {"typ": "person", "anzeigename": name, **daten},
            nummern or [],
            [],
            org_id=org_id,
            user_id=None,
        )
    finally:
        db.close()


def _objekt_mit_zuordnungen(db, org_id: int, quelle_id: int, ziel_id: int, *, art_quelle: str, art_ziel: str):
    objekt = Objekt(org_id=org_id, nummer=800000 + quelle_id, name="Merge-Objekt", status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt)
    db.flush()
    quelle = ObjektKontakt(org_id=org_id, objekt_id=objekt.id, kontakt_id=quelle_id, art=art_quelle, name="Quelle")
    ziel = ObjektKontakt(org_id=org_id, objekt_id=objekt.id, kontakt_id=ziel_id, art=art_ziel, name="Ziel")
    db.add_all((quelle, ziel))
    db.flush()
    return objekt, quelle, ziel


def _benachrichtigung(db, org_id: int, objekt_id: int, objekt_kontakt_id: int, empfaenger: str):
    incident = Incident(primary_org_id=org_id, alarm_type_code="T1")
    db.add(incident)
    db.flush()
    eintrag = ObjektKontaktBenachrichtigung(
        org_id=org_id,
        incident_id=incident.id,
        objekt_id=objekt_id,
        objekt_kontakt_id=objekt_kontakt_id,
        kanal="sms",
        empfaenger=empfaenger,
    )
    db.add(eintrag)
    db.flush()
    return eintrag


def test_duplikatcheck_findet_alle_signale_und_isoliert_orgs(client):
    user = _setup_user("p4_duplikate")
    org_id = user.org_id
    assert org_id is not None
    per_telefon = _kontakt(org_id, "Telefon Treffer", nummern=[{"nummer": "+43 664 111"}])
    per_email = _kontakt(org_id, "Mail Treffer", email="mail@example.at")
    per_name = _kontakt(org_id, "Gleicher Name", organisation="FF Muster")
    _kontakt(org_id, "Eigenstaendig", nummern=[{"nummer": "+43 664 999"}])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b = FireDept(slug="p4-duplikate-b", name="P4 Duplikate B", color="#123456", bos="FW")
        db.add(org_b)
        db.commit()
        org_b_id = org_b.id
    finally:
        db.close()
    fremd = _kontakt(org_b_id, "Gleicher Name", nummern=[{"nummer": "+43 664 111"}], organisation="FF Muster")

    _login(client, user.username)
    telefon = client.get("/kontakte/duplikatcheck", params={"anzeigename": "Neu", "nummer": "0043664111"})
    assert telefon.status_code == 200 and "Telefon Treffer" in telefon.text
    email = client.get("/kontakte/duplikatcheck", params={"anzeigename": "Neu", "email": "MAIL@example.at"})
    assert "Mail Treffer" in email.text
    name = client.get("/kontakte/duplikatcheck", params={"anzeigename": "gleicher name", "organisation": "ff muster"})
    assert "Gleicher Name" in name.text and str(fremd.id) not in name.text
    distinct = client.get("/kontakte/duplikatcheck", params={"anzeigename": "Niemand", "nummer": "+436649999"})
    assert "Moegliche Duplikate" not in distinct.text

    assert {per_telefon.id, per_email.id, per_name.id}.isdisjoint({fremd.id})


def test_create_blockiert_unbestaetigte_duplikate_und_erlaubt_bestaetigte(client):
    user = _setup_user("p4_create")
    assert user.org_id is not None
    _kontakt(user.org_id, "Bestehend", email="gleich@example.at")
    csrf = _login(client, user.username)
    daten = {"_csrf": csrf, "typ": "person", "anzeigename": "Neu Aber Gleich", "email": "gleich@example.at"}
    db = SessionLocal()
    set_tenant_context(db, user.org_id)
    try:
        vorher = db.query(Kontakt).count()
    finally:
        db.close()
    warnung = client.post("/kontakte/", data=daten)
    assert warnung.status_code == 200 and "Moegliche doppelte Kontakte" in warnung.text
    db = SessionLocal()
    set_tenant_context(db, user.org_id)
    try:
        assert db.query(Kontakt).count() == vorher
    finally:
        db.close()
    erstellt = client.post("/kontakte/", data={**daten, "duplikate_bestaetigt": "1"}, follow_redirects=False)
    assert erstellt.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, user.org_id)
    try:
        assert db.query(Kontakt).count() == vorher + 1
    finally:
        db.close()


def test_merge_vereinigt_telefone_kategorien_anhaenge_und_referenzen():
    user = _setup_user("p4_werte")
    assert user.org_id is not None
    quelle = _kontakt(
        user.org_id,
        "Quelle",
        nummern=[
            {"nummer": "0043664123", "label": "Quelle Mobil"},
            {"nummer": "+43 1 555", "label": "Doppelt"},
        ],
    )
    ziel = _kontakt(user.org_id, "Ziel", nummern=[{"nummer": "+431555", "label": "Ziel Büro"}])
    db = SessionLocal()
    set_tenant_context(db, user.org_id)
    try:
        a = KontaktKategorie(org_id=user.org_id, name="P4 A")
        b = KontaktKategorie(org_id=user.org_id, name="P4 B")
        db.add_all((a, b))
        db.flush()
        db.add_all(
            (
                KontaktKategorieZuordnung(org_id=user.org_id, kontakt_id=quelle.id, kategorie_id=a.id),
                KontaktKategorieZuordnung(org_id=user.org_id, kontakt_id=quelle.id, kategorie_id=b.id),
                KontaktKategorieZuordnung(org_id=user.org_id, kontakt_id=ziel.id, kategorie_id=b.id),
                KontaktAnhang(
                    org_id=user.org_id,
                    kontakt_id=quelle.id,
                    dateiname="p4.pdf",
                    medientyp="application/pdf",
                    speicher_pfad="kontakte/p4.pdf",
                ),
                KontaktExterneReferenz(
                    org_id=user.org_id, kontakt_id=quelle.id, quelle="p4", quelle_kontext="test", extern_id="1"
                ),
            )
        )
        db.commit()
        ergebnis = kontakt_service.merge_kontakte(db, quelle.id, ziel.id, {}, user_id=user.id)
        assert ergebnis.kontakt.id == ziel.id
        db.expire_all()
        zusammen = kontakt_service.get_kontakt(db, ziel.id, include_archiviert=True)
        assert zusammen is not None
        assert {(t.nummer_normalisiert, t.label) for t in zusammen.telefone} >= {
            ("+43664123", "Quelle Mobil"),
            ("+431555", "Ziel Büro"),
        }
        assert sum(t.nummer_normalisiert == "+431555" for t in zusammen.telefone) == 1
        zuordnungen = db.query(KontaktKategorieZuordnung).filter_by(kontakt_id=ziel.id).all()
        assert {z.kategorie_id for z in zuordnungen} == {a.id, b.id}
        assert len(zuordnungen) == 2
        assert db.query(KontaktAnhang).filter_by(kontakt_id=ziel.id, dateiname="p4.pdf").count() == 1
        assert db.query(KontaktExterneReferenz).filter_by(kontakt_id=ziel.id, extern_id="1").count() == 1
    finally:
        db.close()


def test_merge_repointet_objektzuordnungen_ohne_kollision_und_erhaelt_protokoll():
    user = _setup_user("p4_repoint")
    assert user.org_id is not None
    quelle, ziel = _kontakt(user.org_id, "Quelle"), _kontakt(user.org_id, "Ziel")
    db = SessionLocal()
    set_tenant_context(db, user.org_id)
    try:
        objekt, qz, zz = _objekt_mit_zuordnungen(
            db, user.org_id, quelle.id, ziel.id, art_quelle="betreiber", art_ziel="hausverwaltung"
        )
        protokoll = _benachrichtigung(db, user.org_id, objekt.id, qz.id, "+43664001")
        db.commit()
        qz_id, zz_id, protokoll_id = qz.id, zz.id, protokoll.id
        kontakt_service.merge_kontakte(db, quelle.id, ziel.id, {}, user_id=user.id)
        db.expire_all()
        uebrig = db.query(ObjektKontakt).filter(ObjektKontakt.id.in_((qz_id, zz_id))).all()
        assert {z.id for z in uebrig} == {qz_id, zz_id}
        assert all(z.kontakt_id == ziel.id for z in uebrig)
        assert db.get(ObjektKontaktBenachrichtigung, protokoll_id).objekt_kontakt_id == qz_id
    finally:
        db.close()


def test_merge_objektkollision_vereinigt_unterschiedliche_freigaben():
    user = _setup_user("p4_kollision")
    assert user.org_id is not None
    quelle, ziel = _kontakt(user.org_id, "Quelle"), _kontakt(user.org_id, "Ziel")
    db = SessionLocal()
    set_tenant_context(db, user.org_id)
    try:
        objekt, qz, zz = _objekt_mit_zuordnungen(
            db, user.org_id, quelle.id, ziel.id, art_quelle="betreiber", art_ziel="betreiber"
        )
        db.add_all(
            (
                ObjektKontaktFreigabe(
                    org_id=user.org_id, objekt_kontakt_id=qz.id, kanal="sms", ziel_wert="+43664002", aktiv=True
                ),
                ObjektKontaktFreigabe(
                    org_id=user.org_id, objekt_kontakt_id=zz.id, kanal="sms", ziel_wert="+43664003", aktiv=True
                ),
            )
        )
        db.commit()
        qz_id, zz_id = qz.id, zz.id
        kontakt_service.merge_kontakte(db, quelle.id, ziel.id, {}, user_id=user.id)
        db.expire_all()
        assert db.get(ObjektKontakt, qz_id) is None
        uebrig = db.query(ObjektKontakt).filter_by(objekt_id=objekt.id, art="betreiber").all()
        assert [z.id for z in uebrig] == [zz_id]
        freigaben = db.query(ObjektKontaktFreigabe).filter_by(objekt_kontakt_id=zz_id).all()
        assert {(f.ziel_wert, f.aktiv) for f in freigaben} == {("+43664002", True), ("+43664003", True)}
    finally:
        db.close()


def test_merge_freigabenkonflikt_archiviert_quelle_und_zeigt_warnung(client):
    user = _setup_user("p4_konflikt")
    assert user.org_id is not None
    quelle = _kontakt(user.org_id, "Quelle", notizen="Alte Notiz")
    ziel = _kontakt(user.org_id, "Ziel")
    db = SessionLocal()
    set_tenant_context(db, user.org_id)
    try:
        objekt, qz, zz = _objekt_mit_zuordnungen(
            db, user.org_id, quelle.id, ziel.id, art_quelle="betreiber", art_ziel="betreiber"
        )
        target = "+43664004"
        db.add_all(
            (
                ObjektKontaktFreigabe(
                    org_id=user.org_id, objekt_kontakt_id=qz.id, kanal="sms", ziel_wert=target, aktiv=True
                ),
                ObjektKontaktFreigabe(
                    org_id=user.org_id, objekt_kontakt_id=zz.id, kanal="sms", ziel_wert=target, aktiv=False
                ),
            )
        )
        protokoll = _benachrichtigung(db, user.org_id, objekt.id, qz.id, target)
        db.commit()
        qz_id, zz_id, protokoll_id = qz.id, zz.id, protokoll.id
        ergebnis = kontakt_service.merge_kontakte(db, quelle.id, ziel.id, {}, user_id=user.id)
        assert ergebnis.freigabe_konflikte and target in ergebnis.freigabe_konflikte[0]
        db.expire_all()
        freigaben = db.query(ObjektKontaktFreigabe).filter_by(objekt_kontakt_id=zz_id, ziel_wert=target).all()
        assert len(freigaben) == 1 and not freigaben[0].aktiv
        assert db.get(ObjektKontakt, qz_id) is None
        assert db.get(ObjektKontaktBenachrichtigung, protokoll_id).objekt_kontakt_id == zz_id
        archiviert = kontakt_service.get_kontakt(db, quelle.id, include_archiviert=True)
        assert archiviert is not None and archiviert.archiviert and not archiviert.aktiv
        assert str(ziel.id) in (archiviert.notizen or "")
    finally:
        db.close()

    csrf = _login(client, user.username)
    # Ein zweites Konfliktpaar testet den echten HTTP-Redirect inklusive gerenderter Warnung.
    http_quelle, http_ziel = _kontakt(user.org_id, "HTTP Quelle"), _kontakt(user.org_id, "HTTP Ziel")
    db = SessionLocal()
    set_tenant_context(db, user.org_id)
    try:
        objekt, qz, zz = _objekt_mit_zuordnungen(
            db, user.org_id, http_quelle.id, http_ziel.id, art_quelle="sonstig", art_ziel="sonstig"
        )
        db.add_all(
            (
                ObjektKontaktFreigabe(
                    org_id=user.org_id, objekt_kontakt_id=qz.id, kanal="sms", ziel_wert="http-ziel", aktiv=True
                ),
                ObjektKontaktFreigabe(
                    org_id=user.org_id, objekt_kontakt_id=zz.id, kanal="sms", ziel_wert="http-ziel", aktiv=False
                ),
            )
        )
        db.commit()
    finally:
        db.close()
    response = client.post(
        f"/kontakte/{http_quelle.id}/zusammenfuehren",
        data={"_csrf": csrf, "ziel_id": str(http_ziel.id)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "merge_konflikt" in response.history[0].headers["location"]
    assert "merge_konflikt" in str(response.url)
    assert "HTTP Ziel" in response.text
    assert "Freigabe-Konflikt nach Zusammenfuehren" in response.text and "http-ziel" in response.text
    assert "HTTP Quelle" not in client.get("/kontakte").text


def test_merge_lehnt_fremde_org_ab_und_feldwahl_ist_pro_feld_unabhaengig(client):
    user = _setup_user("p4_feldwahl")
    assert user.org_id is not None
    quelle = _kontakt(user.org_id, "Quelle", organisation="Quelle Organisation", funktion="Quelle Funktion")
    ziel = _kontakt(user.org_id, "Ziel", organisation="Ziel Organisation", funktion="Ziel Funktion")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b = FireDept(slug="p4-merge-b", name="P4 Merge B", color="#654321", bos="FW")
        db.add(org_b)
        db.commit()
        fremd = _kontakt(org_b.id, "Fremd")
        with pytest.raises(ValueError, match="selben Organisation"):
            kontakt_service.merge_kontakte(db, quelle.id, fremd.id, {}, user_id=user.id)
        assert db.get(Kontakt, quelle.id).archiviert is False
        assert db.get(Kontakt, fremd.id).archiviert is False
    finally:
        db.close()

    csrf = _login(client, user.username)
    response = client.post(
        f"/kontakte/{quelle.id}/zusammenfuehren",
        data={
            "_csrf": csrf,
            "ziel_id": str(ziel.id),
            "feldwahl_organisation": "quelle",
            "feldwahl_funktion": "ziel",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, user.org_id)
    try:
        nachher = kontakt_service.get_kontakt(db, ziel.id, include_archiviert=True)
        assert nachher is not None
        assert nachher.organisation == "Quelle Organisation"
        assert nachher.funktion == "Ziel Funktion"
    finally:
        db.close()
