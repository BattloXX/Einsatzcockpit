"""Tablet-API fuer an der Einsatzstelle belassenes Material."""

from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.verleih import VerleihAusleihe, VerleihFoto
from tests.test_einheit_api import _daten, _device, _h, _png
from tests.test_einheit_zugang import _zugang_mit_auftrag


def _verleih_an(x, aktiv=True):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        # _daten deliberately keeps the org private; derive it from the assigned site.
        from app.models.major_incident import IncidentSite

        org_id = db.get(IncidentSite, x["a"]).org_id
        settings = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if settings is None:
            settings = OrgSettings(org_id=org_id)
            db.add(settings)
        settings.mi_feature_geraeteverleih = aktiv
        db.commit()
    finally:
        db.close()


def _material(client, x, **extra):
    payload = {
        "client_uuid": str(uuid4()),
        "positionen": [{"bezeichnung": "Tauchpumpe", "menge": 2}],
        "name": "Erika Muster",
    }
    payload.update(extra)
    return client.post(f"/einheit/api/auftrag/{x['d1']}/material", json=payload, headers=_h(client))


def test_material_anlage_replay_sms_detail_und_foto(client, setup_db, monkeypatch):
    x = _daten()
    _verleih_an(x)
    _device(client, x)
    gesendet = []

    async def send_sms(*args):
        gesendet.append(args)
        return True

    monkeypatch.setattr("app.services.sms_service.send_sms", send_sms)
    client_uuid = str(uuid4())
    response = _material(client, x, client_uuid=client_uuid, telefon="+436641234", sms_senden=True)
    replay = _material(client, x, client_uuid=client_uuid, telefon="+436641234", sms_senden=True)
    assert response.status_code == replay.status_code == 200
    assert response.json() == replay.json()
    ausleihe_id = response.json()["entity_id"]
    assert len(gesendet) == 1
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(VerleihAusleihe).filter_by(site_id=x["a"]).count() == 1
    finally:
        db.close()
    detail = client.get(f"/einheit/api/auftrag/{x['d1']}").json()
    assert detail["material"][0]["id"] == ausleihe_id
    assert "telefon" not in detail["material"][0] and "pin" not in detail["material"][0]
    foto = client.post(
        f"/einheit/api/auftrag/{x['d1']}/material_foto",
        data={"client_uuid": str(uuid4()), "material_client_uuid": client_uuid},
        files={"file": ("beweis.png", _png(), "image/png")}, headers=_h(client),
    )
    assert foto.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(VerleihFoto, foto.json()["entity_id"]).author_name == "U0"
    finally:
        db.close()
    unbekannt = client.post(
        f"/einheit/api/auftrag/{x['d1']}/material_foto",
        data={"client_uuid": str(uuid4()), "material_client_uuid": str(uuid4())},
        files={"file": ("beweis.png", _png(), "image/png")}, headers=_h(client),
    )
    assert unbekannt.status_code == 409 and unbekannt.json() == {"code": "material_unbekannt"}


def test_material_feature_aus_und_fremde_einheit(client, setup_db):
    x = _daten()
    _verleih_an(x, aktiv=False)
    _device(client, x)
    assert _material(client, x).status_code == 403
    assert client.get("/einheit/api/zustand").json()["verleih_aktiv"] is False
    _verleih_an(x)
    own = _material(client, x)
    assert own.status_code == 200
    # Die zweite Einheit hat an ihrer anderen Stelle keinen Zugriff auf unsere Ausleihe.
    _device(client, x, second=True)
    response = client.post(
        f"/einheit/api/auftrag/{x['d2']}/material_zurueck",
        json={"client_uuid": str(uuid4()), "ausleihe_id": own.json()["entity_id"]}, headers=_h(client),
    )
    assert response.status_code == 403 and response.json() == {"code": "material_fremd"}


def test_gk_darf_material_anlegen_und_sieht_nur_eigenes(client, setup_db):
    cookie, dispatch_id, einheit_id, _ = _zugang_mit_auftrag()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        from app.models.major_incident import EinheitSiteDispatch

        dispatch = db.get(EinheitSiteDispatch, dispatch_id)
        assert dispatch is not None
        settings = db.query(OrgSettings).filter_by(org_id=dispatch.site.org_id).first()
        if settings is None:
            settings = OrgSettings(org_id=dispatch.site.org_id)
            db.add(settings)
        settings.mi_feature_geraeteverleih = True
        db.add(VerleihAusleihe(
            org_id=dispatch.site.org_id, lage_id=dispatch.site.major_incident_id, site_id=dispatch.site_id,
            einheit_id=None, quelle="einheit", name="Fremde Ausleihe",
        ))
        db.commit()
    finally:
        db.close()
    client.cookies.set("ec_gk", cookie)
    client.get("/einheit/api/zustand")
    response = client.post(
        f"/einheit/api/auftrag/{dispatch_id}/material",
        json={"client_uuid": str(uuid4()), "positionen": [{"bezeichnung": "Schlauch", "menge": 1}]},
        headers=_h(client),
    )
    assert response.status_code == 200
    detail = client.get(f"/einheit/api/auftrag/{dispatch_id}").json()
    assert len(detail["material"]) == 1 and detail["material"][0]["eigene"] is True
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(VerleihAusleihe, response.json()["entity_id"]).einheit_id == einheit_id
    finally:
        db.close()


def test_material_simulation_in_echtlage_ist_lesend(client, setup_db):
    from app.core.security import sign_session

    x = _daten()
    _verleih_an(x)
    client.cookies.set("session", sign_session(x["admin"]))
    headers = {"X-EC-Einheit-Sim": str(x["e1"])}
    client.get("/einheit/api/zustand", headers=headers)
    headers.update(_h(client))
    response = client.post(
        f"/einheit/api/auftrag/{x['d1']}/material",
        json={"client_uuid": str(uuid4()), "positionen": [{"bezeichnung": "Pumpe", "menge": 1}]}, headers=headers,
    )
    assert response.status_code == 403 and response.json() == {"code": "simulation_nur_lesend"}


def test_material_medienroute_eigene_lage_und_fremde_org(client, setup_db):
    x = _daten()
    _verleih_an(x)
    _device(client, x)
    client_uuid = str(uuid4())
    assert _material(client, x, client_uuid=client_uuid).status_code == 200
    foto = client.post(
        f"/einheit/api/auftrag/{x['d1']}/material_foto",
        data={"client_uuid": str(uuid4()), "material_client_uuid": client_uuid},
        files={"file": ("beweis.png", _png(), "image/png")}, headers=_h(client),
    )
    foto_id = foto.json()["entity_id"]
    detail = client.get(f"/einheit/api/auftrag/{x['d1']}").json()
    assert detail["material"][0]["fotos"][0]["thumb_url"] == f"/einheit/medien/verleih/thumb/{foto_id}"
    assert client.get(f"/einheit/medien/verleih/{foto_id}").status_code == 200
    assert client.get(f"/einheit/medien/verleih/thumb/{foto_id}").status_code == 200

    # Ein Tablet einer anderen Org/Lage sieht das Foto nicht.
    y = _daten()
    _device(client, y)
    assert client.get(f"/einheit/medien/verleih/{foto_id}").status_code == 404
    assert client.get(f"/einheit/medien/verleih/thumb/{foto_id}").status_code == 404
