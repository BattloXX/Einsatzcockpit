"""Atomare Uebernahme, Quellenalterung und bestehende Kartenkonsumenten."""
import asyncio
import json
from unittest.mock import patch

import pytest
from PIL import Image
from sqlalchemy import event

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.tools.objekt_karte import objekt_karte_lesen, objekt_karte_vorschau
from app.models.objekt import Objekt, ObjektChange, ObjektDokument, ObjektKartenObjekt, ObjektKartenVorschlag
from app.models.user import User
from app.services import objekt_karte_freigabe_service as service
from app.services.objekt_pdf_service import render_objekt_map_png
from app.services.objekt_service import erstelle_arbeitskopie
from tests.test_mcp_objekt_karte import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import punkte
from tests.test_objekt_karte_vorschlaege import _neu


def _kontext(db, karte):
    set_tenant_context(db, karte["org_id"])
    return db.get(Objekt, karte["objekt_id"]), db.get(User, karte["users"]["u"])


def _freigeben(db, objekt, user, ids, warnungen=None):
    return service.freigeben(db, objekt, ids, user, warnungen or {}, "mcp")


def test_hinzufuegen_audit_pdf_lesen_und_legacy(karte):
    with SessionLocal() as db:
        objekt, user = _kontext(db, karte)
        legacy = [(m.id, m.position_status) for m in objekt.karten_objekte]
        v = _neu(db, karte, lat=47.101, lng=9.201)
        result = _freigeben(db, objekt, user, [v["id"]])
        mid = result["ergebnisse"][0]["marker_id"]
        db.commit()
        m = db.get(ObjektKartenObjekt, mid)
        assert (m.position_status, m.revision, m.geprueft_von) == ("bestaetigt", 1, user.id)
        assert m.geprueft_am.tzinfo is None
        assert all(db.get(ObjektKartenObjekt, i).position_status == status == "ungeprueft" for i, status in legacy)
        audit = db.query(ObjektChange).filter_by(objekt_id=objekt.id, feld="symbol_neu").one()
        assert audit.before_json is None and json.loads(audit.after_json)["id"] == mid
        assert (audit.bereich, audit.quelle, audit.user_id) == ("karte", "mcp", user.id)
        gelesen = asyncio.run(objekt_karte_lesen(MCPContext(db, user, objekt.org_id), objekt.id))
        assert any(m["id"] == mid and m["position_status"] == "bestaetigt" for m in gelesen["marker"])
        with patch("staticmap.StaticMap") as karte_mock:
            karte_mock.return_value.render.return_value = Image.new("RGB", (10, 10))
            assert render_objekt_map_png(objekt).startswith(b"\x89PNG")
            assert any(c.args[0].coord == (9.201, 47.101) for c in karte_mock.return_value.add_marker.call_args_list)
        with pytest.raises(service.FreigabeKonflikt):
            _freigeben(db, objekt, user, [v["id"]])


@pytest.mark.parametrize("operation", ["aendern", "entfernen"])
def test_aendern_entfernen_revision_konkurrenz(karte, operation):
    with SessionLocal() as db:
        objekt, user = _kontext(db, karte)
        m = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt.id, typ="tor").one()
        mid = m.id
        daten = dict(operation=operation, basis_marker_id=mid, expected_revision=1, lat=47.102, lng=9.202)
        a = _neu(db, karte, **daten)
        b = _neu(db, karte, **(daten | {"lat": 47.103}))
        db.commit()
        _freigeben(db, objekt, user, [a["id"]])
        db.commit()
        assert db.get(ObjektKartenVorschlag, b["id"]).freigabe_status == "ersetzt"
        audit = db.query(ObjektChange).filter_by(objekt_id=objekt.id,
                    feld="symbol_geaendert" if operation == "aendern" else "symbol_geloescht").one()
        assert json.loads(audit.before_json)["revision"] == 1
        if operation == "aendern":
            assert db.get(ObjektKartenObjekt, mid).revision == 2
            assert json.loads(audit.after_json)["revision"] == 2
        else:
            assert db.get(ObjektKartenObjekt, mid) is None and audit.after_json is None


def test_konflikt_rollt_gesamte_auswahl_zurueck(karte):
    with SessionLocal() as db:
        objekt, user = _kontext(db, karte)
        m = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt.id, typ="tor").one()
        a = _neu(db, karte, lat=47.102, lng=9.202)
        b = _neu(db, karte, operation="aendern", basis_marker_id=m.id, expected_revision=1, lat=47.103, lng=9.203)
        m.revision = 2
        db.commit()
        with pytest.raises(service.FreigabeKonflikt) as exc:
            _freigeben(db, objekt, user, [a["id"], b["id"]])
        assert len(exc.value.ergebnisse) == 2 and all(e["ursache"] for e in exc.value.ergebnisse)
        assert db.query(ObjektKartenObjekt).count() == 2
        assert db.query(ObjektKartenVorschlag).filter_by(freigabe_status="offen").count() == 2
        assert db.query(ObjektChange).filter_by(feld="symbol_neu").count() == 0


