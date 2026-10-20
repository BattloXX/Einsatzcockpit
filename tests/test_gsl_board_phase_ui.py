"""UI regressions for the GSL board's automatic phase workflow."""
from datetime import UTC, datetime

from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, SitePhase
from app.services import resource_service as rs
from tests.test_einheit_api import _daten as _api_daten
from tests.test_gsl_fuehrung_einheiten import _als, _broadcasts, _mit_rolle, _post


def _leader(client, data):
    _als(client, sign_session(_mit_rolle(data["admin"], "incident_leader")))


def test_board_renders_fertig_gemeldet_and_abgeschlossen_columns(client):
    data = _api_daten(); _leader(client, data)
    response = client.get(f"/lage/{data['lage']}")
    assert response.status_code == 200
    assert response.text.index("Fertig gemeldet") < response.text.index("Abgeschlossen")
    assert 'data-phase="fertig_gemeldet"' in response.text
    assert ">Erledigt<" not in response.text


def test_detail_buttons_dispatch_art_and_closed_site(client):
    data = _api_daten(); _leader(client, data)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        db.query(EinheitSiteDispatch).filter_by(site_id=data["a"]).update({"withdrawn_at": datetime.now(UTC)})
        db.get(IncidentSite, data["a"]).phase = SitePhase.eingegangen
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert "Erkundung zuweisen" in detail and "name=\"auftragsart\"" in detail
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        db.get(IncidentSite, data["a"]).phase = SitePhase.erledigt
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert "Einsatz wieder öffnen" in detail


def test_drag_assign_and_withdraw_are_scoped_by_art(client):
    data = _api_daten(); _leader(client, data)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        db.query(EinheitSiteDispatch).filter_by(site_id=data["a"]).update({"withdrawn_at": datetime.now(UTC)})
        db.get(IncidentSite, data["a"]).phase = SitePhase.eingegangen
        db.commit()
    finally:
        db.close()
    assign = f"/lage/{data['lage']}/stellen/{data['a']}/einheit-zuweisen"
    assert _post(client, assign, {"einheit_id": data["e1"]}).status_code == 204
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        site = db.get(IncidentSite, data["a"])
        dispatch = db.query(EinheitSiteDispatch).filter_by(
            einheit_id=data["e1"], site_id=site.id, withdrawn_at=None
        ).one()
        assert dispatch.auftragsart == "erkundung"
        rs.dispatch_to_site(db, data["e1"], data["lage"], site.id, auftragsart="abarbeitung")
        db.commit()
    finally:
        db.close()
    withdraw = f"/lage/{data['lage']}/stellen/{data['a']}/einheit-abziehen"
    assert _post(client, withdraw, {"einheit_id": data["e1"], "auftragsart": "erkundung"}).status_code == 200
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        rows = db.query(EinheitSiteDispatch).filter_by(einheit_id=data["e1"], site_id=data["a"]).all()
        assert any(row.auftragsart == "erkundung" and row.withdrawn_at for row in rows)
        assert any(row.auftragsart == "abarbeitung" and row.withdrawn_at is None for row in rows)
    finally:
        db.close()


def test_abschliessen_broadcasts_each_withdrawn_unit(client, monkeypatch):
    data = _api_daten(); _leader(client, data); events = _broadcasts(monkeypatch)
    close = f"/lage/{data['lage']}/stellen/{data['a']}/abschliessen"
    assert _post(client, close, {"aktive_zurueckziehen": "1"}).status_code == 204
    assert any(event["type"] == "site:card_changed" for event in events)
    assert any(event["type"] == "einheit:changed" for event in events)


# ── Ergänzungen: Primäraktion, Rechte, Labels, Karte, Drag & Drop, Smart Quotes ──

import re  # noqa: E402

_SMART_IN_ATTR = re.compile(r"""=\s*("[^"]*[“”„‘’][^"]*"|'[^']*[“”„‘’][^']*')""")


def _site_vorbereiten(data, phase, *, dispatches_weg=True):
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        if dispatches_weg:
            for row in db.query(EinheitSiteDispatch).filter_by(site_id=data["a"], withdrawn_at=None).all():
                row.withdrawn_at = datetime.now(UTC)
        db.get(IncidentSite, data["a"]).phase = phase
        db.commit()
    finally:
        db.close()


def _readonly(client, data):
    _als(client, sign_session(_mit_rolle(data["admin"], "readonly")))


def test_primaere_aktion_folgt_der_spalte(client):
    def knopf(detail, art, stil):
        return re.search(rf'data-auftragsart="{art}"[^>]*class="btn btn--sm btn--{stil}"', detail)

    data = _api_daten(); _leader(client, data)
    _site_vorbereiten(data, SitePhase.eingegangen)
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert knopf(detail, "erkundung", "primary") and knopf(detail, "abarbeitung", "secondary")
    assert '<input type="hidden" name="auftragsart" value="erkundung">' in detail
    # Jeder Button setzt die Art selbst – nicht auf den Submit-Button-Wert verlassen.
    assert "this.form.elements.auftragsart.value='erkundung'" in detail
    assert "this.form.elements.auftragsart.value='abarbeitung'" in detail
    _site_vorbereiten(data, SitePhase.bewertet)
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert knopf(detail, "abarbeitung", "primary") and knopf(detail, "erkundung", "secondary")
    assert '<input type="hidden" name="auftragsart" value="abarbeitung">' in detail


def test_abgeschlossene_stelle_ohne_disponieren_und_wiedereroeffnen_nur_fuer_fuehrung(client):
    data = _api_daten(); _leader(client, data)
    _site_vorbereiten(data, SitePhase.erledigt)
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert "Erkundung zuweisen" not in detail and "zuerst wieder öffnen" in detail
    assert "/wiedereroeffnen" in detail
    _readonly(client, data)
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert "Einsatz wieder öffnen" not in detail


