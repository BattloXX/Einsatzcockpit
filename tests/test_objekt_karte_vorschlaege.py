"""Vorschlaege bleiben von produktiven Markern getrennt."""
import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektChange, ObjektDokument, ObjektKartenObjekt, ObjektKartenVorschlag
from app.services import objekt_karte_vorschlag_service as service
from app.services.objekt_karte_service import ObjektKartenFehler
from app.services.objekt_service import erstelle_arbeitskopie, uebernimm_arbeitskopie
from tests.test_mcp_objekt_karte import karte as karte
from tests.test_mcp_objekte import _seed


def _neu(db, karte, **daten):
    objekt = service.produktives_objekt(db, karte["org_id"], karte["objekt_id"])
    return service.vorschlag_speichern(db, objekt, {"symbol_code": "tor"} | daten, user_id=karte["users"]["u"])


def test_keine_marker_aenderung_fehlende_position_idempotenz_audit(karte):
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        vorher = [(m.id, m.revision, m.lat, m.lng) for m in db.query(ObjektKartenObjekt).all()]
        v = _neu(db, karte, erkennungs_konfidenz=0.98, genauigkeit_m=40)
        assert v["lat"] is None and v["lng"] is None
        assert v["position_status"] == "pruefung_erforderlich"
        assert v["erkennungs_konfidenz"] == 0.98 and v["genauigkeit_m"] == 40
        assert _neu(db, karte)["id"] == v["id"]
        assert db.query(ObjektKartenVorschlag).count() == 1
        assert vorher == [(m.id, m.revision, m.lat, m.lng) for m in db.query(ObjektKartenObjekt).all()]
        audit = db.query(ObjektChange).filter_by(feld="vorschlag_neu").one()
        assert audit.bereich == "karte" and audit.quelle == "mcp"


def test_warnungen_dubletten_kritisch_und_distanz(karte):
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        v = _neu(db, karte, lat=47.11, lng=9.22)
        assert any(w.get("marker_id") for w in v["warnungen"] if w["code"] == "moegliche_dublette")
        v2 = _neu(db, karte, lat=47.110001, lng=9.22, label="zweiter")
        assert {"code": "moegliche_dublette", "vorschlag_id": v["id"]} in v2["warnungen"]
        v3 = _neu(db, karte, symbol_code="bmz", lat=48, lng=10)
        assert {w["code"] for w in v3["warnungen"]} >= {"weit_vom_objekt", "einsatzkritisch_freigabe_erforderlich"}
        v4 = _neu(db, karte, label="Zufahrt")
        assert any(w.get("marker_id") for w in v4["warnungen"])


@pytest.mark.parametrize("daten", [
    {"operation": "aendern"}, {"operation": "entfernen"}, {"lat": 91, "lng": 9},
    {"lat": 47}, {"lat": float("nan"), "lng": 9}, {"lat": 47, "lng": 181},
    {"erkennungs_konfidenz": 1.1}, {"genauigkeit_m": -1}, {"rotation": 1.2},
    {"symbol_code": "unbekannt"}, {"position_status": "bestaetigt"}, {"freigabe_status": "angenommen"},
    {"quelle_ausschnitt": [0, 0, 2, 2]}, {"geometry": {"type": "Point", "coordinates": [181, 47]}},
])
def test_validierung(karte, daten):
    with SessionLocal() as db, pytest.raises(ObjektKartenFehler):
        set_tenant_context(db, karte["org_id"])
        _neu(db, karte, **daten)


def test_revision_aendern_verwerfen_und_geometrie(karte):
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        objekt = service.produktives_objekt(db, karte["org_id"], karte["objekt_id"])
        marker = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt.id, typ="tor").one()
        with pytest.raises(ObjektKartenFehler, match="expected_revision"):
            _neu(db, karte, operation="aendern", basis_marker_id=marker.id)
        with pytest.raises(ObjektKartenFehler, match="Versionskonflikt"):
            _neu(db, karte, operation="aendern", basis_marker_id=marker.id, expected_revision=99)
        v = _neu(db, karte, operation="aendern", basis_marker_id=marker.id, expected_revision=1)
        neu = service.vorschlag_speichern(db, objekt, {"lat": 47, "lng": 9, "rotation": 450},
                                         user_id=karte["users"]["u"], vorschlag_id=v["id"])
        assert neu["position_status"] == "pruefung_erforderlich" and neu["rotation"] == 90
        verworfen = service.vorschlag_verwerfen(db, objekt, v["id"], user_id=karte["users"]["u"])
        assert verworfen["freigabe_status"] == "abgelehnt"
        assert marker.revision == 1 and marker.lat == 47.11
        geom = _neu(db, karte, symbol_code="geometrie", geometry={"type": "Point", "coordinates": [9, 47]})
        assert geom["position_status"] == "vorgeschlagen"
        assert {c.feld for c in db.query(ObjektChange).all()} >= {"vorschlag_geaendert", "vorschlag_verworfen"}


