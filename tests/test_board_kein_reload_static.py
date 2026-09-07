"""Statische Regressionstests gegen Voll-Reloads auf den beiden Live-Boards."""
from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "app/static/js/app.js"
LAGE_JS = ROOT / "app/static/js/lage_board.js"


def test_board_javascript_enthaelt_keine_voll_reloads():
    assert "location.reload()" not in LAGE_JS.read_text()
    app_lines = APP_JS.read_text().splitlines()
    unerlaubt = [line for line in app_lines if "location.reload()" in line and "troopsGrid" not in line]
    assert unerlaubt == []


def test_reload_board_ist_vollstaendig_entfernt():
    treffer = []
    for path in (ROOT / "app").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".js", ".html"}:
            if "reload_board" in path.read_text(errors="ignore"):
                treffer.append(path.relative_to(ROOT))
    assert treffer == []


def _incident_broadcast_types() -> set[str]:
    """Liest AST-Dicts an manager.broadcast-Aufrufen auf dem Einsatz-Kanal.

    Die explizite Prüfung des ersten Arguments grenzt broadcast_lage und andere
    Offset-Kanaele aus. Jeder so gefundene Literal-Typ muss im zentralen Switch
    als case vorkommen; dadurch macht ein neuer Server-Typ ohne Client-Eintrag
    diesen Test rot.
    """
    result = set()
    for directory in (ROOT / "app/routers", ROOT / "app/services"):
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or len(node.args) < 2:
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr != "broadcast":
                    continue
                channel = ast.unparse(node.args[0])
                if channel not in {"incident_id", "incident.id", "iid"}:
                    continue
                payload = node.args[1]
                if not isinstance(payload, ast.Dict):
                    continue
                for key, value in zip(payload.keys, payload.values, strict=True):
                    if (
                        isinstance(key, ast.Constant) and key.value == "type"
                        and isinstance(value, ast.Constant) and isinstance(value.value, str)
                    ):
                        result.add(value.value)
    return result


def test_jeder_incident_broadcast_hat_einen_client_case():
    client_types = set(re.findall(r"case '([^']+)':", APP_JS.read_text()))
    assert _incident_broadcast_types() - client_types == set()


def _lage_broadcast_types() -> set[str]:
    """Alle literal Event-Typen, die über den GSL-Kanal gesendet werden."""
    result = set()
    for directory in (ROOT / "app/routers", ROOT / "app/services"):
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id != "broadcast_lage":
                    continue
                if len(node.args) < 2 or not isinstance(node.args[1], ast.Dict):
                    continue
                for key, value in zip(node.args[1].keys, node.args[1].values, strict=True):
                    if (isinstance(key, ast.Constant) and key.value == "type"
                            and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                        result.add(value.value)
    return result


def test_jeder_gsl_broadcast_hat_einen_lage_board_handler():
    """Neue GSL-Events dürfen nicht stillschweigend ohne Board-Reaktion bleiben."""
    client_types = set(re.findall(r"msg\.type === '([^']+)'", LAGE_JS.read_text()))
    # Das Verleih-Modul hat keinen Konsumenten auf dem Board.
    assert _lage_broadcast_types() - client_types - {"verleih:changed"} == set()


def test_gsl_panel_header_und_body_haben_getrennte_swap_ziele():
    """Ein Live-Refresh kann einen Edit-Modus erkennen, ohne den Body zu überschreiben."""
    site_detail = (ROOT / "app/templates/incident_major/_site_detail.html").read_text()
    marker_panel = (ROOT / "app/templates/incident_major/_cross_marker_panel.html").read_text()
    header = (ROOT / "app/templates/incident_major/_site_detail_header.html").read_text()
    assert 'id="siteDetailHeader"' in header
    assert 'id="siteDetailBody"' in site_detail
    assert 'data-open-cross-marker-id' in marker_panel
    assert "header.dataset.editing !== 'true'" in LAGE_JS.read_text()
