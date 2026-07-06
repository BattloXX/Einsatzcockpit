"""Objektverwaltung – Katalog-/Symbol-Verwaltung + Detail-Tabs.

PR1: Detailseite mit Tabs (Übersicht / Lagekarte / Dokumente), Karte als
editierbarer Inline-Tab.
PR2: pflegbare Auswahllisten (Kontaktarten/Dokumentarten/Piktogramme).
"""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import (
    AUSWAHL_KONTAKTART,
    AUSWAHL_PIKTOGRAMM,
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektAuswahl,
    ObjektKontakt,
)
from app.models.user import Role, User, UserRole


@pytest.fixture(autouse=True)
def _no_login_ratelimit():
    """Deaktiviert das Login-Rate-Limit fuer diese Datei (mehrere HTTP-Logins je Lauf).

    Die geteilte slowapi-Instanz (10/min, IP-basiert) wuerde sonst im Gesamt-Lauf
    beim Bündel dieser Render-Tests 429 liefern. Kein Test prüft Rate-Limiting.
    """
    from app.core.rate_limit import limiter
    if limiter is None:
        yield
        return
    prev = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev


def _login(client, username, password):
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


def _setup_objekt(username, *, nummer, rollen=("org_admin", "objekt_verwalter")):
    """Legt User (mit Rollen), aktiviert das Objekt-Modul und ein freigegebenes Objekt an."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Verwaltung Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))

        sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org.id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org.id)
            db.add(os_row)
        os_row.objekt_module_enabled = True

        obj = Objekt(org_id=org.id, nummer=nummer, name="Tab-Test-Objekt",
                     status=OBJEKT_STATUS_FREIGEGEBEN, lat=47.4652, lng=9.7503)
        db.add(obj)
        db.commit()
        return org.id, obj.id
    finally:
        db.close()


def test_detail_zeigt_tabs(client):
    _, obj_id = _setup_objekt("verw_tabs_user", nummer=4711)
    _login(client, "verw_tabs_user", "Test1234!")

    r = client.get(f"/objekte/{obj_id}")
    assert r.status_code == 200, r.text[:500]
    # Tab-Leiste vorhanden, Karte/Dokumente lazy per Event
    assert "objekt-tabs" in r.text
    assert "objekt-tab-karte" in r.text
    assert "objekt-tab-dokumente" in r.text
    # Karte + Dokumente werden nicht mehr per hx-trigger=load geladen, sondern per Tab-Event
    assert f"/objekte/{obj_id}/karte/tab" in r.text
    assert 'hx-trigger="objekt-tab-karte from:body once"' in r.text


def test_karte_tab_editierbar_fuer_verwalter(client):
    _, obj_id = _setup_objekt("verw_karte_user", nummer=4712)
    _login(client, "verw_karte_user", "Test1234!")

    r = client.get(f"/objekte/{obj_id}/karte/tab")
    assert r.status_code == 200, r.text[:500]
    # Editierbarer Editor inkl. Symbolpalette und Editor-Canvas
    assert "oks-palette" in r.text
    assert "objekt-karte-tab" in r.text
    assert "var EDITIERBAR = true" in r.text


def test_karte_tab_readonly_fuer_leser(client):
    _, obj_id = _setup_objekt("verw_leser_user", nummer=4713, rollen=("readonly",))
    _login(client, "verw_leser_user", "Test1234!")

    r = client.get(f"/objekte/{obj_id}/karte/tab")
    assert r.status_code == 200, r.text[:500]
    # Leser sehen die Karte schreibgeschützt, ohne Palette
    assert "var EDITIERBAR = false" in r.text
    assert "oks-palette" not in r.text


# ── PR2: Auswahllisten ──────────────────────────────────────────────────────────

def test_lade_auswahl_fallback_und_seed():
    from app.services.objekt_service import lade_auswahl, seed_objekt_auswahl

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        # Frisch: keine Zeilen -> Fallback auf die Modul-Konstante
        db.query(ObjektAuswahl).filter(ObjektAuswahl.org_id == org.id).delete()
        db.commit()
        fallback = lade_auswahl(db, org.id, AUSWAHL_KONTAKTART)
        assert "brandschutzbeauftragter" in fallback

        # Nach Seed: aus der DB (system-geschützt)
        seed_objekt_auswahl(db, org.id)
        db.commit()
        aus_db = lade_auswahl(db, org.id, AUSWAHL_PIKTOGRAMM)
        assert aus_db.get("ex") == "💥 EX-Bereich"  # Icon + Name kombiniert
        assert db.query(ObjektAuswahl).filter(
            ObjektAuswahl.org_id == org.id, ObjektAuswahl.system.is_(True)).count() >= 8
    finally:
        db.close()


def _verwaltung_setup(username, nummer):
    """User (org_admin), Objekt-Modul an, geseedete Auswahllisten + Symbole."""
    from app.services.objekt_service import seed_objekt_auswahl
    from app.services.objekt_symbol_service import seed_objekt_symbole

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Admin", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "org_admin").id))
        sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org.id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org.id)
            db.add(os_row)
        os_row.objekt_module_enabled = True
        seed_objekt_auswahl(db, org.id)
        seed_objekt_symbole(db, org.id)
        obj = Objekt(org_id=org.id, nummer=nummer, name="Verw-Objekt",
                     status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(obj)
        db.commit()
        return org.id, obj.id
    finally:
        db.close()


def test_kataloge_zeigt_neue_tabs(client):
    _verwaltung_setup("verw_admin_tabs", nummer=5001)
    _login(client, "verw_admin_tabs", "Test1234!")
    r = client.get("/objekte/kataloge")
    assert r.status_code == 200, r.text[:500]
    assert "Kontaktarten" in r.text
    assert "Dokumentarten" in r.text
    assert "Gefahren-Piktogramme" in r.text


def test_auswahl_crud_und_guards(client):
    org_id, _ = _verwaltung_setup("verw_admin_crud", nummer=5002)
    _login(client, "verw_admin_crud", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    # Neu anlegen (Kontaktart) -> Code aus Namen abgeleitet
    r = client.post("/objekte/kataloge/auswahl/kontaktart/neu",
                    data={"_csrf": csrf, "name": "Wach-Dienst", "sort": "9"},
                    follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        eintrag = db.query(ObjektAuswahl).filter(
            ObjektAuswahl.typ == "kontaktart", ObjektAuswahl.name == "Wach-Dienst").first()
        assert eintrag is not None
        assert eintrag.code == "wach_dienst"
        assert eintrag.system is False
        neu_id = eintrag.id

        # Standardeintrag darf nicht gelöscht werden
        sys_eintrag = db.query(ObjektAuswahl).filter(
            ObjektAuswahl.typ == "kontaktart", ObjektAuswahl.system.is_(True)).first()
        sys_id = sys_eintrag.id
    finally:
        db.close()

    r = client.post(f"/objekte/kataloge/auswahl/kontaktart/{sys_id}/loeschen",
                    data={"_csrf": csrf}, follow_redirects=False)
    assert r.headers["location"].endswith("error=system&tab=kontaktart")

    # In Benutzung -> Löschsperre
    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        obj = db.query(Objekt).filter(Objekt.nummer == 5002).first()
        db.add(ObjektKontakt(org_id=org_id, objekt_id=obj.id, art="wach_dienst", name="Herr X"))
        db.commit()
    finally:
        db.close()
    r = client.post(f"/objekte/kataloge/auswahl/kontaktart/{neu_id}/loeschen",
                    data={"_csrf": csrf}, follow_redirects=False)
    assert r.headers["location"].endswith("error=in_use&tab=kontaktart")


# ── PR3: Karten-Symbole + Bild-Upload ────────────────────────────────────────────

def test_symbol_seed_und_labels():
    from app.models.objekt import ObjektSymbol
    from app.services.objekt_symbol_service import lade_symbol_labels, seed_objekt_symbole

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        seed_objekt_symbole(db, org.id)
        db.commit()
        assert db.query(ObjektSymbol).filter(
            ObjektSymbol.org_id == org.id, ObjektSymbol.system.is_(True)).count() >= 20
        labels = lade_symbol_labels(db, org.id)
        assert labels.get("fsd") == "FSD / Schlüsselsafe"
    finally:
        db.close()


def test_sanitize_svg_entfernt_skript():
    from app.services.objekt_symbol_service import sanitize_svg
    roh = (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
           b'<script>alert(2)</script><rect width="10" height="10"/></svg>')
    sauber = sanitize_svg(roh)
    assert b"<script" not in sauber.lower()
    assert b"onload" not in sauber.lower()
    assert b"<rect" in sauber


def test_store_symbol_bild_validierung(tmp_path, monkeypatch):
    from app.config import settings
    from app.services import objekt_symbol_service as svc
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))

    # Falscher Typ
    try:
        svc.store_symbol_bild(1, 1, "x.gif", b"GIF89a")
        assert False, "GIF haette abgelehnt werden muessen"
    except ValueError:
        pass
    # Zu gross
    try:
        svc.store_symbol_bild(1, 1, "x.png", b"x" * (settings.OBJEKT_SYMBOL_MAX_BYTES + 1))
        assert False, "zu grosse Datei haette abgelehnt werden muessen"
    except ValueError:
        pass
    # SVG wird sanitisiert auf der Platte abgelegt
    rel = svc.store_symbol_bild(7, 42, "logo.svg",
                                b'<svg><script>x</script><circle r="1"/></svg>')
    inhalt = svc.symbol_bild_absolut(rel).read_bytes()
    assert b"<script" not in inhalt.lower()
    assert rel == "symbole/7/42.svg"


def test_karten_symbole_json(client):
    _verwaltung_setup("verw_symjson", nummer=5101)
    _login(client, "verw_symjson", "Test1234!")
    r = client.get("/objekte/karten-symbole.json")
    assert r.status_code == 200, r.text[:300]
    codes = {s["code"] for s in r.json()["symbole"]}
    assert "fsd" in codes and "gefahr_ex" in codes


def test_symbol_crud_und_guards(client):
    from app.models.objekt import ObjektKartenObjekt, ObjektSymbol
    org_id, obj_id = _verwaltung_setup("verw_symcrud", nummer=5102)
    _login(client, "verw_symcrud", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    # Neu (Textsymbol) -> Code aus Namen
    r = client.post("/objekte/kataloge/symbole/neu",
                    data={"_csrf": csrf, "name": "Tank-Anlage", "stil": "box", "text": "TA", "sort": "5"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text[:300]

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        sym = db.query(ObjektSymbol).filter(ObjektSymbol.code == "tank_anlage").first()
        assert sym is not None and sym.text == "TA" and sym.system is False
        neu_id = sym.id
        sys_sym = db.query(ObjektSymbol).filter(
            ObjektSymbol.code == "fsd", ObjektSymbol.org_id == org_id).first()
        sys_id = sys_sym.id
        # Symbol in Benutzung setzen
        db.add(ObjektKartenObjekt(org_id=org_id, objekt_id=obj_id, typ="tank_anlage",
                                  lat=47.0, lng=9.0))
        db.commit()
    finally:
        db.close()

    # Standardsymbol nicht löschbar
    r = client.post(f"/objekte/kataloge/symbole/{sys_id}/loeschen",
                    data={"_csrf": csrf}, follow_redirects=False)
    assert r.headers["location"].endswith("error=system&tab=symbole")
    # In Benutzung -> Löschsperre
    r = client.post(f"/objekte/kataloge/symbole/{neu_id}/loeschen",
                    data={"_csrf": csrf}, follow_redirects=False)
    assert r.headers["location"].endswith("error=in_use&tab=symbole")


def test_symbol_bild_upload_und_auslieferung(client, tmp_path, monkeypatch):
    from app.config import settings
    from app.models.objekt import ObjektSymbol
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))

    org_id, _ = _verwaltung_setup("verw_symbild", nummer=5103)
    _login(client, "verw_symbild", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script><rect width="8" height="8"/></svg>'
    r = client.post("/objekte/kataloge/symbole/neu",
                    data={"_csrf": csrf, "name": "Eigenes Symbol", "stil": "bild", "sort": "1"},
                    files={"bild": ("logo.svg", svg, "image/svg+xml")},
                    follow_redirects=False)
    assert r.status_code == 303, r.text[:300]

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        sym = db.query(ObjektSymbol).filter(ObjektSymbol.code == "eigenes_symbol").first()
        assert sym is not None and sym.stil == "bild" and sym.bild_pfad
        sym_id = sym.id
    finally:
        db.close()

    # Geschuetzte Auslieferung mit CSP-Header, sanitisiert
    r = client.get(f"/objekt-medien/symbol/{sym_id}")
    assert r.status_code == 200
    assert "script-src 'none'" in r.headers.get("content-security-policy", "") \
        or "default-src 'none'" in r.headers.get("content-security-policy", "")
    assert b"<script" not in r.content.lower()

    # Katalog-JSON enthaelt die Bild-URL
    r = client.get("/objekte/karten-symbole.json")
    eintrag = next(s for s in r.json()["symbole"] if s["code"] == "eigenes_symbol")
    assert eintrag["bild"] == f"/objekt-medien/symbol/{sym_id}"
