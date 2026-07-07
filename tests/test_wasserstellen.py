"""Tests für Wasserstellen-Stammdaten: MGI-Konvertierung, CSV-Import, Idempotenz,
Umkreis-Filter, OSM-Dedupe sowie Routen-Registrierung."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from app.models.wasserstelle import Wasserstelle
from app.services import wasserstelle_service as wss

# ── DB-Fixture (nutzt die von conftest angelegte test.db) ───────────────────────

@pytest.fixture
def db(setup_db):
    eng = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=eng)
    s = Session()
    set_tenant_context(s, None)
    try:
        yield s
    finally:
        s.rollback()
        s.close()


_ORG = 990101  # eigener Test-Org-Bereich (kein FK auf org_id)


# ── MGI EPSG:31281 → WGS84 ──────────────────────────────────────────────────────

def test_mgi_konvertierung_wolfurt_marktplatz():
    lat, lng = wss.mgi_zu_wgs84(-43760.4703, 259506.2998)
    # Wolfurt Marktplatz ≈ 47.4728 N, 9.7525 O
    assert lat is not None and lng is not None
    assert 47.46 < lat < 47.48
    assert 9.74 < lng < 9.76


# ── CSV-Parsing ─────────────────────────────────────────────────────────────────

_CSV = (
    b"OBJECTID;OBJ_ID;PUNKTNR;PROJEKTNR;GEOMETER;JAHR_EINME;GTYPE_ID;GRUPPE;"
    b"UNTERGRUPP;KURZBESCHR;HOEHE;DREHUNG;SKALIERUNG;RECHTSWERT;HOCHWERT;FARBE\n"
    b"Marktplatz;;;;;;;Wasser;Hydrant;;;;;-43760,4703;259506,2998;\n"
    b"Senderstrasse UF;;;;;;;Wasser;Unterflurhydrant;;;;;-44711,528;257548,546;\n"
    b"Saugstelle Frickenesch;;;;;;;Wasser;Saugstelle;;;;;-43267,3649615613;258907,583150749;\n"
    b"Loeschteich Schlossgasse;;;;;;;Wasser;Loeschteich;;;;;-43344,5059631736;259283,223880129;\n"
    b"Weberstrasse alt;;;;;;;Wasser;Hydrant geloescht;;;;;-44329,195;258168,703;\n"
    b"Kaputt;;;;;;;Wasser;Hydrant;;;;;;;\n"  # keine Koordinaten → Skip
)


def test_parse_csv_typen_und_geloescht():
    res = wss.parse_wasserstellen_csv(_CSV)
    typen = {e["bezeichnung"]: e["typ"] for e in res["eintraege"]}
    assert typen["Marktplatz"] == "ueberflur"
    assert typen["Senderstrasse UF"] == "unterflur"
    assert typen["Saugstelle Frickenesch"] == "saugstelle"
    assert typen["Loeschteich Schlossgasse"] == "loeschteich"
    # "Hydrant geloescht" → als inaktiv importiert, Typ trotzdem abgeleitet
    geloescht = next(e for e in res["eintraege"] if e["bezeichnung"] == "Weberstrasse alt")
    assert geloescht["aktiv"] is False
    assert geloescht["typ"] == "ueberflur"
    # Zeile ohne Koordinaten übersprungen
    assert res["gesamt"] == 5
    assert res["uebersprungen"] == 1


def test_parse_csv_dedupliziert_gleiche_zeile():
    doppelt = _CSV + b"Marktplatz;;;;;;;Wasser;Hydrant;;;;;-43760,4703;259506,2998;\n"
    res = wss.parse_wasserstellen_csv(doppelt)
    marktplatz = [e for e in res["eintraege"] if e["bezeichnung"] == "Marktplatz"]
    assert len(marktplatz) == 1


# ── Import (Idempotenz + Ersetzen) ──────────────────────────────────────────────

def test_import_idempotent_und_ersetzen(db):
    db.query(Wasserstelle).filter(Wasserstelle.org_id == _ORG).delete()
    db.commit()
    eintraege = wss.parse_wasserstellen_csv(_CSV)["eintraege"]

    r1 = wss.importiere_eintraege(db, _ORG, eintraege, user_id=None)
    db.commit()
    assert r1["neu"] == 5 and r1["aktualisiert"] == 0

    # Zweiter Lauf: nichts neu, alle aktualisiert (idempotent per import_key)
    r2 = wss.importiere_eintraege(db, _ORG, eintraege, user_id=None)
    db.commit()
    assert r2["neu"] == 0 and r2["aktualisiert"] == 5
    assert db.query(Wasserstelle).filter(Wasserstelle.org_id == _ORG).count() == 5

    # Ersetzen: alte Import-Daten weg, neu angelegt
    r3 = wss.importiere_eintraege(db, _ORG, eintraege[:2], user_id=None, ersetzen=True)
    db.commit()
    assert r3["neu"] == 2
    assert db.query(Wasserstelle).filter(Wasserstelle.org_id == _ORG).count() == 2

    db.query(Wasserstelle).filter(Wasserstelle.org_id == _ORG).delete()
    db.commit()


# ── Umkreis-Filter ──────────────────────────────────────────────────────────────

def test_lade_wasserstellen_im_umkreis(db):
    db.query(Wasserstelle).filter(Wasserstelle.org_id == _ORG).delete()
    db.commit()
    # nah (Wolfurt), fern (>2 km) und inaktiv
    db.add_all([
        Wasserstelle(org_id=_ORG, bezeichnung="Nah A", typ="ueberflur", lat=47.4652, lng=9.7503, aktiv=True),
        Wasserstelle(org_id=_ORG, bezeichnung="Nah B", typ="saugstelle", lat=47.4660, lng=9.7520, aktiv=True),
        Wasserstelle(org_id=_ORG, bezeichnung="Fern", typ="ueberflur", lat=47.5200, lng=9.8200, aktiv=True),
        Wasserstelle(org_id=_ORG, bezeichnung="Inaktiv", typ="ueberflur", lat=47.4653, lng=9.7504, aktiv=False),
    ])
    db.commit()

    res = wss.lade_wasserstellen_im_umkreis(db, _ORG, 47.4652, 9.7503, radius_m=800)
    namen = [r["ref"] for r in res]
    assert "Nah A" in namen and "Nah B" in namen
    assert "Fern" not in namen        # außerhalb Radius
    assert "Inaktiv" not in namen     # inaktiv ausgeblendet
    # sortiert nach Entfernung, Format-Felder vorhanden
    assert res[0]["entfernung_m"] <= res[-1]["entfernung_m"]
    saug = next(r for r in res if r["ref"] == "Nah B")
    assert saug["icon_kat"] == "loeschwasser"
    assert saug["quelle"] == "stammdaten"

    db.query(Wasserstelle).filter(Wasserstelle.org_id == _ORG).delete()
    db.commit()


# ── OSM-Dedupe ──────────────────────────────────────────────────────────────────

def test_dedupe_osm_gegen_stammdaten():
    stammdaten = [{"lat": 47.4652, "lng": 9.7503}]
    osm = [
        {"id": "osm-1", "lat": 47.46521, "lng": 9.75031},  # ~2 m → Duplikat, raus
        {"id": "osm-2", "lat": 47.4800, "lng": 9.7700},    # Nachbarort → bleibt
    ]
    res = wss.dedupe_osm_gegen_stammdaten(osm, stammdaten, schwelle_m=25)
    ids = [h["id"] for h in res]
    assert ids == ["osm-2"]
    # Ohne Stammdaten bleibt OSM unverändert
    assert wss.dedupe_osm_gegen_stammdaten(osm, []) == osm


# ── Routen-Registrierung ────────────────────────────────────────────────────────

def test_routen_registriert():
    from app.main import app
    pfade = {r.path for r in app.routes}
    assert "/admin/wasserstellen" in pfade
    assert "/admin/wasserstellen/import" in pfade
    assert "/einsatz/{incident_id}/nachbar-gefahren.json" in pfade


# ── Voll-Render der Admin-Seite (fängt Template-Laufzeitfehler) ─────────────────

def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def test_admin_seite_rendert(client):
    s = SessionLocal()
    set_tenant_context(s, None)
    try:
        org = s.query(FireDept).first()
        user = User(username="wss_admin", password_hash=hash_password("Test1234!"),
                    display_name="WSS Admin", org_id=org.id, active=True)
        s.add(user)
        s.flush()
        s.add(UserRole(user_id=user.id, role_id=_rolle(s, "org_admin").id))
        s.add(Wasserstelle(org_id=org.id, bezeichnung="Render Hydrant", typ="ueberflur",
                           lat=47.4652, lng=9.7503, ergiebigkeit_l_min=1200, aktiv=True,
                           quelle="manuell"))
        s.commit()
    finally:
        s.close()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post("/login", data={"username": "wss_admin", "password": "Test1234!", "_csrf": csrf},
                follow_redirects=False)

    r = client.get("/admin/wasserstellen")
    assert r.status_code == 200, r.text[:500]
    assert "Wasserstellen" in r.text
    assert 'id="ws-map"' in r.text            # Karte eingebunden
    assert "Render Hydrant" in r.text          # Registry-Zeile gerendert
    assert "wasserstellen_admin.js" in r.text  # Karten-JS geladen
