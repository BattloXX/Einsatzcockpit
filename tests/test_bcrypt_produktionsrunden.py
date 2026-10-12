"""Die Testsuite senkt die bcrypt-Runden – Produktion muss bei 12 bleiben."""
import ast
from pathlib import Path


def test_produktionswert_im_quelltext_ist_12():
    quelle = (Path(__file__).resolve().parent.parent / "app" / "core" / "security.py").read_text(encoding="utf-8")
    werte = [
        node.value.value for node in ast.walk(ast.parse(quelle))
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "BCRYPT_ROUNDS" for t in node.targets)
    ]
    assert werte == [12]


def test_hash_nutzt_konfigurierte_runden_und_bleibt_pruefbar():
    from app.core import security

    h = security.hash_password("geheim")
    assert h.startswith(f"$2b${security.BCRYPT_ROUNDS:02d}$")
    assert security.verify_password("geheim", h) and not security.verify_password("falsch", h)
