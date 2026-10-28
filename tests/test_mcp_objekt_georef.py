"""MCP-Georeferenzierung: Rechte, Tenant-Grenzen und echte Rollbacks."""

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import ObjektDokumentGeoref
from tests.test_mcp_objekte import _rufe, _seed, _token
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import punkte


def test_dry_run_lesen_verwerfen_und_fremd(client, plan):
    token = _token(client, plan, "u")
    args = dict(objekt_id=plan["objekt_id"], dokument_id=plan["dokument_id"], seite=1)
    trocken = _rufe(
        client, token, "objekt_karte_referenzpunkte", **args, aktion="setzen", punkte=punkte(), pruefen=True
    )
    assert not trocken["gespeichert"] and trocken["georeferenz"]["status"] == "gueltig"
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        assert db.query(ObjektDokumentGeoref).count() == 0
    echt = _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="setzen", punkte=punkte())
    assert echt["gespeichert"]
    assert (
        _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="lesen")["georeferenz"]["status"]
        == "gueltig"
    )
    _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="verwerfen", pruefen=True)
    assert (
        _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="lesen")["georeferenz"]["status"]
        == "gueltig"
    )
    fremd = _seed("georef-fremd", {"u": "objekt_verwalter"})
    ft = _token(client, fremd, "u")
    for tool, argumente in (
        ("objekt_karte_referenzpunkte", args | {"aktion": "lesen"}),
        ("objekt_karte_gebaeude_umrisse", {"objekt_id": plan["objekt_id"]}),
        (
            "objekt_karte_vorschlaege",
            {
                "objekt_id": fremd["objekt_id"],
                "aktion": "anlegen",
                "vorschlaege": [
                    {"symbol_code": "geometrie", "georef_id": echt["georeferenz"]["id"], "bild_x": 0.5, "bild_y": 0.5}
                ],
            },
        ),
    ):
        assert "__fehler__" in _rufe(client, ft, tool, **argumente)
    verworfen = _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="verwerfen")
    assert verworfen["georeferenz"]["status"] == "abgelehnt"


@pytest.mark.parametrize("rolle", ["readonly", "recorder", "incident_leader", "objekt_verwalter", "org_admin"])
def test_rechte(client, plan, rolle):
    seed = _seed("georef-recht-" + rolle, {"u": rolle})
    token = _token(client, seed, "u")
    # Leserecht am eigenen Objekt, aber nicht existierende Seite darf keine Daten liefern.
    assert "__fehler__" in _rufe(
        client,
        token,
        "objekt_karte_referenzpunkte",
        objekt_id=seed["objekt_id"],
        dokument_id=plan["dokument_id"],
        seite=1,
        aktion="lesen",
    )
    from app.models.user import Role, User, UserRole

    with SessionLocal() as db:
        set_tenant_context(db, None)
        u = db.get(User, plan["users"]["u"])
        for ur in list(u.user_roles):
            db.delete(ur)
        db.flush()
        role = db.query(Role).filter_by(code=rolle).one()
        db.add(UserRole(user_id=u.id, role_id=role.id))
        db.commit()
    own = _token(client, plan, "u")
    args = dict(objekt_id=plan["objekt_id"], dokument_id=plan["dokument_id"], seite=1)
    assert "__fehler__" not in _rufe(client, own, "objekt_karte_referenzpunkte", **args, aktion="lesen")
    antwort = _rufe(client, own, "objekt_karte_referenzpunkte", **args, aktion="setzen", punkte=punkte())
    assert ("__fehler__" not in antwort) == (rolle in {"objekt_verwalter", "org_admin"})


def test_umrisse_readonly_org_schalter_und_fehler(client, plan, monkeypatch):
    from unittest.mock import AsyncMock

    from app.models.master import OrgSettings
    from app.services import objekt_gebaeude_service
    from app.services.objekt_karte_service import ObjektKartenFehler

    mock = AsyncMock(return_value={"gebaeude": [{"osm_id": 123, "ecken": []}]})
    monkeypatch.setattr(objekt_gebaeude_service, "gebaeude_umrisse", mock)
    token = _token(client, plan, "u")
    args = dict(objekt_id=plan["objekt_id"])
    antwort = _rufe(client, token, "objekt_karte_gebaeude_umrisse", **args)
    assert antwort["gebaeude"][0]["osm_id"] == 123
    mock.side_effect = ObjektKartenFehler("Overpass-Netzfehler")
    assert "Overpass-Netzfehler" in _rufe(client, token, "objekt_karte_gebaeude_umrisse", **args)["__fehler__"]
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        db.query(OrgSettings).filter_by(org_id=plan["org_id"]).one().hydrant_layer_enabled = False
        db.commit()
    assert "deaktiviert" in _rufe(client, token, "objekt_karte_gebaeude_umrisse", **args)["__fehler__"]
    assert mock.await_count == 2
