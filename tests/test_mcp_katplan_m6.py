"""M6: Quellenabdeckung, Dubletten und lesender Arbeitsstand-Abgleich."""

import asyncio

import pytest

from app.core.tenant import set_tenant_context
from app.mcp.registry import TOOLS
from app.models.katplan import KatplanDokument, KatplanElement, KatplanImportVorschlag, KatplanPlan
from app.models.user import AuditLog, User
from app.services import katplan_abgleich_service as service
from app.services.katplan_quelle_service import quelle_hochladen
from tests.conftest import TestingSession
from tests.test_katplan_k1b import umgebung  # noqa: F401
from tests.test_katplan_k7b import _pdf_bytes
from tests.test_katplan_k8 import daten  # noqa: F401
from tests.test_mcp_katplan import inhalte, rufe  # noqa: F401
from tests.test_mcp_katplan_m3 import quelle as quelle
from tests.test_mcp_katplan_schreiben import schreiben


def abgleichen(info, **kwargs):
    return schreiben(info, "katplan_import_abgleichen", dokument_id=info["dokument_id"], **kwargs)


def test_seiten_menge():
    assert service.seiten_menge("") == set()
    assert service.seiten_menge("3-4,7,4") == {3, 4, 7}
    for wert in ("0", "4-3", "1,,2", "abc"):
        with pytest.raises(ValueError):
            service.seiten_menge(wert)


def test_abdeckung_vorschlaege_audit_und_lesend(quelle):
    schreiben(quelle, "katplan_vorschlaege_aus_dokument", dokument_id=quelle["dokument_id"],
              seiten_nr=3, elemente=[{"typ": "info", "titel": "A"}, {"typ": "info", "titel": "B"}])
    with TestingSession() as db:
        set_tenant_context(db, None)
        v = db.query(KatplanImportVorschlag).filter_by(plan_id=quelle["plan_id"]).first()
        v.status = "verworfen"
        plan = db.get(KatplanPlan, quelle["plan_id"])
        vorher = plan.version
        db.commit()
    result = abgleichen(quelle)
    assert result["quelle"]["id"] == quelle["dokument_id"]
    assert [s["seiten_nr"] for s in result["seiten_ohne_element"]] == list(range(3, 26))
    seite = result["seiten_ohne_element"][0]
    assert seite["titel"] == "Titel 3" and seite["textauszug"] == "Quelltext Seite 3"
    assert seite["thumb_url"].endswith("/seite/3/thumb")
    assert result["offene_vorschlaege"] == [{"seiten_nr": 3, "anzahl": 1}]
    assert {e["kennung"] for e in result["elemente_ohne_quelle"]} == {"KS-02", "IN-01"}
    assert result["veraltete_quellbezuege"] == []
    assert 0 < len(result["naechste_schritte"]) <= 30
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.get(KatplanPlan, quelle["plan_id"]).version == vorher
        assert db.query(AuditLog).filter_by(action="mcp.katplan_import_abgleichen",
                                           entity_id=quelle["plan_id"]).count() == 1
        dok = db.get(KatplanDokument, quelle["dokument_id"])
        assert f"/quelle/{dok.uuid}/" in seite["thumb_url"]


def test_neue_quellversion(quelle):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, quelle["plan_id"])
        user = db.query(User).filter_by(username=quelle["users"]["katplan_verwalter"]).one()
        neu = quelle_hochladen(db, plan, user, _pdf_bytes(), "neu.pdf", titel="Version 2",
                              ersetzt_dokument_id=quelle["dokument_id"])
        db.commit()
        neu_id = neu.id
    result = schreiben(quelle, "katplan_import_abgleichen", dokument_id=neu_id)
    assert [s["seiten_nr"] for s in result["seiten_ohne_element"]] == [1, 2]
    assert result["veraltete_quellbezuege"] == [{
        "kennung": "KS-01", "titel": "Wasser Brücke", "dokument_id": quelle["dokument_id"], "versionsnummer": 1}]
    assert abgleichen(quelle)["veraltete_quellbezuege"] == []


def test_dubletten():
    def element(kennung, titel, typ="massnahme", **kwargs):
        return KatplanElement(kennung=kennung, uuid=kennung, typ=typ, titel=titel, **kwargs)

    a = element("M-01", "  PUMPE   aufbauen ")
    b = element("M-02", "pumpe aufbauen")
    c = element("M-03", "Sperre", strasse="Dorfstraße", hausnummer="7")
    d = element("M-04", "Evakuierung", strasse=" DORFSTRASSE ", hausnummer="7")
    anderer_typ = element("I-01", a.titel, typ="info", strasse="Dorfstraße", hausnummer="7")
    result = service.moegliche_dubletten([a, b, c, d, anderer_typ])
    assert [(x["a"]["kennung"], x["b"]["kennung"]) for x in result] == [("M-01", "M-02"), ("M-03", "M-04")]
    assert result[0]["aehnlichkeit"] == 1 and result[1]["grund"] == "Gleiche Adresse"
    assert len(service.moegliche_dubletten([element(str(n), "Gleich") for n in range(20)])) == 50
    assert service.moegliche_dubletten([a, b], schwelle=1.01) == []


