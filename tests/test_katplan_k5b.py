"""K5b: Plankarte, gefilterte Freigabesicht und Uebersichtskarte."""

from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanElement, KatplanPhase, KatplanPlan
from app.models.user import User
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _user
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, objekte
from tests.test_katplan_k5a import POLYGON, PUNKT, speichern


@pytest.fixture
def kartenplan(client, umgebung):
    login(client, umgebung)
    punkt = anlegen(client, umgebung)
    polygon = anlegen(client, umgebung, typ="gefahrenbereich")
    geheim = anlegen(client, umgebung)
    ohne = anlegen(client, umgebung, typ="ablauf")
    assert speichern(client, umgebung, polygon, POLYGON).status_code == 200
    assert speichern(client, umgebung, geheim, PUNKT, vertraulich="1").status_code == 200
    assert post(client, basis(umgebung) + "/phasen", name="Vorwarnung", farbe="gelb").status_code == 200
    phase = objekte(umgebung, KatplanPhase)[0]
    assert speichern(client, umgebung, punkt, PUNKT, phasen=[phase.id], geo_geprueft="1").status_code == 200
    # Altdaten ohne geometry_json verwenden weiterhin lat/lng.
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.get(KatplanElement, punkt.id).geometry_json = None
        db.commit()
    login(client, umgebung, "katplan_freigeber")
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    return umgebung, punkt, polygon, geheim, ohne, phase


def features(client, info, ansicht=""):
    r = client.get(basis(info) + "/karte.geojson", params={"ansicht": ansicht})
    assert r.status_code == 200
    assert r.json()["type"] == "FeatureCollection"
    return {f["properties"]["uuid"]: f for f in r.json()["features"]}


def test_freigabe_vertraulichkeit_geometrien_und_felder(client, kartenplan):
    info, punkt, polygon, geheim, _, phase = kartenplan
    login(client, info, "readonly")
    daten = features(client, info)
    assert set(daten) == {punkt.uuid, polygon.uuid}
    assert daten[punkt.uuid]["geometry"] == PUNKT
    assert daten[polygon.uuid]["geometry"] == POLYGON
    eigenschaften = daten[punkt.uuid]["properties"]
    assert eigenschaften["phasen"] == [phase.id]
    assert eigenschaften["geo_status"] == "geprueft"
    assert eigenschaften["typ_icon"] == "📍"
    assert eigenschaften["typ_icon_name"] == "map-pin"
    assert eigenschaften["url"] == basis(info) + f"#element-{punkt.uuid}"
    assert set(eigenschaften) == {"uuid", "kennung", "titel", "typ", "typ_label", "typ_icon", "typ_icon_name",
                                    "prioritaet", "geo_status", "phasen", "url"}
    with TestingSession() as db:
        set_tenant_context(db, None)
        name = "leitung-" + uuid4().hex
        _user(db, name, info["org_id"], "incident_leader")
        db.commit()
    info["users"]["incident_leader"] = name
    login(client, info, "incident_leader")
    assert set(features(client, info)) == {punkt.uuid, polygon.uuid, geheim.uuid}


def test_arbeitsstand_und_standardansicht(client, kartenplan):
    info, punkt, polygon, _, _, _ = kartenplan
    login(client, info)
    neu = anlegen(client, info)
    assert speichern(client, info, neu, PUNKT).status_code == 200
    # Nicht freigegebene Aenderungen sind fuer Bearbeiter standardmaessig sichtbar.
    assert neu.uuid in features(client, info)
    assert neu.uuid in features(client, info, "arbeitsstand")
    assert neu.uuid not in features(client, info, "freigabe")
    assert features(client, info, "arbeitsstand")[neu.uuid]["properties"]["url"] == (
        basis(info) + f"?ansicht=arbeitsstand#element-{neu.uuid}")
    login(client, info, "readonly")
    assert set(features(client, info, "arbeitsstand")) == {punkt.uuid, polygon.uuid}
    assert neu.uuid not in features(client, info)


@pytest.mark.parametrize("suffix", ["/karte", "/karte.geojson"])
def test_mandant_und_fehlende_freigabe(client, umgebung, suffix):
    login(client, umgebung, "readonly")
    assert client.get(basis(umgebung) + suffix).status_code == 404
    with TestingSession() as db:
        set_tenant_context(db, None)
        fremd_org = db.query(User).filter_by(username=umgebung["users"]["fremd"]).one().org_id
        fremd = KatplanPlan(org_id=fremd_org, titel="Fremd")
        db.add(fremd)
        db.commit()
        fremd_id = fremd.id
    login(client, umgebung)
    assert client.get(f"/katastrophenplaene/{fremd_id}" + suffix).status_code == 404


def test_html_filter_reiter_assets_und_unverortete(client, kartenplan):
    info, _, _, geheim, ohne, _ = kartenplan
    login(client, info, "readonly")
    r = client.get(basis(info) + "/karte")
    assert r.status_code == 200
    for text in (">Plan</a>", ">Karte</a>", ">Versionen</a>", 'data-kp-filter-typ',
                 'data-kp-filter-phase', 'data-kp-filter-prio', 'data-kp-filter-geprueft',
                 'katplan_karte.js', 'data-kp-plankarte', 'Nicht verortete Elemente: 1',
                 basis(info) + f"#element-{ohne.uuid}"):
        assert text in r.text
    assert geheim.uuid not in r.text
    assert 'data-mini="true"' in client.get(basis(info)).text
    assert 'Große Karte' in client.get(basis(info)).text
    assert '>Karte</a>' in client.get(basis(info) + '/versionen').text
    login(client, info)
    r = client.get(basis(info) + "/karte?ansicht=arbeitsstand")
    assert 'Freigegebene Fassung' in r.text and 'Arbeitsstand' in r.text and '>Protokoll</a>' in r.text
    assert '>Karte</a>' in client.get(basis(info) + '#kp-protokoll').text


def test_uebersicht_nur_mit_verortung_der_sichtbaren_fassung(client, umgebung):
    login(client, umgebung)
    e = anlegen(client, umgebung)
    assert 'data-kp-plankarte' not in client.get(basis(umgebung)).text
    assert speichern(client, umgebung, e, PUNKT).status_code == 200
    assert 'data-mini="true"' in client.get(basis(umgebung)).text
    login(client, umgebung, "katplan_freigeber")
    freigabe = post(client, basis(umgebung) + "/freigeben")
    assert freigabe.status_code == 200 and 'data-mini="true"' in freigabe.text
    e = objekte(umgebung, KatplanElement)[0]
    assert speichern(client, umgebung, e, None).status_code == 200
    assert 'data-kp-plankarte' not in client.get(basis(umgebung)).text
    login(client, umgebung, "readonly")
    assert 'data-mini="true"' in client.get(basis(umgebung)).text


def test_leere_karte_und_keine_db_mutation(client, umgebung):
    login(client, umgebung)
    assert features(client, umgebung) == {}
    r = client.get(basis(umgebung) + '/karte')
    assert r.status_code == 200 and 'Nicht verortete Elemente: 0' in r.text
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        assert plan.status == 'entwurf' and plan.aktuelle_version_id is None
