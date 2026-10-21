"""K3b: Snapshot-Lesesicht, Historie, Vergleich und Uebersichtsbadge."""

from uuid import uuid4

import pytest
from sqlalchemy import inspect

from app.core.tenant import set_tenant_context
from app.core.timezones import now_local
from app.models.katplan import KatplanAbschnitt, KatplanPhase, KatplanPlan, KatplanVersion, KatplanVersionElement
from app.models.user import User
from app.services import katplan_freigabe_service as service
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _user
from tests.test_katplan_k1b import form, login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, element_form, objekte
from tests.test_katplan_k3a import lade_plan


@pytest.fixture
def freigegeben(client, umgebung):
    login(client, umgebung)
    path = basis(umgebung)
    assert post(client, path + "/abschnitte", titel="Einsatzkapitel").status_code == 200
    assert post(client, path + "/phasen", name="Vorwarnung", farbe="gelb").status_code == 200
    kapitel = objekte(umgebung, KatplanAbschnitt)[0]
    phase = objekte(umgebung, KatplanPhase)[0]
    element = anlegen(client, umgebung, typ="info", abschnitt_id=kapitel.id)
    weg = anlegen(client, umgebung, typ="info")
    umgebung["entfernen_id"] = weg.id
    assert post(client, path + f"/elemente/{element.id}", **element_form(
        element, beschreibung_html="<p>Erster Absatz</p><p>Alter Absatz</p><p>Letzter Absatz</p>",
        tags="Wasser", phasen=[phase.id])).status_code == 200
    login(client, umgebung, "katplan_freigeber")
    assert post(client, path + "/freigeben", aenderungsnotiz="Erste Ausgabe").status_code == 200
    return umgebung, element, lade_plan(umgebung).aktuelle_version_id


def test_leser_bleibt_bei_snapshot_und_liste_filtert_snapshot(client, freigegeben):
    info, element, vid = freigegeben
    path = basis(info)
    login(client, info)
    plan = lade_plan(info)
    assert post(client, path + "/stammdaten", **form(
        titel="Unveröffentlichter Titel", gefahrenarten=["sturm"], version=plan.version)).status_code == 200
    assert post(client, path + f"/elemente/{element.id}", **element_form(
        element, version=1, titel="Unveröffentlichtes Element")).status_code == 200
    login(client, info, "readonly")
    page = client.get(path + "?ansicht=arbeitsstand")
    assert page.status_code == 200
    assert info["titel"] in page.text and "Brücke" in page.text and "Alter Absatz" in page.text
    assert "Unveröffentlichter Titel" not in page.text and "Unveröffentlichtes Element" not in page.text
    assert "Protokoll</button>" not in page.text and "GSL-Vorlage" not in page.text
    liste = client.get("/plaene?q=" + info["titel"] + "&gefahr=hochwasser")
    assert info["titel"] in liste.text and "Unveröffentlichter Titel" not in liste.text
    assert info["titel"] not in client.get("/plaene?gefahr=sturm").text
    assert info["titel"] in client.get("/plaene?status=freigegeben").text
    login(client, info)
    arbeit = client.get(path)
    assert "Unveröffentlichter Titel" in arbeit.text and "Unveröffentlichtes Element" in arbeit.text
    assert "Freigegebene Fassung" in arbeit.text and "Arbeitsstand" in arbeit.text
    assert "Unveröffentlichte Änderungen seit Version 1" in arbeit.text
    frei = client.get(path + "?ansicht=freigabe")
    assert info["titel"] in frei.text and "Unveröffentlichtes Element" not in frei.text.split('id="kp-protokoll"')[0]
    assert "Version: 1" in client.get("/plaene").text


def test_vertraulichkeit_und_filter_verzeichnis(client, freigegeben):
    info, _, v1 = freigegeben
    login(client, info)
    geheim = anlegen(client, info, typ="info")
    assert post(client, basis(info) + f"/elemente/{geheim.id}", **element_form(
        geheim, titel="Geheime Maßnahme", vertraulich="1", gsl_vorlage="1")).status_code == 200
    login(client, info, "katplan_freigeber")
    assert post(client, basis(info) + "/freigeben").status_code == 200
    login(client, info, "readonly")
    page = client.get(basis(info))
    assert "Geheime Maßnahme" not in page.text
    assert "Geheime Maßnahme" not in client.get(basis(info) + f"/vergleich?von={v1}").text
    assert "Geheime Maßnahme" not in client.get(basis(info) + f"/versionen/{lade_plan(info).aktuelle_version_id}").text
    for text in ('aria-label="Phasenfilter"', "Vorwarnung", "Alle", "Inhaltsverzeichnis", "Einsatzkapitel", "data-phasen="):
        assert text in page.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        name = "leitung-" + uuid4().hex
        _user(db, name, info["org_id"], "incident_leader")
        db.commit()
    info["users"]["incident_leader"] = name
    login(client, info, "incident_leader")
    page = client.get(basis(info))
    assert "Geheime Maßnahme" in page.text and "GSL-Vorlage" not in page.text
    assert "Geheime Maßnahme" in client.get(basis(info) + f"/vergleich?von={v1}").text
    login(client, info)
    assert "GSL-Vorlage" in client.get(basis(info)).text


