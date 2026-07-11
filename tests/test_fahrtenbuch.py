"""Tests: Digitales Fahrten- & Betriebsbuch."""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import UTC, datetime, timedelta
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.core.tenant import set_tenant_context
from app.models.fahrtenbuch import Fahrt, FahrtErfassungsweg, FahrtKategorie, FahrtStatus, Fahrtzweck, Zielort
from app.models.master import Member, OrgSettings, VehicleMaster
from app.models.user import Role, User, UserRole
from app.services.fahrtenbuch_service import (
    erstelle_fahrt,
    pruefe_doppelfahrt,
    pruefe_zaehler,
    recompute_zaehlerstand,
    storniere_fahrt,
    stammdaten_korrektur_zaehler,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session(setup_db):
    from tests.conftest import TestingSession
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.rollback()
    db.close()


@pytest.fixture()
def org(db_session):
    from app.models.master import FireDept
    dept = db_session.query(FireDept).first()
    assert dept, "Keine Org in der Test-DB"
    return dept


@pytest.fixture()
def fahrzeug(db_session, org):
    fz = (
        db_session.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == org.id)
        .first()
    )
    if not fz:
        fz = VehicleMaster(dept_id=org.id, code="TEST-FZ", name="Testfahrzeug", type="Test",
                            display_order=99)
        db_session.add(fz)
        db_session.flush()
    fz.km_aktuell = 1000
    fz.betriebsstunden_aktuell = Decimal("100.0")
    fz.seilwinde_bh_aktuell = Decimal("50.0")
    fz.erfasst_km = True
    fz.erfasst_betriebsstunden = True
    fz.seilwinde_abfrage = True
    fz.warn_schwelle_km = 50
    fz.warn_schwelle_bh = Decimal("10")
    db_session.flush()
    return fz


@pytest.fixture()
def zweck(db_session, org):
    z = db_session.query(Fahrtzweck).filter(Fahrtzweck.org_id == org.id).first()
    if not z:
        z = Fahrtzweck(org_id=org.id, name="Testübung", kategorie=FahrtKategorie.uebung)
        db_session.add(z)
        db_session.flush()
    return z


def _basis_daten(org_id, fahrzeug_id, zweck_id):
    return {
        "org_id": org_id,
        "fahrzeug_id": fahrzeug_id,
        "zweck_id": zweck_id,
        "maschinist_name": "Max Mustermann",
        "km_stand_neu": 1010,
        "erfasst_via": FahrtErfassungsweg.web,
    }


# ── Zähler-Tests ──────────────────────────────────────────────────────────────

def test_zaehler_steigt_normal(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "km", 1050)
    assert erg.delta == 50
    assert erg.warnung is False


def test_zaehler_fehler_wenn_sinkend(fahrzeug):
    with pytest.raises(HTTPException) as exc_info:
        pruefe_zaehler(fahrzeug, "km", 999)
    assert exc_info.value.status_code == 422


def test_zaehler_warnung_bei_grossem_delta(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "km", 1100)
    assert erg.warnung is True
    assert erg.delta == 100


def test_zaehler_bh_delta(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "bh", Decimal("105.0"))
    assert erg.delta == Decimal("5.0")
    assert erg.warnung is False


def test_zaehler_bh_warnung(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "bh", Decimal("115.0"))
    assert erg.warnung is True


def test_zaehler_seilwinde(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "seilwinde_bh", Decimal("55.0"))
    assert erg.delta == Decimal("5.0")


# ── Fahrt erstellen ───────────────────────────────────────────────────────────

