"""Embedding-Praefixe, Normalisierung und Ausfallschutz ohne externen Host."""

import json
import math

import httpx
import pytest

from app.config import Settings, settings
from app.services.suche import embedding
from app.services.suche.embedding import EmbeddingFehler, EmbeddingProvider


@pytest.fixture(autouse=True)
def konfiguration(monkeypatch):
    embedding.setze_breaker_zurueck()
    monkeypatch.setattr(settings, "SEARCH_EMBED_DIM", 2)
    monkeypatch.setattr(settings, "SEARCH_EMBED_BATCH", 16)
    monkeypatch.setattr(settings, "SEARCH_EMBED_URL", "http://localhost:11434/")
    monkeypatch.setattr(settings, "SEARCH_EMBED_MODEL", "embeddinggemma")
    monkeypatch.setattr(settings, "SEARCH_EMBED_QUERY_TIMEOUT_S", 2.0)
    monkeypatch.setattr(settings, "SEARCH_EMBED_BATCH_TIMEOUT_S", 60.0)
    yield
    embedding.setze_breaker_zurueck()


def test_config_defaults():
    defaults = Settings(_env_file=None)
    assert defaults.SEARCH_SEMANTIC_ENABLED is False
    assert defaults.SEARCH_EMBED_URL == "http://localhost:11434"
    assert defaults.SEARCH_EMBED_MODEL == "embeddinggemma"
    assert defaults.SEARCH_EMBED_DIM == 768
    assert defaults.SEARCH_EMBED_QUERY_TIMEOUT_S == 2.0
    assert defaults.SEARCH_EMBED_BATCH_TIMEOUT_S == 60.0
    assert defaults.SEARCH_EMBED_BATCH == 16
    assert defaults.SEARCH_SEMANTIC_MAX_DISTANZ == 0.6


def test_praefixe_batching_kuerzung_und_norm():
    requests = []

    def antwort(request):
        requests.append(request)
        daten = json.loads(request.content)
        assert daten["model"] == "embeddinggemma"
        assert str(request.url) == "http://localhost:11434/api/embed"
        return httpx.Response(200, json={"embeddings": [[3, 4, 100] for _ in daten["input"]]})

    provider = EmbeddingProvider(httpx.MockTransport(antwort))
    assert provider.embed_anfrage("Wo ist die BMZ?") == pytest.approx([0.6, 0.8])
    vektoren = provider.embed_dokumente([(f"Titel {i}", f"Text {i}") for i in range(17)])
    assert len(vektoren) == 17
    assert all(len(v) == 2 and math.hypot(*v) == pytest.approx(1) for v in vektoren)
    assert [json.loads(r.content)["input"] for r in requests] == [
        ["task: search result | query: Wo ist die BMZ?"],
        [f"title: Titel {i} | text: Text {i}" for i in range(16)],
        ["title: Titel 16 | text: Text 16"],
    ]
    assert [r.extensions["timeout"]["read"] for r in requests] == [2.0, 60.0, 60.0]
    assert provider.embed_dokumente([]) == []
    assert len(requests) == 3


@pytest.mark.parametrize("daten", [
    {"embeddings": [[1]]}, {"embeddings": []}, {"embeddings": [[1, 2], [3, 4]]},
    {"embeddings": [[0, 0]]}, {"embeddings": [["1", 2]]}, {"embeddings": [[True, 2]]},
    {"embeddings": None}, {}, [],
])
def test_ungueltige_antwort(daten):
    provider = EmbeddingProvider(httpx.MockTransport(lambda _: httpx.Response(200, json=daten)))
    with pytest.raises(EmbeddingFehler):
        provider.embed_anfrage("vertraulich")


def test_falsche_batchanzahl():
    provider = EmbeddingProvider(httpx.MockTransport(
        lambda _: httpx.Response(200, json={"embeddings": [[1, 2]]})))
    with pytest.raises(EmbeddingFehler):
        provider.embed_dokumente([("A", "B"), ("C", "D")])


@pytest.mark.parametrize("fehler", ["http", "timeout", "json"])
def test_http_timeout_und_logs(fehler, caplog):
    def antwort(request):
        if fehler == "timeout":
            raise httpx.ReadTimeout("vertraulicher Antworttext", request=request)
        if fehler == "json":
            return httpx.Response(200, text="vertraulicher Antworttext")
        return httpx.Response(500, text="vertraulicher Antworttext")

    provider = EmbeddingProvider(httpx.MockTransport(antwort))
    with pytest.raises(EmbeddingFehler) as exc:
        provider.embed_anfrage("vertraulicher Suchtext")
    assert "vertraulich" not in caplog.text
    assert "vertraulich" not in str(exc.value)
    assert exc.value.__suppress_context__


def test_breaker_oeffnet_halboffen_und_erfolg(monkeypatch):
    zeit = [100.0]
    monkeypatch.setattr(embedding.time, "monotonic", lambda: zeit[0])
    requests = []
    status = [500]

    def antwort(request):
        requests.append(request)
        if len(requests) == 6:
            assert embedding.breaker_offen()
            with pytest.raises(EmbeddingFehler):
                provider.embed_anfrage("Parallelversuch")
        return httpx.Response(status[0], json={"embeddings": [[1, 0]]})

    provider = EmbeddingProvider(httpx.MockTransport(antwort))
    for i in range(5):
        with pytest.raises(EmbeddingFehler):
            provider.embed_anfrage("Test")
        assert embedding.breaker_offen() is (i == 4)
    with pytest.raises(EmbeddingFehler):
        EmbeddingProvider(httpx.MockTransport(antwort)).embed_anfrage("Test")
    assert len(requests) == 5
    zeit[0] += 59.9
    assert embedding.breaker_offen()
    zeit[0] += 0.1
    assert not embedding.breaker_offen()
    # Ein erfolgloser Halb-offen-Versuch oeffnet erneut fuer 60 Sekunden.
    with pytest.raises(EmbeddingFehler):
        provider.embed_anfrage("Test")
    assert embedding.breaker_offen()
    zeit[0] += 60
    status[0] = 200
    assert provider.embed_anfrage("Test") == [1, 0]
    assert not embedding.breaker_offen()
    status[0] = 500
    with pytest.raises(EmbeddingFehler):
        provider.embed_anfrage("Test")
    assert not embedding.breaker_offen()
