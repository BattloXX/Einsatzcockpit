"""Dreispaltiges ASÜW-Formular: aktuelle Zyklen, Papier-Rückfallebene und Druckwege."""
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.core.templating import templates
from app.models.breathing import BreathingDeployment, BreathingKontrolle, BreathingTroop, PressureLog, TroopMember
from app.models.gateway import DOC_ASUEW_FORMULAR, DOC_ASUEW_FORMULAR_LEER, DOCUMENT_TYPE_LABELS, PrintJob
from app.models.incident import Incident, IncidentChange
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.services.breathing_service import AsuewSettings, asuew_formular_seiten, load_asuew_formular_troops
from app.services.pdf_service import render_asuew_formular_pdf
from app.services.print_artifact_service import ArtifactError, render_job_pdf
from tests.test_breathing_routes import breathing_routes  # noqa: F401

NOW = datetime(2026, 11, 6, 23, 30, tzinfo=UTC)
SETTINGS = AsuewSettings(30, 60, 75, 30)


def troop(tid=1):
    return BreathingTroop(id=tid, name=f"Trupp {tid}", unit_name="Florian 1", task_text="Keller",
                          angriffsweg="Treppe", entry_at=NOW, ueberwacher_name="Aufsicht", members=[
        TroopMember(id=tid * 10, free_text_name="Anna", role="truppmann", start_press=290),
        TroopMember(id=tid * 10 + 1, free_text_name="Berta", role="truppfuehrer", start_press=300),
        TroopMember(id=tid * 10 + 2, free_text_name="Clara", role="truppmann", start_press=280),
    ])


def html_for(troops, org=None, settings=SETTINGS, leer=False):
    return templates.env.get_template("pdf/asuew_formular.html").render(
        seiten=asuew_formular_seiten(troops, settings, leer), incident=Incident(),
        org=org, user=SimpleNamespace(org=org), now=NOW, logo_url="", einsatzort="",
        einsatzdatum="", ueberwacher="", leer=leer,
    )


def test_pages_grouping_blank_and_names():
    assert len(asuew_formular_seiten([troop(i) for i in range(1, 4)], SETTINGS, False)) == 1
    pages = asuew_formular_seiten([troop(i) for i in range(1, 5)], SETTINGS, False)
    assert len(pages) == 2 and all(len(page) == 3 for page in pages)
    assert pages[1][1]["funkrufname"] == ""
    data = pages[0][0]
    assert data["tf"] == "Berta" and data["namen"] == ["Anna", "Clara"]
    assert data["startdruck"] == "TM 290 / TF 300 / TM 280"
    assert data["ziel"] == "Keller / Treppe"
    blank = asuew_formular_seiten([troop()], SETTINGS, True)
    assert len(blank) == 1 and len(blank[0]) == 3
    assert all(c["entry_at"] is None and not c["tf"] and not c["bemerkungen"] for c in blank[0])
    assert asuew_formular_seiten([], SETTINGS, False) == blank


def test_additional_members_and_current_cycle_only():
    t = troop()
    t.members.extend([TroopMember(free_text_name="Dora", role="truppmann"),
                      TroopMember(free_text_name="Alt", role="truppfuehrer", deployment_id=1)])
    t.deployments = [BreathingDeployment(lfd_nr=1)]
    page = asuew_formular_seiten([t, troop(2)], SETTINGS, False)[0]
    assert page[0]["namen"] == ["Anna", "Clara", "Dora"]
    assert len(page[1]["namen"]) == len(page[2]["namen"]) == 3
    assert page[0]["einsatz_nr"] == 2
    assert ">Alt<" not in html_for([t])
    assert "Einsatz Nr. 2" in html_for([t])


