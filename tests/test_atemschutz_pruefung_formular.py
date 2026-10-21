"""Oeffentliches Token-Formular: AGT-Auswahl, Freitext und Fehler-Vorbelegung."""
import json
import re
from html.parser import HTMLParser
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.atemschutz_pruefung import AtemschutzGeraet, AtemschutzPruefung
from app.models.master import FireDept, Member, MemberQualification, OrgSettings, Qualification


class _Formular(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    @property
    def picker(self):
        return next(attrs for _, attrs in self.elements if attrs.get("class") == "person-select")

    @property
    def people(self):
        return json.loads(re.search(r"people: (\[.*?\]),\s*initialName:", self.picker["x-data"], re.S)[1])

    def input(self, name):
        return next(attrs for tag, attrs in self.elements if tag == "input" and attrs.get("name") == name)


@pytest.fixture
def formular(client):
    client.cookies.clear()
    token = uuid4().hex
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"ap-{token}", name="Formular Testfeuerwehr")
        andere_org = FireDept(slug=f"ap-fremd-{token}", name="Andere Testfeuerwehr")
        db.add_all([org, andere_org])
        db.flush()
        db.add(OrgSettings(org_id=org.id, atemschutz_pruefung_modul_aktiv=True, atemschutz_pruef_token=token))
        geraet = AtemschutzGeraet(org_id=org.id, nummer="AP-FORMULAR", aktiv=True)
        agt = Member(org_id=org.id, lastname="AGTTest", firstname="Anna", active=True)
        ohne_agt = Member(org_id=org.id, lastname="OhneQualifikation", firstname="Berta", active=True)
        fremd = Member(org_id=andere_org.id, lastname="GeheimerFremdtraeger", firstname="Clara", active=True)
        inaktiv = Member(org_id=org.id, lastname="InaktiverTraeger", firstname="Dora", active=False)
        db.add_all([geraet, agt, ohne_agt, fremd, inaktiv])
        qualifikation = db.query(Qualification).filter(Qualification.code == "AGT").first()
        if qualifikation is None:
            qualifikation = Qualification(code="AGT", label="Atemschutztraeger")
            db.add(qualifikation)
        db.flush()
        for member in (agt, fremd, inaktiv):
            db.add(MemberQualification(member_id=member.id, qualification_id=qualifikation.id))
        db.commit()
        daten = {"t": token, "geraet_id": str(geraet.id), "eingesetzt_am": "2026-10-21",
                 "einsatz_art": "uebung", "flaschendruck_bar": "300", "druckabfall_bar": "5",
                 "rueckzugssignal_bar": "55", "sichtpruefung_ok": "ok", "geraet_einsatzbereit_ok": "ok"}
        ids = {"org": org.id, "geraet": geraet.id, "agt": agt.id}
    finally:
        db.close()
    response = client.get(f"/ap/{token}")
    assert response.status_code == 200
    daten["_csrf"] = client.cookies.get("ec_csrf")
    return response, daten, ids


def test_token_formular_zeigt_nur_aktive_agt_der_token_org(formular):
    response, _, ids = formular
    html = _Formular(response.text)
    assert html.picker["data-required"] == "true"
    assert html.input("traeger_member_id")[":value"] == "memberId"
    assert any(tag == "script" and attrs.get("src", "").startswith("/static/js/person_select_flyout.js?v=")
               for tag, attrs in html.elements)
    assert html.people == [{"id": ids["agt"], "name": "AGTTest Anna", "lastname": "AGTTest", "firstname": "Anna"}]
    assert "Formular Testfeuerwehr" in response.text
    for verborgen in ("OhneQualifikation", "GeheimerFremdtraeger", "InaktiverTraeger"):
        assert verborgen not in response.text


@pytest.mark.parametrize("freitext", [False, True])
def test_token_formular_speichert_mitglied_oder_freitext(client, formular, freitext):
    _, daten, ids = formular
    if freitext:
        daten["traeger_free_text"] = "  Externer Traeger  "
    else:
        daten["traeger_member_id"] = str(ids["agt"])
    response = client.post("/atemschutz-pruefung", data=daten)
    assert response.status_code == 200
    assert "Prüfung gespeichert" in response.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        pruefung = db.query(AtemschutzPruefung).filter_by(org_id=ids["org"], geraet_id=ids["geraet"]).one()
        assert pruefung.traeger_member_id == (None if freitext else ids["agt"])
        assert pruefung.traeger_free_text == ("Externer Traeger" if freitext else None)
    finally:
        db.close()


def test_token_formular_ohne_traeger_rendert_komponente_erneut(client, formular):
    _, daten, ids = formular
    response = client.post("/atemschutz-pruefung", data=daten)
    assert response.status_code == 422
    assert "Bitte einen Atemschutzträger wählen oder eintragen." in response.text
    html = _Formular(response.text)
    assert html.input("traeger_member_id")[":value"] == "memberId"
    assert [person["id"] for person in html.people] == [ids["agt"]]


@pytest.mark.parametrize("freitext", [False, True])
def test_token_formular_behaelt_traeger_nach_validierungsfehler(client, formular, freitext):
    _, daten, ids = formular
    daten["eingesetzt_am"] = "ungueltig"
    if freitext:
        daten["traeger_free_text"] = "Externer Traeger"
    else:
        daten["traeger_member_id"] = str(ids["agt"])
    response = client.post("/atemschutz-pruefung", data=daten)
    assert response.status_code == 422
    html = _Formular(response.text)
    form_state = next(attrs["x-data"] for tag, attrs in html.elements if tag == "form" and "x-data" in attrs)
    assert f"traegerFreitext: {'true' if freitext else 'false'}" in form_state
    if freitext:
        assert html.input("traeger_free_text")["value"] == "Externer Traeger"
    else:
        assert 'initialName: "AGTTest Anna"' in html.picker["x-data"]
        assert f'initialId: "{ids["agt"]}"' in html.picker["x-data"]
