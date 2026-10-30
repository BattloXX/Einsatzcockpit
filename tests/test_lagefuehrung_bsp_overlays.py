"""P3: bestaetigte BSP-Einsatzebene, Rechte und gemeinsame Einstellungen."""
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.permissions import EINSATZ_LESE_ROLLEN
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.lagefuehrung import LagefuehrungBerechtigung, LagefuehrungEvent
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import (
    BspOverlayEinsatz,
    Objekt,
    ObjektBspOverlay,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektEinsatz,
)
from app.models.user import Role, User
from app.services import objekt_bsp_overlay_service as bsp
from app.services import objekt_georef_service as geo
from tests.test_bsp_overlay_routes import _rolle
from tests.test_bsp_overlay_service import umgebung as umgebung
from tests.test_mcp_objekte import _seed
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import punkte
from tests.test_objekt_karte_freigabe_routes import _login
from tests.test_objekt_karte_rotation import _post


@pytest.fixture
def einsatz_plan(umgebung):
    db, overlay, user, plan = umgebung
    bsp.freigeben(db, overlay, user)
    org = db.query(OrgSettings).filter_by(org_id=plan["org_id"]).one()
    org.lagefuehrung_modul_aktiv = True
    system = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
    if system:
        system.value = "true"
    else:
        db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
    incident = Incident(primary_org_id=plan["org_id"], alarm_type_code="T1", status="active")
    anderer = Incident(primary_org_id=plan["org_id"], alarm_type_code="T1", status="active")
    db.add_all([incident, anderer])
    db.flush()
    db.add(ObjektEinsatz(org_id=plan["org_id"], incident_id=incident.id,
                        objekt_id=overlay.objekt_id, quelle="manuell", status="bestaetigt"))
    db.commit()
    daten = plan | {"incident_id": incident.id, "anderer_id": anderer.id, "overlay_id": overlay.id,
                    "sha": overlay.raster_sha256, "bounds": json.loads(overlay.raster_bounds_json)}
    _rolle(daten, "incident_leader")
    return daten


def _url(p, incident_id=None):
    return f'/einsatz/{incident_id or p["incident_id"]}/lagefuehrung/bsp-overlays'


def _einstellung(client, p, daten=None, incident_id=None):
    return _post(client, f'{_url(p, incident_id)}/{p["overlay_id"]}/einstellung',
                 daten or {"sichtbarkeit": "ON", "deckkraft": 0.7})


def _lesen(client, p):
    r = client.get(_url(p) + ".json")
    assert r.status_code == 200, r.text
    return r.json()


def test_json_nur_bestaetigt_freigegeben_auslieferbar(client, einsatz_plan):
    p = einsatz_plan
    _login(client, p)
    daten = _lesen(client, p)
    assert len(daten) == 1
    o = daten[0]
    assert o["id"] == p["overlay_id"] and o["objekt_id"] == p["objekt_id"]
    assert o["objekt_name"] == "Musterbetrieb" and o["name"] == "Lageplan"
    assert o["raster_url"] == f'/objekt-medien/bsp-overlay/{o["id"]}/raster?v=1-{p["sha"][:8]}'
    assert o["freigegeben_am"].endswith("Z") and o["bounds"] == p["bounds"]
    assert o["kann_einstellen"] is True
    assert o["sichtbarkeit"] == "INHERIT" and o["deckkraft"] is None
    assert o["effektiv_sichtbar"] is False and o["effektiv_deckkraft"] == 0.6
    assert {"plan_art", "geschoss", "bereich", "freigabe_nr", "standard_sichtbar", "standard_deckkraft"} <= o.keys()
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        overlay = db.get(ObjektBspOverlay, p["overlay_id"])
        overlay.status = "entwurf"
        db.commit()
    assert _lesen(client, p) == []
    assert _einstellung(client, p).status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        overlay = db.get(ObjektBspOverlay, p["overlay_id"])
        overlay.status = "freigegeben"
        raster_pfad = overlay.raster_pfad
        db.commit()
    (p["pfad"] / raster_pfad).write_bytes(b"beschaedigt")
    assert _lesen(client, p) == []
    assert _einstellung(client, p).status_code == 404