def test_warnung_ohne_blockade(quelle):
    result = schreiben(quelle, "katplan_vorschlaege_aus_dokument", dokument_id=quelle["dokument_id"],
                       seiten_nr=1, elemente=[{"typ": "kontrollstelle", "titel": "Wasser Brücke"},
                                              {"typ": "massnahme", "titel": "Wasser Brücke"}])
    assert result["anzahl"] == 2
    assert result["warnungen"] == ["Vorschlag 1 'Wasser Brücke' ähnelt KS-01 'Wasser Brücke'"]
    assert abgleichen(quelle)["offene_vorschlaege"] == [{"seiten_nr": 1, "anzahl": 2}]


def test_rechte_und_fremde_quellen(quelle):
    with pytest.raises(PermissionError, match="Arbeitsstand"):
        rufe(quelle, "katplan_import_abgleichen", plan_id=quelle["plan_id"], dokument_id=quelle["dokument_id"])
    with pytest.raises(ValueError, match="Nicht gefunden"):
        rufe(quelle, "katplan_import_abgleichen", "fremd", plan_id=quelle["plan_id"],
             dokument_id=quelle["dokument_id"])
    with TestingSession() as db:
        set_tenant_context(db, None)
        anderer = KatplanPlan(org_id=quelle["org_id"], titel="Anderer Plan")
        db.add(anderer)
        db.flush()
        dokument = db.get(KatplanDokument, quelle["neu_id"])
        dokument.plan_id = anderer.id
        db.commit()
    with pytest.raises(ValueError, match="Nicht gefunden"):
        schreiben(quelle, "katplan_import_abgleichen", dokument_id=quelle["neu_id"])
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.get(KatplanDokument, quelle["dokument_id"]).archiviert = True
        db.commit()
    with pytest.raises(ValueError, match="Nicht gefunden"):
        abgleichen(quelle)


def test_vertraulichkeitsregel(quelle, monkeypatch):
    monkeypatch.setattr(service, "sieht_vertraulich", lambda user: False)
    result = abgleichen(quelle)
    assert "IN-01" not in {e["kennung"] for e in result["elemente_ohne_quelle"]}


def test_wrapper_und_registrierung(quelle, monkeypatch):
    from app.core.permissions import KATPLAN_LESE_ROLLEN
    from app.mcp import server

    assert TOOLS["katplan_import_abgleichen"].required_roles == KATPLAN_LESE_ROLLEN
    assert TOOLS["katplan_import_abgleichen"].module_check is not None

    async def call(name, **kwargs):
        assert name == "katplan_import_abgleichen"
        assert kwargs == {"plan_id": quelle["plan_id"], "dokument_id": quelle["dokument_id"]}
        return {"ok": True}

    monkeypatch.setattr(server, "_call_registered_tool", call)
    assert asyncio.run(server.katplan_import_abgleichen(quelle["plan_id"], quelle["dokument_id"])) == {"ok": True}


def test_abgleich_dubletten_fehlende_seite_und_textlimit(quelle):
    from app.models.katplan import KatplanDokumentSeite

    with TestingSession() as db:
        set_tenant_context(db, None)
        e = db.query(KatplanElement).filter_by(plan_id=quelle["plan_id"], kennung="KS-02").one()
        e.titel = "Wasser Brücke"
        seite = db.query(KatplanDokumentSeite).filter_by(dokument_id=quelle["dokument_id"], seiten_nr=3).one()
        seite.volltext = "X" * 250
        db.delete(db.query(KatplanDokumentSeite).filter_by(dokument_id=quelle["dokument_id"], seiten_nr=25).one())
        db.commit()
    result = abgleichen(quelle)
    assert result["seiten_ohne_element"][0]["textauszug"] == "X" * 200
    assert result["seiten_ohne_element"][-1]["titel"] is None
    assert result["seiten_ohne_element"][-1]["textauszug"] == ""
    assert len(result["dubletten"]) == 1
    assert {result["dubletten"][0][k]["kennung"] for k in ("a", "b")} == {"KS-01", "KS-02"}


def test_offene_vorschlaege_nach_dokument_und_seite(quelle):
    for nr in (1, 1, 2):
        schreiben(quelle, "katplan_vorschlaege_aus_dokument", dokument_id=quelle["dokument_id"],
                  seiten_nr=nr, elemente=[{"typ": "info", "titel": "Hinweis"}])
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.add(KatplanImportVorschlag(org_id=quelle["org_id"], plan_id=quelle["plan_id"],
                                      dokument_id=quelle["neu_id"], seiten_nr=1, typ="info", titel="Andere Quelle"))
        db.commit()
    assert abgleichen(quelle)["offene_vorschlaege"] == [{"seiten_nr": 1, "anzahl": 2}, {"seiten_nr": 2, "anzahl": 1}]


def test_fremde_org_dokument(quelle):
    with TestingSession() as db:
        set_tenant_context(db, None)
        fremd = db.query(User).filter_by(username=quelle["users"]["fremd"]).one()
        db.get(KatplanDokument, quelle["dokument_id"]).org_id = fremd.org_id
        db.commit()
    with pytest.raises(ValueError, match="Nicht gefunden"):
        abgleichen(quelle)
