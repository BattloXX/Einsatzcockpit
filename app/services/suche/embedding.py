"""Lokale Ollama-Embeddings mit Matryoshka-Kuerzung und Circuit-Breaker."""

import logging
import math
import threading
import time

import httpx

from app.config import settings

_logger = logging.getLogger(__name__)
_sperre = threading.Lock()
_fehler = 0
_offen_seit = 0.0
_halboffen = False


class EmbeddingFehler(Exception):
    """Provider nicht erreichbar oder Embedding-Antwort ungueltig."""


def setze_breaker_zurueck() -> None:
    global _fehler, _offen_seit, _halboffen
    with _sperre:
        _fehler, _offen_seit, _halboffen = 0, 0.0, False


def breaker_offen() -> bool:
    with _sperre:
        return _halboffen or (_fehler >= 5 and time.monotonic() - _offen_seit < 60)


def _beginne_versuch() -> None:
    global _halboffen
    with _sperre:
        if _halboffen or (_fehler >= 5 and time.monotonic() - _offen_seit < 60):
            raise EmbeddingFehler("Embedding-Circuit-Breaker offen")
        if _fehler >= 5:
            _halboffen = True


def _beende_versuch(erfolg: bool) -> None:
    global _fehler, _offen_seit, _halboffen
    with _sperre:
        if erfolg:
            _fehler, _offen_seit = 0, 0.0
        else:
            _fehler += 1
            if _fehler >= 5:
                _offen_seit = time.monotonic()
        _halboffen = False


class EmbeddingProvider:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def embed_anfrage(self, text: str) -> list[float]:
        return self._embed([f"task: search result | query: {text}"], settings.SEARCH_EMBED_QUERY_TIMEOUT_S)[0]

    def embed_dokumente(self, paare: list[tuple[str, str]]) -> list[list[float]]:
        if settings.SEARCH_EMBED_BATCH < 1:
            raise EmbeddingFehler("Ungueltige Batchgroesse")
        vektoren = []
        for start in range(0, len(paare), settings.SEARCH_EMBED_BATCH):
            texte = [f"title: {titel} | text: {text}"
                     for titel, text in paare[start:start + settings.SEARCH_EMBED_BATCH]]
            vektoren.extend(self._embed(texte, settings.SEARCH_EMBED_BATCH_TIMEOUT_S))
        return vektoren

    def _embed(self, texte: list[str], timeout: float) -> list[list[float]]:
        _beginne_versuch()
        try:
            with httpx.Client(transport=self.transport, timeout=timeout) as client:
                antwort = client.post(settings.SEARCH_EMBED_URL.rstrip("/") + "/api/embed",
                                      json={"model": settings.SEARCH_EMBED_MODEL, "input": texte})
                antwort.raise_for_status()
                daten = antwort.json()
            if not isinstance(daten, dict) or not isinstance(daten.get("embeddings"), list):
                raise ValueError("Ungueltige Embedding-Antwort")
            roh = daten["embeddings"]
            if len(roh) != len(texte):
                raise ValueError("Falsche Vektoranzahl")
            dim = settings.SEARCH_EMBED_DIM
            vektoren = []
            for vektor in roh:
                if dim < 1 or not isinstance(vektor, list) or len(vektor) < dim:
                    raise ValueError("Falsche Vektordimension")
                if any(isinstance(wert, bool) or not isinstance(wert, (int, float))
                       or not math.isfinite(wert) for wert in vektor):
                    raise ValueError("Ungueltige Vektorwerte")
                gekuerzt = [float(wert) for wert in vektor[:dim]]
                norm = math.hypot(*gekuerzt)
                if not math.isfinite(norm) or norm == 0:
                    raise ValueError("Ungueltige Vektornorm")
                vektoren.append([wert / norm for wert in gekuerzt])
        except (httpx.HTTPError, ValueError, TypeError, OverflowError) as exc:
            _beende_versuch(False)
            _logger.warning("%s", type(exc).__name__)
            raise EmbeddingFehler("Embedding-Aufruf fehlgeschlagen") from None
        _beende_versuch(True)
        return vektoren
