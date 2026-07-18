"""Cross-Org-Regressionsnetz für öffentliche Token-Routen (Audit A7 / SEC-11).

Anonyme Endpunkte laufen OHNE Tenant-Filter (dependencies.py, SEC-11) und
müssen selbst über ihren Token scopen. Diese Tests halten das fest: Ein
gültiger Token der Org A darf niemals Daten der Org B preisgeben.

Neue öffentliche Routen (Token/QR/PIN/Signatur) bitte hier mit einem
Cross-Org-Fall ergänzen (siehe CLAUDE.md, Abschnitt Tenant-Scoping).
"""
from datetime import UTC, datetime

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings, VehicleMaster
from app.models.objekt import AlarmInfoscreenToken

ORG_A = 1  # FF Wolfurt (seeded)

RAW_TOKEN_A = "iso-test-infoscreen-token-org-a"
RAW_TOKEN_B = "iso-test-infoscreen-token-org-b"
FAB_TOKEN_A = "iso-test-fahrtenbuch-a"
FAB_TOKEN_B = "iso-test-fahrtenbuch-b"
FAHRZEUG_B_CODE = "ISO-GEHEIM-TLF-B"  # Formular rendert fz.code, nicht fz.name


def _setup_zwei_orgs() -> int:
    """Org B mit aktivem Einsatz, Infoscreen-Tokens + Fahrtenbuch-Tokens für A und B.

    Idempotent (Session-DB wird zwischen Tests nicht zurückgesetzt).
    Returns org_b_id.
    """
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b = db.query(FireDept).filter(FireDept.slug == "iso-test-org-b").first()
        if org_b is None:
            org_b = FireDept(slug="iso-test-org-b", name="Isolationstest Org B")
            db.add(org_b)
            db.flush()

            db.add(Incident(
                primary_org_id=org_b.id,
                alarm_type_code="B3",
                status="active",
                address_street="Geheime Adresse der Org B",
                address_no="42",
                started_at=datetime.now(UTC).replace(tzinfo=None),
            ))
            for org_id, raw in ((ORG_A, RAW_TOKEN_A), (org_b.id, RAW_TOKEN_B)):
                db.add(AlarmInfoscreenToken(
                    org_id=org_id, token_hash=hash_api_key(raw),
                    name=f"Isolationstest {org_id}", aktiv=True,
                ))
            for org_id, fab in ((ORG_A, FAB_TOKEN_A), (org_b.id, FAB_TOKEN_B)):
                org_s = (db.query(OrgSettings).filter(OrgSettings.org_id == org_id)
                         .execution_options(include_all_tenants=True).first())
                if org_s is None:
                    org_s = OrgSettings(org_id=org_id)
                    db.add(org_s)
                org_s.fahrtenbuch_token = fab
                org_s.fahrtenbuch_modul_aktiv = True
            db.add(VehicleMaster(dept_id=org_b.id, code=FAHRZEUG_B_CODE, name="Isolationstest TLF B",
                                 active=True, deleted=False,
                                 is_adhoc=False, is_external=False))
            db.commit()
        return org_b.id
    finally:
        db.close()


# ── Alarm-Infoscreen (/infoscreen/alarm/{token}) ──────────────────────────────

def test_infoscreen_token_sieht_nur_eigene_org(client):
    _setup_zwei_orgs()

    # Der aktive Einsatz der Org B darf über den A-Token NICHT sichtbar werden.
    # (Kein Assert auf modus=="idle": andere Tests der Suite hinterlassen
    # aktive Einsätze in der seeded Org A — entscheidend ist die Isolation.)
    r_a = client.get(f"/infoscreen/alarm/{RAW_TOKEN_A}/daten")
    assert r_a.status_code == 200
    assert r_a.json()["org_name"] != "Isolationstest Org B"
    assert "Geheime Adresse der Org B" not in r_a.text

    # Org B sieht ihren eigenen Einsatz (modus "alarm").
    r_b = client.get(f"/infoscreen/alarm/{RAW_TOKEN_B}/daten")
    assert r_b.status_code == 200
    assert r_b.json()["modus"] == "alarm"
    assert "Geheime Adresse der Org B" in r_b.text