def test_fremde_marker_quellen_vorschlaege_und_kopie(karte):
    fremd = _seed("vorschlag-fremd", {"u": "objekt_verwalter"})
    with SessionLocal() as db:
        set_tenant_context(db, None)
        objekt = service.produktives_objekt(db, karte["org_id"], karte["objekt_id"])
        marker = ObjektKartenObjekt(org_id=fremd["org_id"], objekt_id=fremd["objekt_id"], typ="tor")
        db.add(marker)
        db.flush()
        with pytest.raises(ObjektKartenFehler, match="Basismarker"):
            _neu(db, karte, operation="aendern", basis_marker_id=marker.id, expected_revision=1)
        with pytest.raises(ObjektKartenFehler, match="Quelldokument"):
            _neu(db, karte, quelle_dokument_id=999999)
        v = _neu(db, karte)
        with pytest.raises(ObjektKartenFehler):
            service.vorschlag_verwerfen(db, db.get(Objekt, fremd["objekt_id"]), v["id"], user_id=fremd["users"]["u"])
        kopie = erstelle_arbeitskopie(db, objekt, karte["users"]["u"])
        db.flush()
        with pytest.raises(ObjektKartenFehler, match="Basisobjekt"):
            service.produktives_objekt(db, karte["org_id"], kopie.id)
        assert db.query(ObjektKartenVorschlag).filter_by(objekt_id=kopie.id).count() == 0


def test_veraltet_dateihash_version_und_nur_selects(karte, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    (tmp_path / "plan.pdf").write_bytes(b"version1")
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        objekt = service.produktives_objekt(db, karte["org_id"], karte["objekt_id"])
        dokument = ObjektDokument(org_id=objekt.org_id, objekt_id=objekt.id,
                                 pfad="plan.pdf", dateiname_original="plan.pdf")
        db.add(dokument)
        db.flush()
        v = _neu(db, karte, quelle_dokument_id=dokument.id)
        assert v["quelle_dokument_hash"] == hashlib.sha256(b"version1").hexdigest()
        db.commit()
        (tmp_path / "plan.pdf").write_bytes(b"version2")
        statements = []
        conn = db.connection()
        def merken(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(conn, "before_cursor_execute", merken)
        try:
            resultat = service.pruefe_karte(db, objekt)
        finally:
            event.remove(conn, "before_cursor_execute", merken)
        assert resultat["vorschlaege"][0]["position_status"] == "veraltet"
        assert not db.dirty and all(s.lstrip().upper().startswith("SELECT") for s in statements)
        assert db.get(ObjektKartenVorschlag, v["id"]).position_status == "pruefung_erforderlich"
        # Neue Dokumentversion mit gleichem Dateiinhalt ist weiterhin gueltig.
        (tmp_path / "neu.pdf").write_bytes(b"version1")
        dokument.ist_aktuelle_version = False
        db.add(ObjektDokument(org_id=objekt.org_id, objekt_id=objekt.id, pfad="neu.pdf", dateiname_original="neu.pdf",
                             dokument_gruppe_id=dokument.id, versionsnummer=2, ist_aktuelle_version=True))
        db.flush()
        assert service.pruefe_karte(db, objekt)["vorschlaege"][0]["position_status"] != "veraltet"


def test_marker_metadaten_arbeitskopie_und_merge(karte):
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        objekt = db.get(Objekt, karte["objekt_id"])
        marker = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt.id, typ="tor").one()
        dokument = ObjektDokument(org_id=objekt.org_id, objekt_id=objekt.id,
                                 pfad="plan.pdf", dateiname_original="plan.pdf")
        db.add(dokument)
        db.flush()
        felder = {"position_status": "bestaetigt", "quelle_dokument_id": dokument.id, "quelle_seite": 4,
                  "genauigkeit_m": 2.5, "revision": 7, "geprueft_von": karte["users"]["u"],
                  "geprueft_am": datetime(2026, 10, 28, tzinfo=UTC).replace(tzinfo=None)}
        for k, wert in felder.items():
            setattr(marker, k, wert)
        kopie = erstelle_arbeitskopie(db, objekt, karte["users"]["u"])
        db.flush()
        kopierter = db.query(ObjektKartenObjekt).filter_by(objekt_id=kopie.id, typ="tor").one()
        assert {k: getattr(kopierter, k) for k in felder} == felder
        uebernimm_arbeitskopie(db, kopie, karte["users"]["u"])
        db.flush()
        produktiv = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt.id, typ="tor").one()
        assert {k: getattr(produktiv, k) for k in felder} == felder


def test_pruefung_bestand_dubletten_luecken_und_revision(karte):
    from app.services.objekt_karte_service import karten_objekt_update

    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        objekt = service.produktives_objekt(db, karte["org_id"], karte["objekt_id"])
        marker = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt.id, typ="tor").one()
        db.add(ObjektKartenObjekt(org_id=objekt.org_id, objekt_id=objekt.id, typ="tor",
                                 lat=marker.lat, lng=marker.lng))
        db.flush()
        resultat = service.pruefe_karte(db, objekt)
        assert len(resultat["dubletten"]) == 1
        assert resultat["luecken"] == ["bmz"]
        assert len(resultat["marker_ohne_koordinaten"]) == 1
        assert resultat["anzahl_ungeprueft"] == 3
        _neu(db, karte, lat=47.1, lng=9.2)
        assert _neu(db, karte, lat=47.1, lng=9.2, label="Selber Ort")["id"] == db.query(ObjektKartenVorschlag).one().id
        karten_objekt_update(db, objekt, marker.id, {"label": "Neuer Zugang"}, user_id=karte["users"]["u"])
        assert marker.revision == 2
        with pytest.raises(ObjektKartenFehler, match="Versionskonflikt"):
            _neu(db, karte, operation="entfernen", basis_marker_id=marker.id, expected_revision=1)
