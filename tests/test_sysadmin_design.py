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