def zweite_version(client, info, element):
    login(client, info)
    entfernt = anlegen(client, info, typ="info")
    # Dieses Element ist nur in der zweiten Freigabe vorhanden.
    assert post(client, basis(info) + f"/elemente/{element.id}", **element_form(
        element, version=1, titel="Geändert", beschreibung_html="<p>Erster Absatz</p><p>Neuer Absatz</p><p>Letzter Absatz</p>")).status_code == 200
    login(client, info, "katplan_freigeber")
    assert post(client, basis(info) + "/freigeben", aenderungsnotiz="Zweite Ausgabe").status_code == 200
    return lade_plan(info).aktuelle_version_id, entfernt


def test_historie_fremde_ids_und_archiv(client, freigegeben):
    info, element, v1 = freigegeben
    v2, _ = zweite_version(client, info, element)
    login(client, info, "readonly")
    path = basis(info)
    historie = client.get(path + "/versionen")
    assert historie.status_code == 200
    assert "Erste Ausgabe" in historie.text and "Zweite Ausgabe" in historie.text
    assert "Prüf-Verlauf" in historie.text and "Vergleichen mit vorheriger" in historie.text
    alt = client.get(path + f"/versionen/{v1}")
    assert alt.status_code == 200 and "Historische Version 1 – aktuell gilt Version 2" in alt.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        fremd_org = db.query(User).filter_by(username=info["users"]["fremd"]).one().org_id
        fremde_ids = []
        for org_id in (info["org_id"], fremd_org):
            fremd = KatplanPlan(org_id=org_id, titel="Fremd")
            db.add(fremd)
            db.flush()
            v = KatplanVersion(org_id=org_id, plan_id=fremd.id, versionsnummer=1, plan_json="{}",
                               inhalt_hash="fremd", anzahl_elemente=0)
            db.add(v)
            db.flush()
            fremde_ids.append(v.id)
        db.commit()
    for vid in fremde_ids:
        assert client.get(path + f"/versionen/{vid}").status_code == 404
        assert client.get(path + f"/vergleich?von={vid}&nach={v2}").status_code == 404
        assert client.get(path + f"/vergleich?von={v1}&nach={vid}").status_code == 404
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        db.get(KatplanPlan, info["plan_id"]).status = "archiviert"
        db.commit()
    for suffix in ("", "/versionen", f"/versionen/{v1}", "/vergleich"):
        assert client.get(path + suffix).status_code == 404
    login(client, info)
    assert client.get(path + "/versionen").status_code == 200


def test_vergleich_und_arbeitsstand_schreibt_nichts(client, freigegeben):
    info, element, v1 = freigegeben
    login(client, info)
    assert post(client, basis(info) + f"/elemente/{info['entfernen_id']}/loeschen").status_code == 200
    v2, _ = zweite_version(client, info, element)
    path = basis(info)
    login(client, info, "readonly")
    page = client.get(path + f"/vergleich?von={v1}&nach={v2}")
    assert page.status_code == 200
    for text in ("Neu</h2>", "Entfernt</h2>", "Geändert</h2>", "Beschreibung", "-Alter Absatz", "+Neuer Absatz", "Kopf-Änderungen", "Kapitel-Änderungen"):
        assert text in page.text
    assert client.get(path + "/vergleich?nach=arbeitsstand").status_code == 403
    login(client, info)
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        vorher = db.query(KatplanVersion).count(), db.query(KatplanVersionElement).count()
    assert client.get(path + "/vergleich?nach=arbeitsstand").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        assert (db.query(KatplanVersion).count(), db.query(KatplanVersionElement).count()) == vorher
        plan = db.get(KatplanPlan, info["plan_id"])
        with db.no_autoflush:
            _, snapshot = service.erstelle_snapshot(db, plan, flush=False)
        elemente = [KatplanVersionElement(org_id=plan.org_id, **e) for e in snapshot]
        assert all(inspect(e).transient for e in elemente)
        db.flush()
        assert db.query(KatplanVersionElement).count() == vorher[1]
    assert client.get(path + "/vergleich").status_code == 200
    assert client.get(path + "/vergleich?nach=kaputt").status_code == 404


def test_beschreibungsdiff_nur_geaenderter_absatz():
    diff = service._aenderungen(
        {"beschreibung_html": "<p>Erster Absatz</p><p>Alt</p><p>Dritter Absatz</p>"},
        {"beschreibung_html": "<p>Erster Absatz</p><p>Neu</p><p>Dritter Absatz</p>"},
    )[0]["diff"]
    assert [z for z in diff if z.startswith("-") and not z.startswith("---")] == ["-Alt"]
    assert [z for z in diff if z.startswith("+") and not z.startswith("+++")] == ["+Neu"]
    assert service._textzeilen("<h3>Titel</h3><p> Eins  zwei<br>Drei</p><ul><li>Vier</li></ul><h4>Ende</h4>") == [
        "Titel", "Eins zwei", "Drei", "Vier", "Ende"]


def test_badge_nur_fuer_berechtigte(client, freigegeben):
    info, _, _ = freigegeben
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        plan = db.get(KatplanPlan, info["plan_id"])
        plan.wiedervorlage_am = now_local().date()
        plan.status = "in_pruefung"
        db.commit()
    login(client, info, "katplan_freigeber")
    page = client.get("/")
    assert "1 fällige Wiedervorlagen · 1 in Prüfung" in page.text
    login(client, info, "readonly")
    assert "fällige Wiedervorlagen" not in client.get("/").text