@pytest.mark.parametrize("fall", [
    "ohne_position", "pruefung_erforderlich", "veraltet", "warnung", "kritisch", "arbeitskopie",
])
def test_freigabehindernisse(karte, fall):
    with SessionLocal() as db:
        objekt, user = _kontext(db, karte)
        daten = {} if fall == "ohne_position" else {"lat": 47.101, "lng": 9.201}
        if fall == "kritisch":
            daten["symbol_code"] = "bmz"
        if fall == "warnung":
            daten.update(lat=48, lng=10)
        v = _neu(db, karte, **daten)
        if fall in {"pruefung_erforderlich", "veraltet"}:
            db.get(ObjektKartenVorschlag, v["id"]).position_status = fall
        if fall == "kritisch":
            db.get(ObjektKartenVorschlag, v["id"]).warnungen_json = "[]"
        if fall == "arbeitskopie":
            erstelle_arbeitskopie(db, objekt, user.id)
        db.commit()
        with pytest.raises(service.FreigabeFehler) as exc:
            _freigeben(db, objekt, user, [v["id"]])
        assert exc.value.status_code == (409 if fall == "arbeitskopie" else 422)
        if fall in {"warnung", "kritisch"}:
            code = "weit_vom_objekt" if fall == "warnung" else "einsatzkritisch_freigabe_erforderlich"
            _freigeben(db, objekt, user, [v["id"]], {str(v["id"]): [code]})
            assert db.get(ObjektKartenVorschlag, v["id"]).freigabe_status == "angenommen"


def test_korrigieren_ablehnen_und_quellhash(karte, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    plan = tmp_path / "plan.pdf"
    plan.write_bytes(b"v1")
    with SessionLocal() as db:
        objekt, user = _kontext(db, karte)
        doc = ObjektDokument(org_id=objekt.org_id, objekt_id=objekt.id, pfad="plan.pdf", dateiname_original="plan.pdf")
        db.add(doc)
        db.flush()
        a = _neu(db, karte, quelle_dokument_id=doc.id, lat=47.101, lng=9.201)
        _freigeben(db, objekt, user, [a["id"]])
        b = _neu(db, karte, quelle_dokument_id=doc.id, lat=47.102, lng=9.202)
        db.commit()
        plan.write_bytes(b"v2")
        service.markiere_veraltete(db, objekt)
        db.commit()
        assert db.get(ObjektKartenVorschlag, b["id"]).position_status == "veraltet"
        assert db.query(ObjektKartenObjekt).filter_by(position_status="veraltet").count() == 1
        korr = service.vorschlag_korrigieren(db, objekt, b["id"], 47.103, 9.203, 450, "Korrigiert", user)
        assert (korr["position_methode"], korr["position_status"], korr["genauigkeit_m"], korr["rotation"]) == (
            "manuell_korrigiert", "vorgeschlagen", None, 90)
        assert {"code": "position_manuell"} in korr["warnungen"]
        service.ablehnen(db, objekt, [b["id"]], "Keine sichere Zuordnung", user)
        db.commit()
        assert db.get(ObjektKartenVorschlag, b["id"]).geprueft_von == user.id
        assert "Keine sichere" in db.query(ObjektChange).filter_by(feld="vorschlag_abgelehnt").one().after_json


def test_vorschau_nur_selects(karte):
    with SessionLocal() as db:
        objekt, user = _kontext(db, karte)
        _neu(db, karte, lat=47.101, lng=9.201)
        db.commit()
        statements = []
        conn = db.connection()
        def merken(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(conn, "before_cursor_execute", merken)
        try:
            antwort = asyncio.run(objekt_karte_vorschau(MCPContext(db, user, objekt.org_id), objekt.id, "offen"))
        finally:
            event.remove(conn, "before_cursor_execute", merken)
        assert len(antwort["marker"]) == 2 and len(antwort["vorschlaege"]) == 1
        assert antwort["ui_link"].endswith(f"/objekte/{objekt.id}/karte")
        assert all(s.lstrip().upper().startswith("SELECT") for s in statements)


def test_mehrere_ausgewaehlte_konkurrenten_sind_konflikt(karte):
    with SessionLocal() as db:
        objekt, user = _kontext(db, karte)
        m = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt.id, typ="tor").one()
        daten = dict(operation="aendern", basis_marker_id=m.id, expected_revision=1, lng=9.201)
        a = _neu(db, karte, lat=47.101, **daten)
        b = _neu(db, karte, lat=47.102, **daten)
        db.commit()
        with pytest.raises(service.FreigabeKonflikt) as exc:
            _freigeben(db, objekt, user, [a["id"], b["id"]])
        assert all(e["konflikt"] for e in exc.value.ergebnisse)
        assert m.revision == 1


def test_kritischer_basismarker_braucht_bestaetigung(karte):
    with SessionLocal() as db:
        objekt, user = _kontext(db, karte)
        m = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt.id, typ="tor").one()
        m.typ = "bmz"
        v = _neu(db, karte, operation="entfernen", basis_marker_id=m.id, expected_revision=1, lat=47.101, lng=9.201)
        db.commit()
        with pytest.raises(service.FreigabeFehler) as exc:
            _freigeben(db, objekt, user, [v["id"]])
        assert "einsatzkritisch_freigabe_erforderlich" in exc.value.ergebnisse[0]["ursache"]