def test_dispatchzeile_zeigt_erkundung_und_erkundet(client):
    data = _api_daten(); _leader(client, data)
    _site_vorbereiten(data, SitePhase.eingegangen)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        dispatch = rs.dispatch_to_site(db, data["e1"], data["lage"], data["a"], auftragsart="erkundung")
        dispatch.einheit_status, dispatch.beendet_at = "abgeschlossen", datetime.now(UTC)
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert re.search(r'class="res-badge res-badge--blue">\s*<svg\b[^>]*>.*?</svg> Erkundung</span>', detail, re.S)
    assert "Erkundet" in detail


def test_beendeten_auftrag_manuell_abziehen_und_wiedereroeffnen(client):
    data = _api_daten(); _leader(client, data)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        dispatch = db.get(EinheitSiteDispatch, data["d1"])
        dispatch.einheit_status = "abgeschlossen"
        dispatch.beendet_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()

    url = f"/lage/{data['lage']}/stellen/{data['a']}/einheit-abziehen"
    before = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert "Abziehen" in before and "Abgezogen" not in before
    response = _post(client, url, {"einheit_id": data["e1"]})
    assert response.status_code == 200 and "Abgezogen" in response.text
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        dispatch = db.get(EinheitSiteDispatch, data["d1"])
        assert dispatch.abgezogen_at is not None and dispatch.withdrawn_at is None
    finally:
        db.close()


def test_detail_unterscheidet_erkundet_von_fertig_gemeldeter_abarbeitung(client):
    data = _api_daten(); _leader(client, data)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        db.query(EinheitSiteDispatch).filter_by(site_id=data["a"]).update({"withdrawn_at": datetime.now(UTC)})
        erkundung = rs.dispatch_to_site(db, data["e1"], data["lage"], data["a"], auftragsart="erkundung")
        erkundung.einheit_status, erkundung.beendet_at = "abgeschlossen", datetime.now(UTC)
        db.get(IncidentSite, data["a"]).phase = SitePhase.bewertet
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert "Einsatz erkundet – disponieren?" in detail
    assert "Stelle abschließen?" not in detail
    assert "Einheit disponieren" in detail
    karte = client.get(f"/lage/{data['lage']}/stellen/{data['a']}/card").text
    assert "Erkundet – disponieren?" in karte

    db = SessionLocal(); set_tenant_context(db, None)
    try:
        abarbeitung = rs.dispatch_to_site(db, data["e1"], data["lage"], data["a"])
        abarbeitung.einheit_status, abarbeitung.beendet_at = "abgeschlossen", datetime.now(UTC)
        db.get(IncidentSite, data["a"]).phase = SitePhase.fertig_gemeldet
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert "Stelle abschließen?" in detail


def test_karte_fertig_gemeldet_zeigt_abschliessen_nur_fuer_fuehrung(client):
    data = _api_daten(); _leader(client, data)
    _site_vorbereiten(data, SitePhase.fertig_gemeldet)
    karte = client.get(f"/lage/{data['lage']}/stellen/{data['a']}/card").text
    assert f'hx-post="/lage/{data["lage"]}/stellen/{data["a"]}/abschliessen"' in karte
    assert "Einsatz abschließen" in karte
    _readonly(client, data)
    karte = client.get(f"/lage/{data['lage']}/stellen/{data['a']}/card").text
    assert "/abschliessen" not in karte


def test_drag_and_drop_bewertet_wird_abarbeitung_abgeschlossen_wird_abgelehnt(client):
    data = _api_daten(); _leader(client, data)
    _site_vorbereiten(data, SitePhase.bewertet)
    assign = f"/lage/{data['lage']}/stellen/{data['a']}/einheit-zuweisen"
    assert _post(client, assign, {"einheit_id": data["e1"]}).status_code == 204
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        dispatch = db.query(EinheitSiteDispatch).filter_by(
            einheit_id=data["e1"], site_id=data["a"], withdrawn_at=None
        ).one()
        assert dispatch.auftragsart == "abarbeitung"
        assert db.get(IncidentSite, data["a"]).phase == SitePhase.disponiert
    finally:
        db.close()
    _site_vorbereiten(data, SitePhase.erledigt)
    response = _post(client, assign, {"einheit_id": data["e1"]})
    assert response.status_code == 400 and "wieder öffnen" in response.text


def test_keine_smart_quotes_in_attributen(client):
    data = _api_daten(); _leader(client, data)
    _site_vorbereiten(data, SitePhase.fertig_gemeldet, dispatches_weg=False)
    for url in (f"/lage/{data['lage']}", f"/lage/{data['lage']}/stellen/{data['a']}",
                f"/lage/{data['lage']}/stellen/{data['a']}/card"):
        html = client.get(url).text
        assert not _SMART_IN_ATTR.findall(html), url


def test_board_laedt_versioniertes_skript_und_verarbeitet_eigene_phasenwechsel(client):
    # Phasen-Automatik verschiebt Karten auch bei eigenen Aktionen: lage_board.js darf das eigene
    # site_phase_changed-Echo nicht verwerfen, und das Skript muss versioniert geladen werden
    # (nginx cacht /static 10 Jahre).
    from pathlib import Path

    data = _api_daten(); _leader(client, data)
    html = client.get(f"/lage/{data['lage']}").text
    assert re.search(r'/static/js/lage_board\.js\?v=[^"]+"', html)
    js = Path("app/static/js/lage_board.js").read_text(encoding="utf-8")
    assert "msg.origin === clientId && msg.type !== 'site_phase_changed'" in js
