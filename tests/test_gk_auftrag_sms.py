"""Unit tests for dispatch SMS template rules."""

import pytest

from app.services.gk_zugang_service import STANDARD_AUFTRAG_NACHRICHT, auftrag_nachricht_validieren, sms_laenge


def test_auftrag_template_requires_link_and_known_placeholders():
    auftrag_nachricht_validieren(STANDARD_AUFTRAG_NACHRICHT)
    with pytest.raises(ValueError, match="Unbekannte"):
        auftrag_nachricht_validieren("Hallo {unbekannt} {link}")
    with pytest.raises(ValueError, match="erforderlich"):
        auftrag_nachricht_validieren("Hallo {einheit}")


def test_auftrag_template_length_and_gsm_counter():
    with pytest.raises(ValueError, match="480"):
        auftrag_nachricht_validieren("{link}" + "x" * 480)
    assert sms_laenge("Auftrag geaendert\n{link}") == (26, "GSM-7", 1)


# ── Route-Tests: Disposition, Änderung, Rückzug lösen genau die passenden SMS aus ──────────────

from types import SimpleNamespace  # noqa: E402
from uuid import uuid4  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.core.tenant import set_tenant_context  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.major_incident import (  # noqa: E402
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageEinheitLeader,
    LageEinheitZugang,
    LageEinheitZugangVersand,
    MajorIncident,
    SitePhase,
)
from app.models.master import FireDept, OrgSettings  # noqa: E402
from app.models.sms import SmsLog  # noqa: E402
from app.models.user import Role, User, UserRole  # noqa: E402
from app.services import gk_zugang_service  # noqa: E402
from tests.test_ressourcenkarte_routes import _login  # noqa: E402


def _aufbau(*, schalter=True, uebung=False, verifiziert=False):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        suffix = uuid4().hex[:8]
        org = FireDept(slug="asms-" + suffix, name="SMS", color="#f00", bos="FW")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, gk_zugang_aktiv=True, gk_auto_sms_auftrag=schalter))
        user = User(username="asms-" + suffix, password_hash=hash_password("Test1234!"), display_name="T",
                    org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == "recorder").one().id))
        lage = MajorIncident(org_id=org.id, name="Hochwasser", status="active", is_exercise=uebung)
        db.add(lage)
        db.flush()
        einheit = LageEinheit(lage_id=lage.id, label="TLF", status="bereitgestellt", resource_type="fahrzeug")
        site = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Riedweg 4", phase=SitePhase.disponiert)
        db.add_all([einheit, site])
        db.flush()
        leader = LageEinheitLeader(
            einheit_id=einheit.id, person_name="Max", phone_e164="+436641234567", phone_version=1, rolle="fuehrer",
            start_at=gk_zugang_service._now(),
            phone_verifiziert_at=gk_zugang_service._now() if verifiziert else None,
        )
        db.add(leader)
        db.flush()
        einheit.leader_assignment_id = leader.id
        db.commit()
        return SimpleNamespace(username=user.username, org=org.id, lage=lage.id, einheit=einheit.id, site=site.id)
    finally:
        db.close()


@pytest.fixture
def sms(monkeypatch):
    gesendet = []

    async def senden(org_id, nummer, text, **kwargs):
        gesendet.append((nummer, text))
        return SimpleNamespace(success=True, provider="test")

    monkeypatch.setattr(gk_zugang_service, "send_sms", senden)
    monkeypatch.setattr(gk_zugang_service, "sms_available", lambda org_id, db: True)
    return gesendet


def _post(client, pfad, **daten):
    return client.post(pfad, data={"_csrf": client.cookies.get("ec_csrf"), **daten})


def _versaende(a):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return [
            (v.ausloeser, v.status)
            for v in db.query(LageEinheitZugangVersand).filter_by(einheit_id=a.einheit).order_by(
                LageEinheitZugangVersand.id
            )
            if v.ausloeser.startswith("auftrag_")
        ]
    finally:
        db.close()


def test_disposition_aenderung_und_rueckzug_senden_genau_je_eine_sms(client, setup_db, sms):
    a = _aufbau()
    _login(client, a.username)
    basis = f"/lage/{a.lage}/stellen/{a.site}"
    assert _post(client, basis + "/einheit-disponieren", einheit_id=a.einheit, auftrag="Keller auspumpen").status_code < 400
    assert len(sms) == 1 and "Neuer Einsatzauftrag" in sms[0][1] and "Keller auspumpen" in sms[0][1]
    assert "/gk#gkz_" in sms[0][1]
    db = SessionLocal()
    set_tenant_context(db, None)
    dispatch_id = db.query(EinheitSiteDispatch).filter_by(einheit_id=a.einheit).one().id
    db.close()
    # gleicher Text erneut gespeichert -> keine weitere SMS
    _post(client, f"{basis}/einheit/{dispatch_id}/auftrag", auftrag="Keller auspumpen")
    assert len(sms) == 1
    # nur Reihenfolge -> keine SMS
    _post(client, f"{basis}/einheit/{dispatch_id}/auftrag", auftrag="Keller auspumpen", reihenfolge=2)
    assert len(sms) == 1
    # wesentliche Änderung -> Änderungs-SMS
    _post(client, f"{basis}/einheit/{dispatch_id}/auftrag", auftrag="Keller und Garage auspumpen")
    assert len(sms) == 2 and "Auftrag geaendert" in sms[1][1]
    # Rückzug -> dritte SMS
    _post(client, basis + "/einheit-abziehen", einheit_id=a.einheit, grund="Abgelöst")
    assert len(sms) == 3 and "zurueckgezogen" in sms[2][1]
    assert [s for _, s in _versaende(a)] == ["gesendet"] * 3
    # Der Zugang wurde nur einmal ausgestellt und nicht pro SMS rotiert.
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(LageEinheitZugang).filter_by(einheit_id=a.einheit, typ="personal").one().generation == 1
        logs = [s.text for s in db.query(SmsLog).filter_by(org_id=a.org, source="gk_auftrag")]
        assert logs and all("gkz_" not in t for t in logs)
    finally:
        db.close()


