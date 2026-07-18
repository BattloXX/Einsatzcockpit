"""Nachschlagewerke PR 2: taeglicher Gefahrgut-Sync (Fetch, Validierung, atomar)."""
import asyncio

import pytest

from app.services import gefahrgut_service as gg
from app.services import nachschlagewerk_sync as sync


def _csv_text(n_rows: int) -> str:
    kopf = "un_nummer;stoffname;klasse;klassifizierungscode;gefahrnummer;verpackungsgruppe"
    zeilen = [f"{1000 + i};Testsoff {i};3;F1;30;II" for i in range(n_rows)]
    return "\n".join([kopf, *zeilen]) + "\n"


class _FakeResp:
    def __init__(self, payload, status=200):
        # sync_gefahrgut liest resp.content (Bytes); str wird als UTF-8 kodiert.
        self.content = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        self.text = payload if isinstance(payload, str) else payload.decode("utf-8", "ignore")
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=self)


class _FakeClient:
    def __init__(self, resp, **kw):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return self._resp


@pytest.fixture(autouse=True)
def _reset_cache():
    gg.invalidate_cache()
    yield
    gg.invalidate_cache()


# ── Validierung ───────────────────────────────────────────────────────────────

def test_valide_csv_gut():
    assert sync._valide_csv(_csv_text(60)) == 60


def test_valide_csv_leer_oder_kaputt():
    assert sync._valide_csv("") == 0
    assert sync._valide_csv("keine;spalten;hier\n1;2;3") == 0  # keine UN-Spalte


def test_seconds_until_next_im_intervall():
    s = sync._seconds_until_next(3, 0)
    assert 0 < s <= 86400


# ── sync_gefahrgut ────────────────────────────────────────────────────────────

def test_sync_ohne_url_false(monkeypatch):
    monkeypatch.setattr(sync.settings, "NACHSCHLAGEWERK_GEFAHRGUT_URL", "")
    assert asyncio.run(sync.sync_gefahrgut()) is False


def test_sync_unplausibel_wird_nicht_uebernommen(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.settings, "NACHSCHLAGEWERK_GEFAHRGUT_URL", "https://example.test/g.csv")
    monkeypatch.setattr(sync.settings, "NACHSCHLAGEWERK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp(_csv_text(5))))
    assert asyncio.run(sync.sync_gefahrgut()) is False
    assert not (tmp_path / "bam_gefahrgut.csv").exists()


def test_sync_erfolg_schreibt_atomar_und_invalidiert(monkeypatch, tmp_path):
    # sync.settings ist dasselbe Singleton, das gefahrgut_service._csv_pfad lokal importiert.
    monkeypatch.setattr(sync.settings, "NACHSCHLAGEWERK_GEFAHRGUT_URL", "https://example.test/g.csv")
    monkeypatch.setattr(sync.settings, "NACHSCHLAGEWERK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp(_csv_text(60))))

    ok = asyncio.run(sync.sync_gefahrgut())
    assert ok is True
    ziel = tmp_path / "bam_gefahrgut.csv"
    assert ziel.exists()
    # Kein Temp-Rest im Verzeichnis
    assert not list(tmp_path.glob(".bam_*.tmp"))
    # gefahrgut_service nutzt jetzt die gesyncte Datei
    assert gg._csv_pfad() == ziel
    treffer = gg.suche("1000")
    assert treffer and treffer[0]["un_vierstellig"] == "1000"


def test_sync_http_fehler_false(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.settings, "NACHSCHLAGEWERK_GEFAHRGUT_URL", "https://example.test/g.csv")
    monkeypatch.setattr(sync.settings, "NACHSCHLAGEWERK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp("", status=500)))
    assert asyncio.run(sync.sync_gefahrgut()) is False


# ── ZIP-Entpacken (BAM-Download) ──────────────────────────────────────────────

def _zip_bytes(members: dict[str, str]) -> bytes:
    import io as _io
    import zipfile as _zip
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as zf:
        for name, inhalt in members.items():
            zf.writestr(name, inhalt)
    return buf.getvalue()


def test_ist_zip_erkennung():
    assert sync._ist_zip(_zip_bytes({"a.csv": _csv_text(1)})) is True
    assert sync._ist_zip(_csv_text(1).encode("utf-8")) is False


def test_extrahiere_csv_direkt():
    text = sync._extrahiere_csv(_csv_text(60).encode("utf-8"))
    assert text is not None and sync._valide_csv(text) == 60


def test_extrahiere_csv_waehlt_daten_ueber_status():
    # Status-Datei hat keine UN-Spalte -> Gefahrgutdaten (viele UN-Zeilen) gewinnt.
    status = "bam_nr;status\n1;aktiv\n2;alt\n"
    payload = _zip_bytes({
        "BAM-Gefahrgutstatus.csv": status,
        "BAM-Gefahrgutdaten.csv": _csv_text(80),
    })
    text = sync._extrahiere_csv(payload)
    assert text is not None and sync._valide_csv(text) == 80


def test_extrahiere_csv_kaputtes_zip():
    assert sync._extrahiere_csv(b"PK\x03\x04kaputt") is None


def test_sync_uebernimmt_zip(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.settings, "NACHSCHLAGEWERK_GEFAHRGUT_URL", "https://example.test/g.zip")
    monkeypatch.setattr(sync.settings, "NACHSCHLAGEWERK_DATA_DIR", str(tmp_path))
    payload = _zip_bytes({
        "BAM-Gefahrgutstatus.csv": "bam_nr;status\n1;aktiv\n",
        "BAM-Gefahrgutdaten.csv": _csv_text(60),
    })
    monkeypatch.setattr(sync.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp(payload)))
    assert asyncio.run(sync.sync_gefahrgut()) is True
    ziel = tmp_path / "bam_gefahrgut.csv"
    assert ziel.exists()
    assert gg._csv_pfad() == ziel
    assert gg.suche("1000")


def test_decode_cp1252_umlaute():
    roh = "un_nummer;stoffname\n1017;Chlor \xe4tzend\n".encode("cp1252")
    assert "ätzend" in sync._decode(roh)
