"""Dialog-Uebernahme: HTTP, Rechte, Snapshots und Einsatzkarten."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentColumn, Message, Task
from app.models.katplan import KatplanDokument, KatplanDokumentSeite, KatplanElement, KatplanPlan
from app.models.master import FireDept
from app.models.user import User
from app.services import katplan_anhang_service as anhang_service
from app.services import katplan_freigabe_service as freigabe
from app.services import katplan_uebernahme_service as service
from tests import test_katplan_k1b as basis_fixtures
from tests import test_katplan_k6a as medien_fixtures
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _user
from tests.test_katplan_k1b import login, post

umgebung = basis_fixtures.umgebung
storage = medien_fixtures.storage


@pytest.fixture
def daten(client, umgebung, monkeypatch):
    broadcast = AsyncMock()
    monkeypatch.setattr(service, "broadcast_uebernahme", broadcast)
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        element = KatplanElement(org_id=plan.org_id, plan_id=plan.id, typ="massnahme",
                                kennung="M-1", titel="Sperren", beschreibung_html="<p>Sperre prüfen</p>")
        incident = Incident(primary_org_id=plan.org_id, nummer=123, reason="Hochwasser",
                            address_street="Hauptplatz", is_exercise=True)
        db.add_all([element, incident])
        db.flush()
        incident.columns = [IncidentColumn(code=c, title=c, column_kind=c) for c in ("tasks", "messages")]
        admin = db.query(User).filter_by(username=umgebung["users"]["org_admin"]).one()
        version = freigabe.freigeben(db, plan, admin)
        db.flush()
        snapshot = freigabe.lade_version(db, plan)[2][0]
        name = f"recorder-{uuid4().hex}"
        _user(db, name, plan.org_id, "recorder")
        umgebung["users"]["recorder"] = name
        db.commit()
        info = {"path": f"/plaene/{plan.id}/elemente/{element.id}/uebernehmen",
                "snapshot_path": f"/plaene/{plan.id}/elemente/{snapshot.id}/uebernehmen?version_id={version.id}",
                "incident_id": incident.id, "element_id": element.id, "broadcast": broadcast}
    login(client, umgebung, "org_admin")
    return info


def senden(client, daten, **werte):
    return post(client, daten["path"], **{
        "art": "auftrag", "incident_id": daten["incident_id"], "titel": "M-1 Sperren",
        "text": "Prüfen", **werte})


def test_get_vorbelegung_und_button(client, umgebung, daten):
    response = client.get(daten["path"])
    assert response.status_code == 200
    assert 'value="M-1 Sperren"' in response.text
    assert "Sperre prüfen" in response.text and "Quelle:" in response.text
    assert "Hochwasser" in response.text and "Hauptplatz" in response.text and "Übung" in response.text
    page = client.get(f'/plaene/{umgebung["plan_id"]}?ansicht=arbeitsstand')
    assert page.status_code == 200
    assert "In Einsatz übernehmen" in page.text
    assert page.text.count('<dialog id="kp-uebernahme-dialog"') == 1
    released = client.get(f'/plaene/{umgebung["plan_id"]}')
    assert released.text.count('<dialog id="kp-uebernahme-dialog"') == 1
    assert "?version_id=" in released.text


@pytest.mark.parametrize("art,modell", [("auftrag", Task), ("meldung", Message)])
def test_post_legt_karte_an(client, umgebung, daten, art, modell):
    response = senden(client, daten, art=art, meldung_status="achtung")
    assert response.status_code == 200 and "erfolgreich übernommen" in response.text
    assert f'href="/einsatz/{daten["incident_id"]}"' in response.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        karte = db.query(modell).filter_by(incident_id=daten["incident_id"]).one()
        assert karte.title == "M-1 Sperren" and karte.detail == "Prüfen"
        assert (karte.source if art == "auftrag" else karte.status) == ("katplan" if art == "auftrag" else "achtung")
    daten["broadcast"].assert_awaited_once()


def test_leser_ohne_einsatzrolle(client, umgebung, daten):
    login(client, umgebung, "readonly")
    assert client.get(daten["path"]).status_code == 403
    assert senden(client, daten).status_code == 403
    page = client.get(f'/plaene/{umgebung["plan_id"]}')
    assert page.status_code == 200 and "In Einsatz übernehmen" not in page.text


def test_recorder_nutzt_snapshot(client, umgebung, daten):
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.get(KatplanElement, daten["element_id"]).titel = "Entwurf geheim"
        db.commit()
    login(client, umgebung, "recorder")
    response = client.get(daten["snapshot_path"])
    assert response.status_code == 200 and "M-1 Sperren" in response.text
    assert "Entwurf geheim" not in response.text
    response = post(client, daten["snapshot_path"], art="auftrag", incident_id=daten["incident_id"],
                    titel="Snapshot", text="Freigabe")
    assert response.status_code == 200


def test_fremder_und_geschlossener_einsatz(client, umgebung, daten):
    with TestingSession() as db:
        set_tenant_context(db, None)
        fremd = FireDept(slug=uuid4().hex, name="Andere")
        db.add(fremd)
        db.flush()
        incident = Incident(primary_org_id=fremd.id)
        db.add(incident)
        db.get(Incident, daten["incident_id"]).status = "closed"
        db.commit()
        fremde_id = incident.id
    response = senden(client, daten, incident_id=fremde_id)
    assert response.status_code == 404 and "Einsatz nicht gefunden" in response.text
    response = senden(client, daten)
    assert response.status_code == 409 and "Einsatz ist geschlossen" in response.text
    assert 'id="kp-uebernahme-inhalt"' in response.text
    response = client.get(daten["path"])
    assert "Kein aktiver Einsatz" in response.text and "disabled" in response.text
    daten["broadcast"].assert_not_awaited()


def test_ungueltiges_formular(client, umgebung, daten):
    response = senden(client, daten, incident_id="kaputt", titel="Eingabe bleibt")
    assert response.status_code == 422 and "Eingabe bleibt" in response.text
    assert daten["broadcast"].await_count == 0


def test_dateifehler_quellseiten_und_mehrfachauswahl(client, umgebung, daten, storage):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        element = db.get(KatplanElement, daten["element_id"])
        admin = db.query(User).filter_by(username=umgebung["users"]["org_admin"]).one()
        uuids = []
        for name in ("eins.png", "zwei.png"):
            anhang = anhang_service.anhang_hochladen(
                db, plan, admin, medien_fixtures.png(), name, "image/png", element=element)
            uuids.append(anhang.uuid)
            (storage / anhang.pfad).unlink()
        dokument = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel="Quellplan",
                                  dateiname_original="plan.pdf", pfad="fehlt.pdf", groesse_bytes=100, seitenzahl=2)
        db.add(dokument)
        db.flush()
        for nr in (1, 2):
            db.add(KatplanDokumentSeite(org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument.id,
                                        seiten_nr=nr, einzel_pdf_pfad="fehlt.pdf"))
        element.quelle_dokument_id, element.quelle_seiten = dokument.id, "1-2"
        db.commit()
    response = client.get(daten["path"])
    assert response.status_code == 200
    assert response.text.count('name="anhang_uuids"') == 2
    assert response.text.count('name="quellseiten"') == 2
    assert "/seite/1/thumb" in response.text
    response = senden(client, daten, anhang_uuids=uuids, quellseiten=["1", "2"])
    assert response.status_code == 200 and "erfolgreich übernommen" in response.text
    assert "Dateien konnten teilweise nicht übernommen werden" in response.text
    assert all(name in response.text for name in ("eins.png", "zwei.png", "Quellplan, S. 1", "Quellplan, S. 2"))
    daten["broadcast"].assert_awaited_once()


def test_kandidaten_nur_einmal_pro_seite(client, umgebung, daten, monkeypatch):
    from unittest.mock import Mock

    kandidaten = Mock(wraps=service.kandidaten_einsaetze)
    monkeypatch.setattr(service, "kandidaten_einsaetze", kandidaten)
    for suffix in ("?ansicht=arbeitsstand", "", "/versionen"):
        kandidaten.reset_mock()
        assert client.get(f'/plaene/{umgebung["plan_id"]}{suffix}').status_code == 200
        kandidaten.assert_called_once()


def test_csrf_erforderlich(client, umgebung, daten):
    response = client.post(daten["path"], data={"art": "auftrag", "incident_id": daten["incident_id"], "titel": "Test"})
    assert response.status_code == 403
    daten["broadcast"].assert_not_awaited()