def test_infoscreen_unbekannter_token_abgelehnt(client):
    # 401 wird vom globalen Exception-Handler für Browser in einen
    # Login-Redirect übersetzt — beides gilt als "abgelehnt".
    r = client.get("/infoscreen/alarm/voellig-unbekannter-token/daten",
                   follow_redirects=False)
    assert r.status_code in (302, 401)
    assert "Geheime Adresse" not in r.text


def test_infoscreen_gesperrter_token_401(client):
    _setup_zwei_orgs()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = (db.query(AlarmInfoscreenToken)
               .filter(AlarmInfoscreenToken.token_hash == hash_api_key(RAW_TOKEN_A))
               .execution_options(include_all_tenants=True).one())
        row.aktiv = False
        db.commit()
        r = client.get(f"/infoscreen/alarm/{RAW_TOKEN_A}/daten",
                       follow_redirects=False)
        assert r.status_code in (302, 401)
    finally:
        row.aktiv = True
        db.commit()
        db.close()


# ── Fahrtenbuch-Erfassung ohne Login (/f/{token}) ─────────────────────────────

def test_fahrtenbuch_token_zeigt_nur_eigene_fahrzeuge(client):
    _setup_zwei_orgs()

    r_a = client.get(f"/f/{FAB_TOKEN_A}")
    assert r_a.status_code == 200
    assert FAHRZEUG_B_CODE not in r_a.text  # Fahrzeug der Org B unsichtbar für Org A

    r_b = client.get(f"/f/{FAB_TOKEN_B}")
    assert r_b.status_code == 200
    assert FAHRZEUG_B_CODE in r_b.text


def test_fahrtenbuch_unbekannter_token_404(client):
    r = client.get("/f/voellig-unbekannter-token")
    assert r.status_code == 404


# ── Förderstrecken-Maschinisten-Zettel ohne Login (/m/foerderstrecke/{token}) ──

FS_TOKEN_A = "iso-fs-token-org-a"
FS_TOKEN_B = "iso-fs-token-org-b"
FS_NAME_A = "ISO-Foerderstrecke-A"
FS_NAME_B = "ISO-GEHEIM-Foerderstrecke-B"


def _setup_fs_tokens() -> int:
    """Je eine Strecke + Maschinisten-Token in Org A und Org B (idempotent)."""
    import hashlib

    from app.models.foerderstrecke import (
        FoerderMaschinistToken,
        FoerderStation,
        Foerderstrecke,
    )
    org_b_id = _setup_zwei_orgs()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        def _ensure(org_id, name, token):
            vorhanden = db.query(FoerderMaschinistToken).filter(
                FoerderMaschinistToken.token_hash == hashlib.sha256(token.encode()).hexdigest()
            ).first()
            if vorhanden:
                return
            s = Foerderstrecke(org_id=org_id, name=name,
                               ansaug_json='{"seehoehe_m":430,"geodaetische_saughoehe_m":2}')
            db.add(s); db.flush()
            db.add(FoerderStation(org_id=org_id, strecke_id=s.id, sort=0, typ="quellpumpe",
                                  lat=47.4, lng=9.7))
            db.add(FoerderMaschinistToken(org_id=org_id, strecke_id=s.id,
                                          token_hash=hashlib.sha256(token.encode()).hexdigest()))
        _ensure(ORG_A, FS_NAME_A, FS_TOKEN_A)
        _ensure(org_b_id, FS_NAME_B, FS_TOKEN_B)
        db.commit()
        return org_b_id
    finally:
        db.close()


def test_foerderstrecke_token_zeigt_nur_eigene_org(client):
    _setup_fs_tokens()

    r_a = client.get(f"/m/foerderstrecke/{FS_TOKEN_A}")
    assert r_a.status_code == 200
    assert FS_NAME_A in r_a.text
    assert FS_NAME_B not in r_a.text            # Strecke der Org B unsichtbar für Token A

    r_b = client.get(f"/m/foerderstrecke/{FS_TOKEN_B}")
    assert r_b.status_code == 200
    assert FS_NAME_B in r_b.text
    assert FS_NAME_A not in r_b.text


def test_foerderstrecke_unbekannter_token_404(client):
    assert client.get("/m/foerderstrecke/voellig-unbekannter-token").status_code == 404
