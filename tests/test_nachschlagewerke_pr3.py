"""Nachschlagewerke PR 3: Offline-Index (index.json) + Service-Worker-Cache-Bucket."""
from pathlib import Path

from app.services import gefahrgut_service as gg

SW_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "sw.js"
JS_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "nachschlagewerke.js"


def _sw() -> str:
    return SW_PATH.read_text(encoding="utf-8")


# ── Datensatz-Index ───────────────────────────────────────────────────────────

def test_alle_eintraege_enthaelt_links_und_un4():
    eintraege = gg.alle_eintraege()
    assert eintraege
    e = next(x for x in eintraege if gg._norm_un(x["un_nummer"]) == "1203")
    assert e["un_vierstellig"] == "1203"
    assert e["links"]


def test_alle_eintraege_sortiert_nach_un():
    eintraege = gg.alle_eintraege()
    keys = [gg._norm_un(e["un_nummer"]).zfill(6) for e in eintraege]
    assert keys == sorted(keys)


# ── Service Worker ────────────────────────────────────────────────────────────

def test_sw_hat_nachschlagewerk_cache_bucket():
    src = _sw()
    assert "ec-nachschlagewerk-v1" in src
    # Bucket steht in der activate-Whitelist (wird bei App-Update NICHT geloescht)
    assert "k !== NW_CACHE" in src


def test_sw_index_json_network_first():
    src = _sw()
    assert "/nachschlagewerke/gefahrgut/index.json" in src


def test_sw_rettungskarten_prefix_cache_first():
    src = _sw()
    assert "/nachschlagewerk-cache/" in src


def test_sw_cache_version_erhoeht():
    # JS-Aenderung -> App-Shell-Cache-Version muss hochgezaehlt sein (mind. v6).
    assert "const CACHE = 'ec-v6'" in _sw()


def test_client_js_vorhanden():
    src = JS_PATH.read_text(encoding="utf-8")
    assert "index.json" in src
    # Nur gerade ASCII-Quotes in Attributen/Logik (Smart Quotes nur in Anzeigetext)
    assert "gg-treffer" in src
