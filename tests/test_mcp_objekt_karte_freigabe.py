"""MCP-Freigabe mit ausdruecklicher Bestaetigung und echtem Rollback."""
import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import ObjektChange, ObjektKartenObjekt, ObjektKartenVorschlag
from tests.test_mcp_objekt_karte import karte as karte
from tests.test_mcp_objekte import _rufe, _seed, _token
from tests.test_objekt_karte_vorschlaege import _neu


def test_bestaetigung_dry_run_und_uebernahme(client, karte):
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        v = _neu(db, karte, lat=47.101, lng=9.201)
        db.commit()
    token = _token(client, karte, "u")
    args = dict(objekt_id=karte["objekt_id"], vorschlag_ids=[v["id"]], bestaetigte_warnungen={})
    assert "__fehler__" in _rufe(client, token, "objekt_karte_freigeben", **args, bestaetigung="ja")
    args["bestaetigung"] = "ICH_BESTAETIGE_FREIGABE"
    result = _rufe(client, token, "objekt_karte_freigeben", **args, pruefen=True)
    assert not result["gespeichert"] and result["ergebnisse"][0]["status"] == "angenommen"
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        assert db.query(ObjektKartenObjekt).count() == 2
        assert db.get(ObjektKartenVorschlag, v["id"]).freigabe_status == "offen"
        assert db.query(ObjektChange).filter_by(feld="symbol_neu").count() == 0
    assert _rufe(client, token, "objekt_karte_freigeben", **args)["gespeichert"]
    assert _rufe(client, token, "objekt_karte_freigeben", **args)["status_code"] == 409


@pytest.mark.parametrize("rolle", ["readonly", "recorder", "objekt_verwalter"])
def test_rechte_und_fremder_tenant(client, karte, rolle):
    seed = _seed("mcp-freigabe-" + rolle, {"u": rolle})
    token = _token(client, seed, "u")
    args = dict(vorschlag_ids=[1], bestaetigte_warnungen={}, bestaetigung="ICH_BESTAETIGE_FREIGABE")
    assert "__fehler__" in _rufe(client, token, "objekt_karte_freigeben", objekt_id=karte["objekt_id"], **args)
    assert "__fehler__" in _rufe(client, token, "objekt_karte_vorschau", objekt_id=karte["objekt_id"])
    assert "__fehler__" not in _rufe(client, token, "objekt_karte_vorschau", objekt_id=seed["objekt_id"])
    if rolle != "objekt_verwalter":
        assert "__fehler__" in _rufe(client, token, "objekt_karte_freigeben", objekt_id=seed["objekt_id"], **args)


def test_quellenalterung_nur_explizit_schreibend(client, karte, tmp_path, monkeypatch):
    from app.config import settings
    from app.models.objekt import ObjektDokument

    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    plan = tmp_path / "plan.pdf"
    plan.write_bytes(b"v1")
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        doc = ObjektDokument(org_id=karte["org_id"], objekt_id=karte["objekt_id"],
                             pfad="plan.pdf", dateiname_original="plan.pdf")
        db.add(doc)
        db.flush()
        v = _neu(db, karte, quelle_dokument_id=doc.id, lat=47.101, lng=9.201)
        db.commit()
    plan.write_bytes(b"v2")
    token = _token(client, karte, "u")
    _rufe(client, token, "objekt_karte_pruefen", objekt_id=karte["objekt_id"])
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        assert db.get(ObjektKartenVorschlag, v["id"]).position_status == "vorgeschlagen"
    for pruefen in (True, False):
        antwort = _rufe(client, token, "objekt_karte_vorschlaege", objekt_id=karte["objekt_id"],
                         aktion="auflisten", veraltete_markieren=True, pruefen=pruefen)
        assert antwort["vorschlaege"][0]["position_status"] == "veraltet"
        with SessionLocal() as db:
            set_tenant_context(db, karte["org_id"])
            assert db.get(ObjektKartenVorschlag, v["id"]).position_status == (
                "vorgeschlagen" if pruefen else "veraltet")