def test_controls_remarks_retroactive_and_timezone():
    t = troop()
    t.kontrollen = [
        BreathingKontrolle(stufe="one_third", art="funk", niedrigster_druck=180,
                          erledigt_at=NOW + timedelta(minutes=10), nachgetragen=True),
        BreathingKontrolle(stufe="two_third", art="kein_kontakt", erledigt_at=NOW + timedelta(minutes=20)),
        BreathingKontrolle(stufe="one_third", art="funk", niedrigster_druck=50,
                          erledigt_at=NOW - timedelta(days=1), deployment_id=1),
    ]
    t.members[0].objective_press, t.members[0].objective_press_at = 250, NOW + timedelta(minutes=2)
    t.withdraw_at, t.back_at = NOW + timedelta(minutes=21), NOW + timedelta(minutes=25)
    t.members[0].back_press = 100
    t.pressure_logs = [PressureLog(troop_member=t.members[0], troop_member_id=t.members[0].id,
                                  ts=NOW + timedelta(minutes=5), pressure_bar=220, nachgetragen=True)]
    t.notfall_at = NOW + timedelta(minutes=22)
    t.formular_audits = [IncidentChange(action="troop.meldung", ts=NOW + timedelta(minutes=6),
                                      after_json=json.dumps({"text": "Lage stabil", "nachgetragen": True}))]
    data = asuew_formular_seiten([t], SETTINGS, False)[0][0]
    assert data["kontrollen"][0][0]["text"] == "180 bar"
    assert len(data["kontrollen"][0]) == 1
    assert data["kontrollen"][1][0]["text"] == "kein Funkkontakt"
    org = SimpleNamespace(timezone="Asia/Tokyo", name="Test")
    html = html_for([t], org)
    for text in ["08:35 Anna 220 bar", "08:36 Lage stabil", "(nachgetragen)", "kein Funkkontakt",
                 "Einsatzziel erreicht: Anna 250 bar", "Zurück: Anna 100 bar", "Notfall", "Rückzug",
                 "1. Kontrolle nach 1/3", "Bemerkungen", "Überwacher"]:
        assert text in html
    assert "FAZ" not in html and ">50 bar<" not in html


def test_blank_interval_org_setting_and_ascii_quotes():
    html = html_for([troop()], settings=AsuewSettings(24, 60, 75, 30), leer=True)
    assert "Nach 8 Minuten" in html and "Nach 16 Minuten" in html
    assert "Anna" not in html and "Uhrzeit &nbsp; :" in html
    source = Path("app/templates/pdf/asuew_formular.html").read_text()
    assert not set('„“”‘’') & set(source)


@pytest.mark.parametrize("leer", [False, True])
def test_routes_real_pdf_and_print_renderer(breathing_routes, leer):  # noqa: F811
    c = breathing_routes
    url = f"/einsatz/{c.incident_id}/atemschutz/formular{'-leer' if leer else ''}.pdf"
    response = c.client.get(url)
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response.headers["content-disposition"].startswith('inline; filename="AS-Ueberwachung_')
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
    assert "Atemschutzüberwachung" in text and "FAZ" not in text
    assert ("Person A" in text) is (not leer)
    document = DOC_ASUEW_FORMULAR_LEER if leer else DOC_ASUEW_FORMULAR
    job = PrintJob(org_id=c.troop.incident.primary_org_id, incident_id=c.incident_id, document_type=document)
    assert render_job_pdf(c.db, job).startswith(b"%PDF")
    assert document in DOCUMENT_TYPE_LABELS


def test_routes_guards_order_and_board(breathing_routes):  # noqa: F811
    from app.routers.ui_breathing import router
    c = breathing_routes
    paths = [r.path for r in router.routes]
    for suffix in ("formular.pdf", "formular-leer.pdf"):
        assert paths.index(f"/einsatz/{{incident_id}}/atemschutz/{suffix}") < paths.index(
            "/einsatz/{incident_id}/atemschutz/{troop_id}/karte")
        assert c.client.get(f"/einsatz/99999999/atemschutz/{suffix}").status_code == 404
    page = c.client.get(f"/einsatz/{c.incident_id}/atemschutz").text
    assert "Formular (PDF)" in page and "Leeres Formular" in page and "asuew_formular_leer" in page
    foreign = FireDept(slug="foreign-formular", name="Fremd")
    c.db.add(foreign)
    c.db.flush()
    incident = Incident(primary_org_id=foreign.id)
    c.db.add(incident)
    c.db.commit()
    for suffix in ("formular.pdf", "formular-leer.pdf"):
        assert c.client.get(f"/einsatz/{incident.id}/atemschutz/{suffix}").status_code == 404
    c.db.add(OrgSettings(org_id=c.troop.incident.primary_org_id, atemschutz_ueberwachung_modul_aktiv=False))
    c.db.commit()
    for suffix in ("formular.pdf", "formular-leer.pdf"):
        assert c.client.get(f"/einsatz/{c.incident_id}/atemschutz/{suffix}").status_code == 404
    for document in (DOC_ASUEW_FORMULAR, DOC_ASUEW_FORMULAR_LEER):
        job = PrintJob(org_id=c.troop.incident.primary_org_id, incident_id=c.incident_id, document_type=document)
        with pytest.raises(ArtifactError):
            render_job_pdf(c.db, job)