def test_migration_quellhash_upgrade_downgrade(tmp_path):
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0333_objekt_karten_quellhash.py"
    spec = importlib.util.spec_from_file_location("migration_0333", pfad)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE objekt_karten_objekt (id INTEGER PRIMARY KEY, position_status VARCHAR(30))")
        conn.exec_driver_sql("INSERT INTO objekt_karten_objekt VALUES (1, 'ungeprueft')")
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            zeile = conn.exec_driver_sql(
                "SELECT position_status, quelle_dokument_hash FROM objekt_karten_objekt").one()
            assert zeile == ("ungeprueft", None)
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert conn.exec_driver_sql("SELECT * FROM objekt_karten_objekt").one() == (1, "ungeprueft")
    engine.dispose()


def test_korrektur_neue_dokumentversion_freigebbar(karte, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    (tmp_path / "v1.pdf").write_bytes(b"v1")
    (tmp_path / "v2.pdf").write_bytes(b"v2")
    with SessionLocal() as db:
        objekt, user = _kontext(db, karte)
        alt = ObjektDokument(org_id=objekt.org_id, objekt_id=objekt.id, pfad="v1.pdf", dateiname_original="Plan.pdf")
        db.add(alt)
        db.flush()
        v = _neu(db, karte, lat=47.101, lng=9.201, quelle_dokument_id=alt.id)
        alt.ist_aktuelle_version = False
        neu = ObjektDokument(org_id=objekt.org_id, objekt_id=objekt.id, pfad="v2.pdf", dateiname_original="Plan.pdf",
                             dokument_gruppe_id=alt.id, versionsnummer=2)
        db.add(neu)
        db.flush()
        service.markiere_veraltete(db, objekt)
        korr = service.vorschlag_korrigieren(db, objekt, v["id"], 47.102, 9.202, 0, "Manuell", user)
        assert korr["quelle_dokument_id"] == neu.id and korr["quelle_dokument_hash"] != v["quelle_dokument_hash"]
        _freigeben(db, objekt, user, [v["id"]], {v["id"]: ["position_manuell"]})
        db.commit()
        service.markiere_veraltete(db, objekt)
        assert db.query(ObjektKartenObjekt).filter_by(position_status="bestaetigt").count() == 1


@pytest.mark.parametrize("fall", ["abgelehnt", "rotation", "hash"])
def test_georef_vor_freigabe_erneut_pruefen(plan, fall):
    from app.models.objekt import ObjektDokumentGeoref, ObjektDokumentSeite
    from app.services.objekt_georef_service import referenzpunkte

    with SessionLocal() as db:
        objekt, user = _kontext(db, plan)
        g = referenzpunkte(db, objekt, plan["dokument_id"], 1, "setzen", punkte(), None, user.id)["georeferenz"]
        v = _neu(db, plan, georef_id=g["id"], bild_x=0.5, bild_y=0.5)
        assert v["position_status"] == "vorgeschlagen"
        if fall == "abgelehnt":
            db.get(ObjektDokumentGeoref, g["id"]).status = "abgelehnt"
        elif fall == "rotation":
            db.query(ObjektDokumentSeite).filter_by(dokument_id=plan["dokument_id"]).one().rotation = 90
        else:
            (plan["pfad"] / "plan.pdf").write_bytes(b"geaendert")
        db.commit()
        with pytest.raises(service.FreigabeFehler):
            _freigeben(db, objekt, user, [v["id"]], {
                v["id"]: ["weit_vom_objekt", "georef_veraltet", "georef_abgelehnt"],
            })
        assert db.query(ObjektKartenObjekt).count() == 2
