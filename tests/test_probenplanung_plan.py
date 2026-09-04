"""Routentests fuer Phase 6: Jahresplan, Kalender und Uebernahmen."""

from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import OrgSettings
from app.models.probenplanung import ProbeCheckliste, ProbeChecklistItem, ProbeNachbereitung
from app.models.teilnahme import Teilnahme, Termin
from tests.test_probenplanung_checkliste import ORG_ID, _flags, _login, _probe_anlegen, _probeart, _user


def _termin(probeart_id: int, titel: str, lokal: str, **werte) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        local = datetime.fromisoformat(lokal).replace(tzinfo=ZoneInfo("Europe/Vienna"))
        row = Termin(
            org_id=ORG_ID,
            typ="uebung",
            titel=titel,
            beginn=local.astimezone(UTC).replace(tzinfo=None),
            probeart_id=probeart_id,
            status=werte.pop("status", "geplant"),
            **werte,
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _leser(client, suffix: str) -> str:
    _user(f"phase6_{suffix}", "readonly")
    _flags()
    return _login(client, f"phase6_{suffix}")


def test_jahresfilter_und_default_in_org_zeitzone(client):
    _leser(client, "jahr")
    probeart_id = _probeart(0, "Phase6 Jahresart")
    _termin(probeart_id, "P6 nur 2025", "2025-06-10T19:00")
    _termin(probeart_id, "P6 nur 2026", "2026-06-10T19:00")

    response = client.get("/probenplanung?jahr=2025")
    assert response.status_code == 200
    assert "P6 nur 2025" in response.text
    assert "P6 nur 2026" not in response.text

    with patch("app.routers.ui_probenplanung.now_local") as mocked_now:
        mocked_now.return_value = datetime(2025, 12, 31, 23, 30, tzinfo=ZoneInfo("Pacific/Honolulu"))
        response = client.get("/probenplanung")
    assert "Probenplan 2025" in response.text
    assert "P6 nur 2025" in response.text


def test_suche_ueber_alle_vier_felder_und_kombifilter(client):
    _leser(client, "filter")
    art_a = _probeart(0, "Phase6 Filter A")
    art_b = _probeart(0, "Phase6 Filter B")
    ids = [
        _termin(art_a, "P6 Titel Nadel", "2026-03-02T19:00", thema="Basis", objekt="A", ort="B"),
        _termin(art_a, "P6 Thema", "2026-04-06T19:00", thema="Thema Nadel", objekt="A", ort="B"),
        _termin(art_a, "P6 Objekt", "2026-05-04T19:00", thema="Basis", objekt="Objekt Nadel", ort="B"),
        _termin(art_b, "P6 Ort", "2026-06-01T19:00", thema="Basis", objekt="A", ort="Ort Nadel"),
    ]
    assert ids
    searched = client.get("/probenplanung?jahr=2026&q=Nadel").text
    for title in ("P6 Titel Nadel", "P6 Thema", "P6 Objekt", "P6 Ort"):
        assert title in searched
    combined = client.get(
        f"/probenplanung?jahr=2026&probeart_id={art_a}&status=geplant&von=2026-04-01&bis=2026-05-31&q=Nadel&zeitraum=alle",
        headers={"HX-Request": "true"},
    )
    assert combined.status_code == 200
    assert 'id="probenplan-tabelle"' in combined.text
    assert "P6 Thema" in combined.text and "P6 Objekt" in combined.text
    assert "P6 Titel Nadel" not in combined.text and "P6 Ort" not in combined.text
    assert "<html" not in combined.text


def test_kalender_monatsgrenzen_und_lokaler_tag_2330(client):
    _leser(client, "kalender")
    probeart_id = _probeart(0, "Phase6 Kalenderart")
    _termin(probeart_id, "P6 Monatsanfang", "2026-03-01T00:15")
    _termin(probeart_id, "P6 Spaet am Abend", "2026-03-31T23:30")
    _termin(probeart_id, "P6 Folgemonat", "2026-04-01T00:15")
    response = client.get("/probenplanung/kalender?jahr=2026&monat=3")
    assert response.status_code == 200
    assert "P6 Monatsanfang" in response.text
    assert "P6 Spaet am Abend" in response.text
    assert "23:30" in response.text
    assert "P6 Folgemonat" not in response.text


def test_duplizieren_nimmt_stammdaten_aber_keine_folgedaten(client):
    _user("phase6_duplikat", "probenverwalter")
    _flags()
    csrf = _login(client, "phase6_duplikat")
    probeart_id = _probeart(0, "Phase6 Duplikatart")
    source_id = _probe_anlegen(client, csrf, probeart_id, "P6 Original")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        source = db.get(Termin, source_id)
        source.thema = "Atemschutz"
        source.objekt = "Lagerhalle"
        source.ort = "Industriestraße"
        source.beschreibung = "Beschreibung"
        source.exercise_incident_id = 999999
        db.add(Teilnahme(org_id=ORG_ID, bezug_typ="uebung", bezug_id=source_id, freitext_name="Gast"))
        db.add(ProbeNachbereitung(org_id=ORG_ID, termin_id=source_id, bemerkungen="Auswertung"))
        db.commit()
    finally:
        db.close()
    response = client.post(
        f"/probenplanung/{source_id}/duplizieren", data={"_csrf": csrf}, follow_redirects=False
    )
    assert response.status_code == 303
    clone_id = int(response.headers["location"].rsplit("/", 1)[1])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        clone = db.get(Termin, clone_id)
        assert (clone.probeart_id, clone.thema, clone.objekt, clone.ort, clone.beschreibung) == (
            probeart_id, "Atemschutz", "Lagerhalle", "Industriestraße", "Beschreibung"
        )
        assert clone.status == "entwurf" and clone.exercise_incident_id is None
        assert db.query(Teilnahme).filter_by(bezug_typ="uebung", bezug_id=clone_id).count() == 0
        assert db.query(ProbeNachbereitung).filter_by(termin_id=clone_id).count() == 0
        checkliste = db.query(ProbeCheckliste).filter_by(termin_id=clone_id).first()
        assert checkliste is None or db.query(ProbeChecklistItem).filter_by(checkliste_id=checkliste.id).count() == 0
    finally:
        db.close()


def test_jahresuebernahme_verschiebt_wochentagsgleich_und_als_entwurf(client):
    _user("phase6_jahresuebernahme", "probenverwalter")
    _flags()
    csrf = _login(client, "phase6_jahresuebernahme")
    probeart_id = _probeart(0, "Phase6 Übernahmeart")
    source_id = _termin(probeart_id, "P6 Dritter Montag", "2025-03-17T19:30")
    response = client.post(
        "/probenplanung/jahr-uebernehmen",
        data={"_csrf": csrf, "quelljahr": "2025", "zieljahr": "2027"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        clone = db.query(Termin).filter(Termin.titel == "P6 Dritter Montag", Termin.id != source_id).one()
        local = clone.beginn.replace(tzinfo=UTC).astimezone(ZoneInfo("Europe/Vienna"))
        assert local.date().isoformat() == "2027-03-15"
        assert local.strftime("%H:%M") == "19:30"
        assert clone.status == "entwurf"
    finally:
        db.close()


def test_termine_redirect_aktiv_und_inaktiv(client):
    _leser(client, "redirect")
    probeart_id = _probeart(0, "Phase6 Redirectart")
    termin_id = _termin(probeart_id, "P6 Redirect", "2026-08-03T19:00")
    assert client.get("/termine", follow_redirects=False).headers["location"] == "/probenplanung"
    detail = client.get(f"/termine/{termin_id}", follow_redirects=False)
    assert detail.headers["location"] == f"/probenplanung/{termin_id}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter(OrgSettings.org_id == ORG_ID).one().probenplanung_modul_aktiv = False
        db.commit()
    finally:
        db.close()
    response = client.get("/termine", follow_redirects=False)
    assert response.status_code == 200
    assert "P6 Redirect" in response.text


def test_einsatz_mannschaftsseite_bleibt_funktionsfaehig(client):
    _user("phase6_mannschaft", "incident_leader")
    _flags()
    _login(client, "phase6_mannschaft")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="T1", status="active")
        db.add(incident)
        db.commit()
        incident_id = incident.id
    finally:
        db.close()
    response = client.get(f"/einsatz/{incident_id}/mannschaft")
    assert response.status_code == 200
    assert "Mannschaft" in response.text