def test_vorschlag_und_mehrobjekt_nur_bestaetigte(client, einsatz_plan):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        zuordnung = db.query(ObjektEinsatz).filter_by(incident_id=p["incident_id"]).one()
        zuordnung.status = "vorschlag"
        db.commit()
    _login(client, p)
    assert _lesen(client, p) == []
    assert _einstellung(client, p).status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        # Zweites Objekt hat eine echte eigene Dokument-/Georef-Kette.
        objekt = Objekt(org_id=p["org_id"], nummer=9, name="Zweite Halle", status="freigegeben")
        db.add(objekt)
        db.flush()
        dokument = ObjektDokument(org_id=p["org_id"], objekt_id=objekt.id, pfad="plan.pdf",
                                   dateiname_original="plan.pdf", freigabe_status="freigegeben")
        db.add(dokument)
        db.flush()
        db.add(ObjektDokumentSeite(org_id=p["org_id"], objekt_id=objekt.id,
                                 dokument_id=dokument.id, seiten_nr=1, bild_pfad="plan.png"))
        db.flush()
        geo.referenzpunkte(db, objekt, dokument.id, 1, "setzen", punkte(), "aehnlichkeit", p["users"]["u"])
        overlay = bsp.entwurf_anlegen(db, objekt.id, dokument.id, 1, {"name": "Halle"}, p["users"]["u"])
        user = db.get(User, p["users"]["u"])
        # Freigabe durch Objektverwalter, danach wieder Lagefuehrungsrolle.
        user.user_roles[0].role = db.query(Role).filter_by(code="objekt_verwalter").one()
        db.flush()
        bsp.freigeben(db, overlay, user)
        db.add(ObjektEinsatz(org_id=p["org_id"], incident_id=p["incident_id"], objekt_id=objekt.id,
                            quelle="manuell", status="bestaetigt"))
        db.commit()
        overlay_id = overlay.id
    assert [o["id"] for o in _lesen(client, p)] == [overlay_id]
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        zuordnung = db.query(ObjektEinsatz).filter_by(
            incident_id=p["incident_id"], objekt_id=p["objekt_id"],
        ).one()
        zuordnung.status = "bestaetigt"
        db.commit()
    assert {o["id"] for o in _lesen(client, p)} == {p["overlay_id"], overlay_id}


@pytest.mark.parametrize("art", ["georef", "dokument"])
def test_veraltetes_overlay_fehlt(client, einsatz_plan, art):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        if art == "georef":
            geo.referenzpunkte(db, db.get(Objekt, p["objekt_id"]), p["dokument_id"], 1,
                              "setzen", punkte(), "aehnlichkeit", p["users"]["u"])
        else:
            db.get(ObjektDokument, p["dokument_id"]).ist_aktuelle_version = False
        db.commit()
    _login(client, p)
    assert _lesen(client, p) == []
    assert _einstellung(client, p).status_code == 404


@pytest.mark.parametrize("sichtbarkeit,standard,deckkraft,erwartet,opacity", [
    ("INHERIT", False, None, False, 0.6), ("INHERIT", True, None, True, 0.6),
    ("ON", False, 0.1, True, 0.1), ("OFF", True, 1.0, False, 1.0),
])
def test_aufloesung_sichtbarkeit_und_deckkraft(
    client, einsatz_plan, sichtbarkeit, standard, deckkraft, erwartet, opacity,
):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        db.get(ObjektBspOverlay, p["overlay_id"]).standard_sichtbar = standard
        db.commit()
    _login(client, p)
    assert _einstellung(client, p, {"sichtbarkeit": sichtbarkeit, "deckkraft": deckkraft}).status_code == 200
    o = _lesen(client, p)[0]
    assert o["sichtbarkeit"] == sichtbarkeit and o["deckkraft"] == deckkraft
    assert o["effektiv_sichtbar"] is erwartet and o["effektiv_deckkraft"] == opacity


@pytest.mark.parametrize("rolle", EINSATZ_LESE_ROLLEN)
def test_leser_raster_erlaubt_post_nur_bearbeiter(client, einsatz_plan, rolle):
    p = einsatz_plan
    _rolle(p, rolle)
    _login(client, p)
    darf = rolle in {"admin", "org_admin", "incident_leader", "recorder"}
    assert _lesen(client, p)[0]["kann_einstellen"] is darf
    assert client.get(f'/objekt-medien/bsp-overlay/{p["overlay_id"]}/raster').status_code == 200
    assert _einstellung(client, p).status_code == (200 if darf else 403)