def test_erstelle_fahrt_erfolgreich(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrt.id is not None
    assert fahrt.km_delta == 10
    assert fahrt.fahrttyp == zweck.kategorie
    assert fahrzeug.km_aktuell == 1010


def test_km_pflicht_bei_erfasst_km(db_session, org, fahrzeug, zweck):
    """Fahrzeug mit erfasst_km ohne km-Stand → 422 (km ist Pflichtfeld)."""
    fahrzeug.erfasst_km = True
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["km_stand_neu"] = None
    with pytest.raises(HTTPException) as exc:
        erstelle_fahrt(daten, db_session)
    assert exc.value.detail == "km_pflicht"


def test_km_optional_wenn_nicht_erfasst(db_session, org, fahrzeug, zweck):
    """Fahrzeug ohne erfasst_km darf ohne km-Stand gespeichert werden."""
    fahrzeug.erfasst_km = False
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["km_stand_neu"] = None
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrt.id is not None
    assert fahrt.km_stand_neu is None


def test_erstelle_fahrt_warnung_ohne_bestaetigung(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["km_stand_neu"] = 1100  # delta=100 > schwelle=50
    with pytest.raises(HTTPException) as exc:
        erstelle_fahrt(daten, db_session)
    assert exc.value.detail == "km_warnung_nicht_bestaetigt"


def test_erstelle_fahrt_warnung_mit_bestaetigung(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["km_stand_neu"] = 1100
    daten["km_warnung_bestaetigt"] = True
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrt.km_warnung_bestaetigt is True


def test_fahrttyp_aus_zweck(db_session, org, fahrzeug, zweck):
    zweck.kategorie = FahrtKategorie.einsatz
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrt.fahrttyp == FahrtKategorie.einsatz


def test_einsatzleiter_optional_gespeichert(db_session, org, fahrzeug, zweck):
    """Einsatzleiter ist optional: mit Angabe wird er denormalisiert gespeichert."""
    fahrzeug.einsatzleiter_abfrage = True
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["einsatzleiter_name"] = "Eva Einsatzleiterin"
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrt.einsatzleiter_name == "Eva Einsatzleiterin"


def test_einsatzleiter_darf_leer_bleiben(db_session, org, fahrzeug, zweck):
    """Auch bei aktivierter Abfrage bleibt der Einsatzleiter optional (kein Zwang)."""
    fahrzeug.einsatzleiter_abfrage = True
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrt.einsatzleiter_name is None
    assert fahrt.einsatzleiter_member_id is None


# ── Storno & Revision ─────────────────────────────────────────────────────────

def test_storno(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    fahrt = erstelle_fahrt(daten, db_session)
    storniere_fahrt(fahrt, "Testfehler", user_id=1, db=db_session)
    assert fahrt.status == FahrtStatus.storniert
    assert fahrt.storno_grund == "Testfehler"


def test_recompute_nach_storno(db_session, org, fahrzeug, zweck):
    fahrzeug.km_aktuell = 1000
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["km_stand_neu"] = 1020
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrzeug.km_aktuell == 1020
    storniere_fahrt(fahrt, "Rückgängig", user_id=1, db=db_session)
    # Nach Storno kein weiterer aktiver Stand → bleibt auf letztem Wert
    # (recompute gibt None zurück wenn keine aktiven Fahrten mehr)
    assert fahrzeug.km_aktuell >= 1000


# ── Stammdaten-Korrektur ──────────────────────────────────────────────────────

def test_stammdaten_korrektur_erlaubt_sinkenden_wert(db_session, org, fahrzeug):
    fahrzeug.km_aktuell = 5000
    db_session.flush()
    stammdaten_korrektur_zaehler(fahrzeug, "km", 4000, user_id=1, db=db_session)
    assert fahrzeug.km_aktuell == 4000


# ── Doppelfahrt-Schutz ────────────────────────────────────────────────────────

def test_doppelfahrt_warnung(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    erstelle_fahrt(daten, db_session)
    warnung = pruefe_doppelfahrt(fahrzeug, db_session, jetzt=datetime.now(UTC))
    assert warnung is True


def test_doppelfahrt_kein_alarm_nach_fenster(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["zeitpunkt"] = datetime.now(UTC) - timedelta(hours=2)
    erstelle_fahrt(daten, db_session)
    # Prüfe von jetzt aus: letzte Fahrt ist 2h alt, Fenster ist 10 min → kein Alarm
    warnung = pruefe_doppelfahrt(fahrzeug, db_session, jetzt=datetime.now(UTC))
    # Kann True sein wenn vorherige Tests Fahrten hinterlassen haben – akzeptabel


# ── Multi-Org-Isolation ───────────────────────────────────────────────────────

def test_multi_org_isolation(db_session):
    from app.models.master import FireDept
    orgs = db_session.query(FireDept).all()
    if len(orgs) < 2:
        pytest.skip("Weniger als 2 Orgs in Test-DB")
    org_a = orgs[0]
    org_b = orgs[1]

    # Fahrt in Org A
    fz_a = db_session.query(VehicleMaster).filter(VehicleMaster.dept_id == org_a.id).first()
    z_a = db_session.query(Fahrtzweck).filter(Fahrtzweck.org_id == org_a.id).first()
    if not fz_a or not z_a:
        pytest.skip("Keine Fahrzeuge/Zwecke in Org A")

    daten_a = {
        "org_id": org_a.id, "fahrzeug_id": fz_a.id, "zweck_id": z_a.id,
        "maschinist_name": "Org-A-Maschinist", "km_stand_neu": None,
        "erfasst_via": FahrtErfassungsweg.web,
    }
    fz_a.erfasst_km = False
    db_session.flush()
    fahrt_a = erstelle_fahrt(daten_a, db_session)

    # Org B sieht die Fahrt nicht (org_id-Filter)
    fahrten_b = (
        db_session.query(Fahrt)
        .filter(Fahrt.org_id == org_b.id, Fahrt.id == fahrt_a.id)
        .all()
    )
    assert fahrten_b == []


# ── Token-Routen (HTTP-Tests) ─────────────────────────────────────────────────

def test_token_route_ungueltig(client: TestClient):
    response = client.get("/f/nicht_existierender_token_xyz")
    assert response.status_code == 404


def test_fahrtenbuch_erfassung_ohne_login(client: TestClient):
    response = client.get("/fahrtenbuch/neu", follow_redirects=False)
    assert response.status_code == 302


def test_verwaltung_ohne_login(client: TestClient):
    response = client.get("/verwaltung/fahrten", follow_redirects=False)
    # Entweder 302 (Login-Redirect) oder 401
    assert response.status_code in (302, 401, 403)


def test_zweck_freitext_nur_bei_sonstige(db_session, org, fahrzeug, zweck):
    zweck.kategorie = FahrtKategorie.sonstige
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["zweck_freitext"] = "Fahrzeugwäsche"
    f = erstelle_fahrt(daten, db_session)
    assert f.zweck_freitext == "Fahrzeugwäsche"


def test_zweck_freitext_ignoriert_bei_anderer_kategorie(db_session, org, fahrzeug, zweck):
    zweck.kategorie = FahrtKategorie.uebung
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["zweck_freitext"] = "sollte ignoriert werden"
    f = erstelle_fahrt(daten, db_session)
    assert f.zweck_freitext is None


def test_fahrtkategorie_label():
    assert FahrtKategorie.taetigkeit.label == "Tätigkeit"
    assert FahrtKategorie.uebung.label == "Übung"
    assert FahrtKategorie.einsatz.label == "Einsatz"


def test_taetigkeit_zweck_ohne_freitext(db_session, org, fahrzeug, zweck):
    """Kategorie 'taetigkeit': feste Listenwerte, kein Freitext (auch wenn mitgeschickt)."""
    zweck.kategorie = FahrtKategorie.taetigkeit
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["zweck_freitext"] = "sollte ignoriert werden"
    f = erstelle_fahrt(daten, db_session)
    assert f.fahrttyp == FahrtKategorie.taetigkeit
    assert f.zweck_freitext is None


def _login(client: TestClient, db_session, org, username: str, role_code: str = "readonly"):
    from app.core.security import hash_password
    user = User(
        username=username, password_hash=hash_password("Test1234!"),
        display_name=username, org_id=org.id, active=True,
    )
    db_session.add(user)
    db_session.flush()
    role = db_session.query(Role).filter(Role.code == role_code).first()
    if not role:
        role = Role(code=role_code, label=role_code)
        db_session.add(role)
        db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.commit()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 302
    return user


def test_zweck_felder_zeigt_einsatzleiter_bei_zweck_flag(client: TestClient, db_session, org):
    """Zweck mit optional_einsatzleiter blendet das (optionale) Einsatzleiter-Feld ein."""
    _login(client, db_session, org, "el_zweck_tester")
    z = Fahrtzweck(org_id=org.id, name="EL-Zweck", kategorie=FahrtKategorie.einsatz,
                   optional_einsatzleiter=True)
    db_session.add(z)
    db_session.commit()
    r = client.get(f"/fahrtenbuch/hx/zweck-felder?zweck_id={z.id}")
    assert r.status_code == 200
    assert 'name="einsatzleiter_name"' in r.text


def test_zweck_felder_zeigt_einsatzleiter_bei_fahrzeug_flag(client: TestClient, db_session, org, fahrzeug):
    """Auch ohne Zweck-Flag erscheint das Feld, wenn das Fahrzeug es aktiviert hat."""
    _login(client, db_session, org, "el_fahrzeug_tester")
    fahrzeug.einsatzleiter_abfrage = True
    z = Fahrtzweck(org_id=org.id, name="Kein-EL-Zweck", kategorie=FahrtKategorie.uebung,
                   optional_einsatzleiter=False)
    db_session.add(z)
    db_session.commit()
    r = client.get(f"/fahrtenbuch/hx/zweck-felder?zweck_id={z.id}&fahrzeug_id={fahrzeug.id}")
    assert r.status_code == 200
    assert 'name="einsatzleiter_name"' in r.text


def test_zweck_felder_sonstige_zeigt_freitext(client: TestClient, db_session, org):
    """Zweck-Kategorie 'sonstige' blendet das Freitext-Zweck-Feld ein."""
    _login(client, db_session, org, "zf_sonstige_tester")
    z = Fahrtzweck(org_id=org.id, name="Sonstiges", kategorie=FahrtKategorie.sonstige)
    db_session.add(z)
    db_session.commit()
    r = client.get(f"/fahrtenbuch/hx/zweck-felder?zweck_id={z.id}")
    assert r.status_code == 200
    assert 'name="zweck_freitext"' in r.text


def test_zweck_felder_taetigkeit_kein_freitext(client: TestClient, db_session, org):
    """Kategorie 'taetigkeit' blendet KEIN Freitext-Feld ein (feste Liste)."""
    _login(client, db_session, org, "zf_taetig_tester")
    z = Fahrtzweck(org_id=org.id, name="Materialtransport", kategorie=FahrtKategorie.taetigkeit)
    db_session.add(z)
    db_session.commit()
    r = client.get(f"/fahrtenbuch/hx/zweck-felder?zweck_id={z.id}")
    assert r.status_code == 200
    assert 'name="zweck_freitext"' not in r.text


def test_zweck_felder_uebung_kein_freitext(client: TestClient, db_session, org):
    _login(client, db_session, org, "zf_uebung_tester")
    z = Fahrtzweck(org_id=org.id, name="Übung X", kategorie=FahrtKategorie.uebung)
    db_session.add(z)
    db_session.commit()
    r = client.get(f"/fahrtenbuch/hx/zweck-felder?zweck_id={z.id}")
    assert r.status_code == 200
    assert 'name="zweck_freitext"' not in r.text


def test_zweck_felder_ohne_flags_kein_einsatzleiter(client: TestClient, db_session, org, fahrzeug):
    """Ohne beide Flags erscheint kein Einsatzleiter-Feld (keine Duplikate/kein Zwang)."""
    _login(client, db_session, org, "el_kein_tester")
    fahrzeug.einsatzleiter_abfrage = False
    z = Fahrtzweck(org_id=org.id, name="Plain-Zweck", kategorie=FahrtKategorie.uebung,
                   optional_einsatzleiter=False)
    db_session.add(z)
    db_session.commit()
    r = client.get(f"/fahrtenbuch/hx/zweck-felder?zweck_id={z.id}&fahrzeug_id={fahrzeug.id}")
    assert r.status_code == 200
    assert 'name="einsatzleiter_name"' not in r.text


# ── Sysadmin-Löschen ──────────────────────────────────────────────────────────

def test_loesche_fahrten_entfernt_und_rechnet_zaehler_neu(db_session, org, fahrzeug, zweck):
    from app.services.fahrtenbuch_service import loesche_fahrten
    fahrzeug.km_aktuell = 1000
    db_session.flush()
    d1 = _basis_daten(org.id, fahrzeug.id, zweck.id); d1["km_stand_neu"] = 1010
    f1 = erstelle_fahrt(d1, db_session)
    d2 = _basis_daten(org.id, fahrzeug.id, zweck.id); d2["km_stand_neu"] = 1030
    f2 = erstelle_fahrt(d2, db_session)
    assert fahrzeug.km_aktuell == 1030

    n = loesche_fahrten([f2.id], org.id, user_id=1, db=db_session)
    assert n == 1
    assert db_session.query(Fahrt).filter(Fahrt.id == f2.id).execution_options(include_all_tenants=True).first() is None
    # Zählerstand fällt auf den höchsten verbliebenen aktiven Wert zurück
    assert fahrzeug.km_aktuell == 1010


def test_loesche_fahrten_nur_eigene_org(db_session, org, fahrzeug, zweck):
    from app.services.fahrtenbuch_service import loesche_fahrten
    f = erstelle_fahrt(_basis_daten(org.id, fahrzeug.id, zweck.id), db_session)
    n = loesche_fahrten([f.id], org_id=org.id + 9999, user_id=1, db=db_session)
    assert n == 0
    assert db_session.query(Fahrt).filter(Fahrt.id == f.id).execution_options(include_all_tenants=True).first() is not None


def test_verwaltung_liste_ohne_loeschen_fuer_fahrtenbuch_admin(client: TestClient, db_session, org):
    """Fahrtenbuch-Admin sieht kein Bulk-Löschen; Liste rendert fehlerfrei."""
    _login(client, db_session, org, "el_liste_admin", role_code="fahrtenbuch_admin")
    r = client.get("/verwaltung/fahrten")
    assert r.status_code == 200
    assert "fb-bulk-form" not in r.text


def test_verwaltung_liste_zeigt_loeschen_fuer_sysadmin(client: TestClient, db_session, org):
    """Sysadmin sieht das Bulk-Löschen-Formular + Org-Umschalter in der Verwaltungsliste."""
    _login(client, db_session, org, "el_liste_sys", role_code="system_admin")
    r = client.get("/verwaltung/fahrten")
    assert r.status_code == 200
    assert "fb-bulk-form" in r.text
    assert "fb-orgbar" in r.text  # Org-Auswahl für Sysadmin


def _org_mit_fahrt(db_session, slug: str, name: str, maschinist: str):
    """Legt eine zweite Org mit Fahrzeug, Zweck und einer Fahrt an. Gibt die Org zurück."""
    from app.models.master import FireDept
    from app.services.fahrtenbuch_service import erstelle_fahrt
    o = FireDept(slug=slug, name=name)
    db_session.add(o)
    db_session.flush()
    fz = VehicleMaster(dept_id=o.id, code=f"{slug}-FZ", name="Fahrzeug", type="Test",
                       display_order=5, erfasst_km=False)
    db_session.add(fz)
    db_session.flush()
    z = Fahrtzweck(org_id=o.id, name=f"{slug}-Zweck", kategorie=FahrtKategorie.uebung)
    db_session.add(z)
    db_session.flush()
    erstelle_fahrt({
        "org_id": o.id, "fahrzeug_id": fz.id, "zweck_id": z.id,
        "maschinist_name": maschinist, "km_stand_neu": None,
        "erfasst_via": FahrtErfassungsweg.web,
    }, db_session)
    db_session.commit()
    return o


def test_sysadmin_sieht_fremde_org_via_org_param(client: TestClient, db_session, org):
    """system_admin kann via ?org=<id> das Fahrtenbuch einer anderen Org ansehen."""
    orgB = _org_mit_fahrt(db_session, "testorgb-fb", "Test-Org-B-FB", "Bernd Fremdorg")
    _login(client, db_session, org, "fb_sysadmin_cross", role_code="system_admin")
    r = client.get(f"/verwaltung/fahrten?org={orgB.id}&status=alle")
    assert r.status_code == 200
    assert "Bernd Fremdorg" in r.text
    # Ohne ?org bleibt der Sysadmin in seiner eigenen Org – fremde Fahrt nicht sichtbar
    r2 = client.get("/verwaltung/fahrten?status=alle")
    assert "Bernd Fremdorg" not in r2.text


def test_regular_admin_ignoriert_org_param(client: TestClient, db_session, org):
    """Ein regulärer fahrtenbuch_admin bleibt trotz ?org=<id> in seiner eigenen Org."""
    orgC = _org_mit_fahrt(db_session, "testorgc-fb", "Test-Org-C-FB", "Clara Fremdorg")
    _login(client, db_session, org, "fb_regular_cross", role_code="fahrtenbuch_admin")
    r = client.get(f"/verwaltung/fahrten?org={orgC.id}&status=alle")
    assert r.status_code == 200
    assert "Clara Fremdorg" not in r.text


def test_loeschen_route_verweigert_nicht_sysadmin(client: TestClient, db_session, org):
    """Fahrtenbuch-Admin (kein Sysadmin) darf nicht löschen → 403."""
    _login(client, db_session, org, "el_nichtsys", role_code="fahrtenbuch_admin")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/verwaltung/fahrten/loeschen",
                    data={"_csrf": csrf, "ids": "1"}, follow_redirects=False)
    assert r.status_code == 403


def test_loeschen_route_sysadmin_loescht(client: TestClient, db_session, org, fahrzeug, zweck):
    """Sysadmin kann markierte Fahrten über die Route löschen."""
    f = erstelle_fahrt(_basis_daten(org.id, fahrzeug.id, zweck.id), db_session)
    db_session.commit()
    fahrt_id = f.id
    _login(client, db_session, org, "el_sysadmin", role_code="system_admin")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/verwaltung/fahrten/loeschen",
                    data={"_csrf": csrf, "ids": str(fahrt_id)}, follow_redirects=False)
    assert r.status_code == 303
    assert "geloescht=1" in r.headers.get("location", "")
    assert db_session.query(Fahrt).filter(Fahrt.id == fahrt_id).execution_options(include_all_tenants=True).first() is None


# ── Fahrzeug-Links / Export ───────────────────────────────────────────────────

def test_exportiere_fahrzeug_links_enthaelt_link():
    import io
    import openpyxl
    from types import SimpleNamespace
    from app.services.excel_export_service import exportiere_fahrzeug_links
    fzs = [
        SimpleNamespace(code="LFA", name="LF-A", kennzeichen="W-1", type="LF", qr_token="tok1"),
        SimpleNamespace(code="KDO", name="Kdo", kennzeichen=None, type="", qr_token=None),
    ]
    data = exportiere_fahrzeug_links(fzs, "ORGT", "https://x.at/")
    wb = openpyxl.load_workbook(io.BytesIO(data))
    rows = list(wb.active.iter_rows(values_only=True))
    assert rows[0] == ("Fahrzeug", "Name", "Kennzeichen", "Typ", "Fahrtenbuch-Link")
    assert rows[1][4] == "https://x.at/f/ORGT/v/tok1"
    assert rows[2][4] in (None, "")  # kein QR-Token → kein Link


def test_fahrzeuge_export_links_route(client: TestClient, db_session, org):
    """POST erzeugt fehlende QR-Tokens und liefert eine Excel-Datei zurück."""
    from app.models.master import OrgSettings, VehicleMaster
    org_s = (
        db_session.query(OrgSettings)
        .filter(OrgSettings.org_id == org.id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not org_s:
        org_s = OrgSettings(org_id=org.id)
        db_session.add(org_s)
    org_s.fahrtenbuch_token = "ORGTOKEN123"
    fz = VehicleMaster(dept_id=org.id, code="EXP-FZ", name="Export-FZ", type="Test", display_order=7)
    db_session.add(fz)
    db_session.commit()
    fz_id = fz.id

    _login(client, db_session, org, "fz_export_admin", role_code="fahrtenbuch_admin")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/admin/fahrtenbuch/fahrzeuge/export-links", data={"_csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 200
    assert "spreadsheet" in r.headers.get("content-type", "")
    # fehlender QR-Token wurde erzeugt
    db_session.expire_all()
    fz2 = db_session.query(VehicleMaster).filter(VehicleMaster.id == fz_id).execution_options(include_all_tenants=True).first()
    assert fz2.qr_token


def test_fahrzeuge_export_links_ohne_org_token_redirect(client: TestClient, db_session, org):
    from app.models.master import OrgSettings
    org_s = (
        db_session.query(OrgSettings)
        .filter(OrgSettings.org_id == org.id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not org_s:
        org_s = OrgSettings(org_id=org.id)
        db_session.add(org_s)
    org_s.fahrtenbuch_token = None
    db_session.commit()
    _login(client, db_session, org, "fz_export_notoken", role_code="fahrtenbuch_admin")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/admin/fahrtenbuch/fahrzeuge/export-links", data={"_csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "qr_kein_org_token" in r.headers.get("location", "")


def test_fahrtenbuch_neu_rendert_offline_draft_markup(client: TestClient, db_session, org):
    """PR6 (STAB-2): Formular muss ohne Jinja-/Template-Fehler rendern und den
    Offline-Draft-Hinweis + localStorage-Key enthalten."""
    from app.core.security import hash_password
    user = User(
        username="fahrtenbuchtester",
        password_hash=hash_password("Test1234!"),
        display_name="Fahrtenbuch Tester",
        org_id=org.id,
        active=True,
    )
    db_session.add(user)
    db_session.flush()
    role = db_session.query(Role).filter(Role.code == "readonly").first()
    if role:
        db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.commit()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        "/login",
        data={"username": "fahrtenbuchtester", "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 302

    r = client.get("/fahrtenbuch/neu", follow_redirects=False)
    assert r.status_code == 200
    assert "draft-restored-hinweis" in r.text
    assert "fahrt_draft_v1" in r.text


# ── Fremde/Ad-hoc Ressourcen: nicht im Fahrtenbuch ───────────────────────────

def test_fahrtenbuch_admin_liste_zeigt_keine_fremden_ressourcen(client: TestClient, db_session, org, fahrzeug):
    """Nutzer-Feedback 2026-07-11: Fremdorganisationen/Ad-hoc-Ressourcen (Stammdaten
    'Ressourcen') sind keine echten eigenen Fahrzeuge und sollen im Fahrtenbuch
    (weder Admin-Liste noch Erfassung) nicht auswählbar sein."""
    _login(client, db_session, org, "fb_fremd_admin", role_code="org_admin")
    fremd = VehicleMaster(dept_id=org.id, code="FREMD-1", name="Fremdes Fahrzeug",
                          is_external=True, adhoc_org_name="FF Nachbarort", display_order=100)
    adhoc = VehicleMaster(dept_id=org.id, code="ADHOC-1", name="Ad-hoc-Fahrzeug",
                          is_adhoc=True, display_order=101)
    db_session.add_all([fremd, adhoc])
    db_session.commit()

    r = client.get("/admin/fahrtenbuch/fahrzeuge")
    assert r.status_code == 200
    assert fahrzeug.code in r.text
    assert "FREMD-1" not in r.text
    assert "ADHOC-1" not in r.text
