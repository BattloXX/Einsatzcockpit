"""S8c: Semantische Kandidaten, hybride Fusion und Ausgabe ohne Vektordatenbank."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.config import settings
from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.mcp.context import MCPContext
from app.mcp.tools import suche as mcp_suche
from app.models.master import FireDept, OrgSettings
from app.models.suche import SearchChunk, SearchDocument
from app.services.katplan_suche_service import markiere
from app.services.suche import abfrage
from app.services.suche.embedding import EmbeddingFehler
from app.services.suche.resolver import ResolverErgebnis
from app.services.suche.textsuche import Kandidat
from app.services.suche.vertrag import SuchAnfrage, SuchErgebnis, Treffer
from tests.conftest import TestingSession


@pytest.fixture
def daten(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_SEMANTIC_ENABLED", True)
    monkeypatch.setattr(settings, "SEARCH_SEMANTIC_MAX_DISTANZ", 0.5)
    monkeypatch.setattr(abfrage.embedding, "breaker_offen", lambda: False)
    faehig = Mock(return_value=True)
    monkeypatch.setattr(abfrage, "vektor_faehig", faehig)
    provider = Mock()
    provider.return_value.embed_anfrage.return_value = [1.0, 0.0]
    monkeypatch.setattr(abfrage, "EmbeddingProvider", provider)
    nachbarn = Mock(return_value=[])
    monkeypatch.setattr(abfrage, "knn", nachbarn)
    monkeypatch.setattr(abfrage.resolver, "pruefe", lambda *_, **__: ResolverErgebnis("/test", "freigegeben"))
    with TestingSession() as db:
        set_tenant_context(db, None)
        orgs = [FireDept(slug=uuid4().hex, name=name) for name in ("Semantik A", "Semantik B")]
        db.add_all(orgs)
        db.flush()
        for org in orgs:
            db.add(OrgSettings(org_id=org.id, suche_aktiv=True))
        dokumente = []
        for org, titel in [(orgs[0], "Brand Fabrik"), (orgs[0], "Sammelplatz"), (orgs[1], "Fremd")]:
            dokument = SearchDocument(org_id=org.id, quelle="objekt", quell_id=uuid4().hex,
                                      titel=titel, inhalt_hash="a" * 64)
            dokument.chunks = [SearchChunk(org_id=org.id, chunk_nr=1,
                                           text=f"{titel}\nHier versammelt sich die Mannschaft. " * 20,
                                           inhalt_hash="b" * 64)]
            db.add(dokument)
            dokumente.append(dokument)
        db.flush()
        user = SimpleNamespace(org_id=orgs[0].id, roles=[SimpleNamespace(code="readonly")])
        yield SimpleNamespace(db=db, user=user, orgs=orgs, docs=dokumente, provider=provider,
                              knn=nachbarn, faehig=faehig)
        db.rollback()


def _nachbar(dokument, distanz=0.2):
    return dokument.id, dokument.chunks[0].id, distanz


def _suche(daten, q="Wo versammelt sich das Team?", **kwargs):
    return abfrage.suche(daten.db, daten.user, SuchAnfrage(q=q, typen=["objekt"], **kwargs))


def test_hybrid_fusion_und_unveraenderte_frage(daten, monkeypatch):
    gemeinsam, semantisch, _ = daten.docs
    monkeypatch.setattr(abfrage, "volltext_kandidaten", lambda *_, **__: [
        Kandidat(gemeinsam.id, gemeinsam.chunks[0].id, 1),
    ])
    daten.knn.return_value = [_nachbar(semantisch, 0.1), _nachbar(gemeinsam, 0.3)]
    result = _suche(daten, limit=3)
    assert [t.quell_id for t in result.treffer] == [gemeinsam.quell_id, semantisch.quell_id]
    assert result.treffer[0].grund == ["volltext", "semantisch"]
    assert result.treffer[1].grund == ["semantisch"]
    assert "Hier versammelt" in result.treffer[1].auszug
    assert 0 < len(result.treffer[1].auszug) <= 300
    assert result.modi == ["exakt", "volltext", "semantisch"]
    assert not result.teilweise
    daten.provider.return_value.embed_anfrage.assert_called_once_with("Wo versammelt sich das Team?")
    daten.knn.assert_called_once_with(daten.db, daten.user.org_id, [1.0, 0.0], ["objekt"], 15)


def test_distanz_schwellwert_und_sortierung(daten):
    nah, grenze, fern = daten.docs
    # Fuer diesen Test liegt auch der dritte Kandidat in der eigenen Organisation.
    fern.org_id = daten.user.org_id
    fern.chunks[0].org_id = daten.user.org_id
    daten.db.flush()
    daten.knn.return_value = [_nachbar(grenze, 0.5), _nachbar(fern, 0.5001), _nachbar(nah, 0.1)]
    result = _suche(daten)
    assert [t.quell_id for t in result.treffer] == [nah.quell_id, grenze.quell_id]


def test_vertraulich_ohne_berechtigung(daten):
    dokument = daten.docs[1]
    dokument.sichtbarkeit = "vertraulich"
    daten.db.flush()
    daten.knn.return_value = [_nachbar(dokument)]
    assert not _suche(daten).treffer
    daten.user.roles = [SimpleNamespace(code="incident_leader")]
    assert _suche(daten).treffer[0].grund == ["semantisch"]


def test_resolver_verwirft(daten, monkeypatch):
    daten.knn.return_value = [_nachbar(daten.docs[1])]
    monkeypatch.setattr(abfrage.resolver, "pruefe", lambda *_, **__: None)
    assert not _suche(daten).treffer


@pytest.mark.parametrize("fehler", [EmbeddingFehler("Provider ausgefallen"), RuntimeError("unerwartet")])
def test_provider_fehler_volltext_bleibt(daten, fehler):
    daten.provider.return_value.embed_anfrage.side_effect = fehler
    result = _suche(daten, q="Brand")
    assert result.treffer[0].quell_id == daten.docs[0].quell_id
    assert result.treffer[0].grund == ["volltext"]
    assert result.teilweise
    assert result.hinweise == ["Semantik nicht verfügbar"]
    assert "semantisch" in result.modi
    daten.knn.assert_not_called()


def test_offener_breaker_still_ueberspringen(daten, monkeypatch):
    monkeypatch.setattr(abfrage.embedding, "breaker_offen", lambda: True)
    result = _suche(daten)
    assert "semantisch" not in result.modi
    assert not result.teilweise and not result.hinweise
    daten.provider.assert_not_called()
    daten.knn.assert_not_called()
    daten.faehig.assert_not_called()


@pytest.mark.parametrize("q", ["B", "BM", "BMZ"])
def test_mindestlaenge(daten, q):
    result = _suche(daten, q=q)
    assert ("semantisch" in result.modi) == (len(q) >= 3)
    assert daten.provider.call_count == int(len(q) >= 3)


def test_flag_aus_provider_nie_aufgerufen(daten, monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_SEMANTIC_ENABLED", False)
    assert "semantisch" not in _suche(daten).modi
    daten.provider.assert_not_called()
    daten.knn.assert_not_called()


def test_sqlite_ohne_vektorfaehigkeit(daten):
    daten.faehig.return_value = False
    assert "semantisch" not in _suche(daten).modi
    daten.provider.assert_not_called()


def test_org_trennung_auch_bei_fremdem_kandidaten(daten):
    eigen, fremd = daten.docs[1:]
    daten.knn.return_value = [_nachbar(fremd, 0.1), _nachbar(eigen, 0.2)]
    result = _suche(daten)
    assert [t.quell_id for t in result.treffer] == [eigen.quell_id]
    assert daten.knn.call_args.args[1] == daten.orgs[0].id
    daten.user.org_id = daten.orgs[1].id
    result = _suche(daten)
    assert [t.quell_id for t in result.treffer] == [fremd.quell_id]
    assert daten.knn.call_args.args[1] == daten.orgs[1].id


def test_org_schalter_aus(daten):
    daten.db.query(OrgSettings).filter_by(org_id=daten.user.org_id).one().suche_aktiv = False
    daten.db.flush()
    assert "semantisch" not in _suche(daten).modi
    daten.provider.assert_not_called()


def test_ohne_indexquellen(daten):
    result = abfrage.suche(daten.db, daten.user, SuchAnfrage(q="Team", typen=["kontakt"]))
    assert "semantisch" not in result.modi
    daten.provider.assert_not_called()


def test_leerer_auszug_faellt_auf_chunkanfang_zurueck(daten, monkeypatch):
    daten.knn.return_value = [_nachbar(daten.docs[1])]
    monkeypatch.setattr(abfrage, "ausschnitt", lambda *_, **__: "")
    result = _suche(daten)
    assert result.treffer[0].auszug.startswith("Hier versammelt")
    assert len(result.treffer[0].auszug) == 300


def test_ui_badge_semantisch():
    treffer = Treffer(quelle="objekt", quell_id="1", titel="Sammelplatz", url="/test", grund=["semantisch"])
    html = templates.env.get_template("suche/_treffer.html").render(
        q="Team", ergebnis=SuchErgebnis(treffer=[treffer]), typ_labels={}, markiere=markiere,
    )
    assert '<span class="suche-badge">Semantisch</span>' in html


async def test_mcp_grund_unveraendert_ohne_neue_felder(daten, monkeypatch):
    daten.knn.return_value = [_nachbar(daten.docs[1])]
    ergebnis = _suche(daten)
    monkeypatch.setattr(mcp_suche, "suche", lambda *_, **__: ergebnis)
    monkeypatch.setattr(mcp_suche, "_kontakt_zugriff", lambda *_, **__: "keiner")
    monkeypatch.setattr(mcp_suche, "_details_erlaubt", lambda *_, **__: False)
    result = await mcp_suche.ecp_suchen(MCPContext(daten.db, daten.user, daten.user.org_id), q="Team")
    assert result["modi"] == ergebnis.modi
    assert result["treffer"][0]["grund"] == ["semantisch"]
    assert set(result["treffer"][0]) == set(mcp_suche._FELDER)


@pytest.mark.parametrize("grund", ["volltext", "semantisch"])
def test_katplan_filter_auf_allen_indexstufen(daten, monkeypatch, grund):
    eigen, anderer_plan, andere_quelle = daten.docs
    anderer_plan.org_id = daten.user.org_id
    anderer_plan.chunks[0].org_id = daten.user.org_id
    for doc, plan_id in ((eigen, 7), (anderer_plan, 8)):
        doc.quelle = "katplan_element"
        doc.meta_json = json.dumps({"plan_id": plan_id, "typ": "info", "prioritaet": 1, "phase_ids": [17]})
    andere_quelle.org_id = daten.user.org_id
    andere_quelle.chunks[0].org_id = daten.user.org_id
    daten.db.flush()
    kandidaten = [Kandidat(d.id, d.chunks[0].id, r) for r, d in enumerate(daten.docs, 1)]
    monkeypatch.setattr(abfrage, "volltext_kandidaten", lambda *_, **__: kandidaten if grund == "volltext" else [])
    daten.knn.return_value = [_nachbar(d) for d in daten.docs] if grund == "semantisch" else []
    anfrage = SuchAnfrage(q="Unbekannte Frage", plan_id=7,
                          filter={"typ": "info", "prioritaet": 1, "phase_id": 17})
    result = abfrage.suche(daten.db, daten.user, anfrage)
    assert [t.quell_id for t in result.treffer] == [eigen.quell_id]
    assert result.treffer[0].grund == [grund] and not result.teilweise
    anfrage.filter["phase_id"] = 18
    assert not abfrage.suche(daten.db, daten.user, anfrage).treffer