def test_explizites_editorrecht_und_abgeschlossener_einsatz(client, einsatz_plan):
    p = einsatz_plan
    _rolle(p, "readonly")
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        db.add(LagefuehrungBerechtigung(org_id=p["org_id"], incident_id=p["incident_id"],
                                       user_id=p["users"]["u"], granted_by_user_id=p["users"]["u"]))
        db.commit()
    _login(client, p)
    assert _lesen(client, p)[0]["kann_einstellen"] is True
    assert _einstellung(client, p).status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        db.get(Incident, p["incident_id"]).status = "closed"
        db.commit()
    assert _lesen(client, p)[0]["kann_einstellen"] is False
    assert _einstellung(client, p).status_code == 409


def test_fremder_einsatz_und_nicht_zugeordnet_404(client, einsatz_plan):
    p = einsatz_plan
    _login(client, p)
    assert client.get(_url(p, p["anderer_id"]) + ".json").json() == []
    assert _einstellung(client, p, incident_id=p["anderer_id"]).status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        db.delete(db.query(ObjektEinsatz).filter_by(incident_id=p["incident_id"]).one())
        db.commit()
    assert _einstellung(client, p).status_code == 404


def test_fremde_org_404(client, einsatz_plan):
    p = einsatz_plan
    fremd = _seed("lft-bsp-fremd-" + uuid4().hex[:8], {"u": "incident_leader"})
    with SessionLocal() as db:
        set_tenant_context(db, fremd["org_id"])
        db.query(OrgSettings).filter_by(org_id=fremd["org_id"]).one().lagefuehrung_modul_aktiv = True
        inc = Incident(primary_org_id=fremd["org_id"], alarm_type_code="T1", status="active")
        db.add(inc)
        db.commit()
        fremd_id = inc.id
    _login(client, fremd)
    assert client.get(_url(p) + ".json").status_code == 404
    assert _einstellung(client, p).status_code == 404
    assert _einstellung(client, p, incident_id=fremd_id).status_code == 404


def test_event_broadcast_und_idempotentes_upsert(client, einsatz_plan, monkeypatch):
    p = einsatz_plan
    broadcast = AsyncMock()
    monkeypatch.setattr("app.routers.ui_lagefuehrung.manager.broadcast", broadcast)
    _login(client, p)
    for _ in range(2):
        assert _einstellung(client, p).status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        einstellung = db.query(BspOverlayEinsatz).filter_by(incident_id=p["incident_id"]).one()
        assert einstellung.overlay_id == p["overlay_id"] and einstellung.sichtbarkeit == "ON"
        assert einstellung.deckkraft == 0.7 and einstellung.geaendert_von == p["users"]["u"]
        events = db.query(LagefuehrungEvent).filter_by(incident_id=p["incident_id"], event_typ="bsp.einstellung").all()
        assert len(events) == 2 and all(e.ref_id == p["overlay_id"] for e in events)
        assert json.loads(events[0].payload) == {"sichtbarkeit": "ON", "deckkraft": 0.7}
    assert broadcast.await_count == 2
    broadcast.assert_awaited_with(p["incident_id"], {"type": "lagefuehrung.bsp.changed", "overlay_id": p["overlay_id"]})


@pytest.mark.parametrize("daten", [{"sichtbarkeit": "falsch"}, {"sichtbarkeit": "ON", "deckkraft": 0},
                                   {"sichtbarkeit": "ON", "deckkraft": 1.1},
                                   {"sichtbarkeit": "ON", "deckkraft": True}])
def test_post_validierung(client, einsatz_plan, daten):
    _login(client, einsatz_plan)
    assert _einstellung(client, einsatz_plan, daten).status_code == 422


def test_csrf_und_modul_guards(client, einsatz_plan):
    p = einsatz_plan
    _login(client, p)
    assert client.post(f'{_url(p)}/{p["overlay_id"]}/einstellung', json={"sichtbarkeit": "ON"}).status_code == 403
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        db.query(OrgSettings).filter_by(org_id=p["org_id"]).one().objekt_module_enabled = False
        db.commit()
    assert _lesen(client, p) == []
    assert _einstellung(client, p).status_code == 404
    assert 'id="lft-layer-bsp"' not in client.get(f'/einsatz/{p["incident_id"]}/lagefuehrung').text
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        db.query(OrgSettings).filter_by(org_id=p["org_id"]).one().lagefuehrung_modul_aktiv = False
        db.commit()
    assert client.get(_url(p) + ".json").status_code == 404
    assert _einstellung(client, p).status_code == 404
