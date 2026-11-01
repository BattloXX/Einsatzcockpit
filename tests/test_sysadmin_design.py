"""Waechter: Sysadmin-Templates bleiben im einheitlichen sys-Design."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
SEITEN = [
    "admin/systemdiagnose/index.html",
    "admin/systemdiagnose/_panel.html",
    "admin/systemdiagnose/_geraete.html",
    "admin/systemdiagnose/_geraet.html",
    "admin/systemdiagnose/_finding.html",
    "admin/systemdiagnose/_settings.html",
    "admin/system_settings.html",
    "admin/systemstatus.html",
    "admin/_systemstatus_liste.html",
    "admin/server_log.html",
    "admin/system_update.html",
]


def test_sysadmin_seiten_ohne_smart_quotes_in_attributen():
    for seite in SEITEN:
        text = (TEMPLATES / seite).read_text(encoding="utf-8")
        for tag in re.findall(r"<[a-zA-Z][^>]*>", text):
            assert not re.search(r"[“”„‘’]", tag), (seite, tag[:80])


def test_sysadmin_seiten_ohne_statische_inline_styles():
    for seite in SEITEN:
        text = (TEMPLATES / seite).read_text(encoding="utf-8")
        for stil in re.findall(r'style="([^"]*)"', text):
            if "{{" in stil:
                continue  # dynamische Werte erlaubt
            raise AssertionError((seite, stil))


def test_sysadmin_css_eingebunden():
    for seite in ("admin/systemdiagnose/index.html", "admin/system_settings.html", "admin/systemstatus.html"):
        assert "/static/css/sysadmin.css" in (TEMPLATES / seite).read_text(encoding="utf-8"), seite


def _admin_layout_seiten() -> list[Path]:
    return sorted(
        pfad
        for pfad in TEMPLATES.rglob("*.html")
        if 'extends "admin/_layout.html"' in pfad.read_text(encoding="utf-8")
    )


def test_alle_admin_seiten_ohne_statische_inline_styles():
    """Verwaltungsseiten nutzen Klassen aus sysadmin.css statt Inline-Styles.

    Erlaubt bleibt nur ``display:none`` (Bestands-JS fragt style.display ab)
    und dynamische Werte mit Jinja-Ausdruck.
    """
    seiten = _admin_layout_seiten()
    assert len(seiten) > 60
    for pfad in seiten:
        text = pfad.read_text(encoding="utf-8")
        for stil in re.findall(r'style="([^"]*)"', text):
            if "{{" in stil or stil.replace(" ", "").rstrip(";") == "display:none":
                continue
            raise AssertionError((str(pfad.relative_to(TEMPLATES)), stil))


def test_alle_admin_seiten_binden_sysadmin_css_ein():
    for pfad in _admin_layout_seiten():
        text = pfad.read_text(encoding="utf-8")
        assert "/static/css/sysadmin.css" in text, str(pfad.relative_to(TEMPLATES))