def test_loader_excludes_audits_of_old_cycle(breathing_routes):  # noqa: F811
    c = breathing_routes
    for action, data in [("troop.meldung", {"text": "Alt"}), ("troop.redeployed", {}),
                         ("troop.meldung", {"text": "Aktuell", "nachgetragen": True})]:
        c.db.add(IncidentChange(incident_id=c.incident_id, entity_type="breathing_troop", entity_id=c.troop.id,
                                action=action, ts=NOW, after_json=json.dumps(data)))
    c.db.commit()
    loaded = load_asuew_formular_troops(c.db, c.incident_id)
    assert len(loaded) == 1
    data = asuew_formular_seiten(loaded, SETTINGS, False)[0][0]
    assert any(r["text"] == "Aktuell" and r["nachgetragen"] for r in data["bemerkungen"])
    assert not any(r["text"] == "Alt" for r in data["bemerkungen"])


def test_pdf_org_date_and_four_troops(monkeypatch):
    import weasyprint
    captured = []
    original = weasyprint.HTML

    def capture(*args, **kwargs):
        captured.append(kwargs["string"])
        return original(*args, **kwargs)

    monkeypatch.setattr(weasyprint, "HTML", capture)
    incident = Incident(started_at=NOW, address_street="Hauptstraße", address_no="1", address_city="Dorf")
    org = SimpleNamespace(timezone="Asia/Tokyo", name="FF Test", logo_path=None)
    pdf = render_asuew_formular_pdf([troop(i) for i in range(1, 5)], incident, org, SETTINGS)
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 2
    assert "Datum: 07.11.2026" in captured[0]
    assert "Einsatzort: Hauptstraße 1, Dorf" in captured[0]
    assert "Überwacher: Aufsicht" in captured[0]


def test_formular_xhtml2pdf_fallback(monkeypatch):
    import weasyprint

    def unavailable(*args, **kwargs):
        raise RuntimeError("WeasyPrint fehlt")

    monkeypatch.setattr(weasyprint, "HTML", unavailable)
    content = render_asuew_formular_pdf([], Incident(), None, SETTINGS, leer=True)
    assert content.startswith(b"%PDF")
    pages = PdfReader(io.BytesIO(content)).pages
    assert len(pages) == 1
    text = pages[0].extract_text()
    assert "Atemschutzüberwachung" in text and "Einsatzcockpit" in text
    assert text.index("Bemerkungen:") < text.index("Einsatzort:")
    content = render_asuew_formular_pdf([troop(i) for i in range(1, 5)], Incident(), None, SETTINGS)
    pages = PdfReader(io.BytesIO(content)).pages
    assert len(pages) == 2
    assert all(page.extract_text().count("Atemschutzüberwachung") == 1 for page in pages)


def test_empty_incident_org_settings_and_system_disabled(breathing_routes):  # noqa: F811
    c = breathing_routes
    org_id = c.troop.incident.primary_org_id
    c.db.add(OrgSettings(org_id=org_id, asuew_erwartete_einsatzzeit_min=24))
    c.db.commit()
    response = c.client.get(f"/einsatz/{c.other_incident_id}/atemschutz/formular-leer.pdf")
    assert response.status_code == 200
    text = PdfReader(io.BytesIO(response.content)).pages[0].extract_text()
    assert "Nach 8 Minuten" in text and "Nach 16 Minuten" in text
    flag = c.db.get(SystemSettings, "breathing_enabled")
    flag.value = "false"
    c.db.commit()
    for suffix in ("formular.pdf", "formular-leer.pdf"):
        assert c.client.get(f"/einsatz/{c.incident_id}/atemschutz/{suffix}").status_code == 404


def test_formular_requires_login(client):
    for suffix in ("formular.pdf", "formular-leer.pdf"):
        response = client.get(f"/einsatz/1/atemschutz/{suffix}", follow_redirects=False)
        assert response.status_code in (302, 404)


def test_blank_with_logo_stays_on_one_page():
    org = SimpleNamespace(name="Feuerwehr Beispiel", timezone="Europe/Vienna", logo_path=None)
    content = render_asuew_formular_pdf([], Incident(is_exercise=True), org, SETTINGS, leer=True,
                                       base_url=str(Path("app").resolve()))
    pages = PdfReader(io.BytesIO(content)).pages
    assert len(pages) == 1
    text = pages[0].extract_text()
    assert "Einsatzort:" in text and "Überwacher:" in text
    assert "ÜBUNG" not in text