def test_schalter_aus_und_abgerueckt_senden_nichts(client, setup_db, sms):
    a = _aufbau(schalter=False)
    _login(client, a.username)
    _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-disponieren", einheit_id=a.einheit, auftrag="X")
    assert sms == [] and _versaende(a) == []


def test_uebung_unterdrueckt_sms_aber_disposition_gelingt(client, setup_db, sms, monkeypatch):
    monkeypatch.setattr(gk_zugang_service, "darf_extern", lambda *args, **kwargs: False)
    a = _aufbau(uebung=True)
    _login(client, a.username)
    antwort = _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-disponieren", einheit_id=a.einheit, auftrag="X")
    assert antwort.status_code < 400 and sms == []
    assert _versaende(a) == [("auftrag_neu", "uebersprungen")]


def test_provider_fehler_blockiert_disposition_nicht(client, setup_db, monkeypatch):
    async def kaputt(*args, **kwargs):
        raise RuntimeError("Gateway weg")

    monkeypatch.setattr(gk_zugang_service, "send_sms", kaputt)
    monkeypatch.setattr(gk_zugang_service, "sms_available", lambda org_id, db: True)
    a = _aufbau()
    _login(client, a.username)
    antwort = _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-disponieren", einheit_id=a.einheit, auftrag="X")
    assert antwort.status_code < 400
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(EinheitSiteDispatch).filter_by(einheit_id=a.einheit).count() == 1
    finally:
        db.close()
    assert _versaende(a) == [("auftrag_neu", "fehlgeschlagen")]


def test_altbestand_zufallstoken_wird_neu_ausgestellt_gueltiger_nicht(client, setup_db, sms):
    from app.core.security import hash_api_key

    a = _aufbau()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zugang = LageEinheitZugang(
            org_id=a.org, lage_id=a.lage, einheit_id=a.einheit, typ="personal", status="aktiv", generation=3,
            token_hash=hash_api_key("gkz_zufallsaltbestand"), laeuft_ab_at=gk_zugang_service._now().replace(year=2099),
            leader_id=db.query(LageEinheitLeader).filter_by(einheit_id=a.einheit).one().id, phone_version=1,
            created_at=gk_zugang_service._now(), updated_at=gk_zugang_service._now(),
        )
        db.add(zugang)
        db.commit()
    finally:
        db.close()
    _login(client, a.username)
    _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-disponieren", einheit_id=a.einheit, auftrag="A")
    assert len(sms) == 1
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=a.einheit, typ="personal").one()
        assert zugang.generation == 4
        token = sms[0][1].split("/gk#", 1)[1].split()[0]
        assert zugang.token_hash == hash_api_key(token) and token != "gkz_zufallsaltbestand"
        generation = zugang.generation
    finally:
        db.close()
    _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-abziehen", einheit_id=a.einheit)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(LageEinheitZugang).filter_by(einheit_id=a.einheit, typ="personal").one().generation == generation
    finally:
        db.close()


def test_fremde_org_einstellungen_beeinflussen_nicht(client, setup_db, sms):
    a = _aufbau(schalter=False)
    _aufbau(schalter=True)
    _login(client, a.username)
    _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-disponieren", einheit_id=a.einheit, auftrag="X")
    assert sms == []


def test_einstellungen_speichern_und_validieren_auftragsvorlage(client, setup_db):
    from tests.test_gk_einstellungen import _anmelden, _daten, _org_mit_admin

    org_id, username = _org_mit_admin(uuid4().hex[:10])
    _anmelden(client, username)
    csrf = client.cookies.get("ec_csrf")
    ok = client.post("/admin/gsl-einstellungen", data=_daten(
        _csrf=csrf, gk_zugang_aktiv="1", gk_auto_sms_auftrag="1",
        gk_auftrag_nachricht="{ereignis}: {einheit} {auftrag} {link}",
    ))
    assert ok.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgSettings).filter_by(org_id=org_id).one()
        assert cfg.gk_auto_sms_auftrag and cfg.gk_auftrag_nachricht == "{ereignis}: {einheit} {auftrag} {link}"
    finally:
        db.close()
    schlecht = client.post("/admin/gsl-einstellungen", data=_daten(
        _csrf=csrf, gk_zugang_aktiv="1", gk_auto_sms_auftrag="1", gk_auftrag_nachricht="ohne link {unbekannt}",
    ))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(OrgSettings).filter_by(org_id=org_id).one().gk_auftrag_nachricht == (
            "{ereignis}: {einheit} {auftrag} {link}"
        ), schlecht.status_code
    finally:
        db.close()
