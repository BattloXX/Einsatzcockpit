"""Vertrag des lokal vendorten Icon-Systems."""

import json
import logging
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from markupsafe import Markup

from app.config import settings
from app.core.icons import GROESSEN, MODUL_ICONS, icon_namen, modul_icon, render_icon, status_dot
from app.core.templating import templates

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"


def selection():
    return {
        line.strip()
        for line in (STATIC / "icons/tabler-auswahl.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def test_vendored_icons():
    files = list((STATIC / "icons/tabler").glob("*.svg"))
    assert {path.stem for path in files} == selection() == icon_namen()
    for path in files:
        svg = ET.fromstring(path.read_text())
        assert svg.attrib["viewBox"] == "0 0 24 24"
        assert len(svg) > 0
        rendered = ET.fromstring(str(render_icon(path.stem)))
        assert len(rendered) > 0
        assert 'd="M0 0h24v24H0z"' not in str(render_icon(path.stem))


def test_render_defaults_and_sizes():
    result = render_icon("printer")
    assert isinstance(result, Markup)
    svg = ET.fromstring(str(result))
    assert svg.attrib["class"] == "ecp-icon ecp-icon--md"
    assert svg.attrib["aria-hidden"] == "true"
    assert svg.attrib["focusable"] == "false"
    for size, pixels in GROESSEN.items():
        svg = ET.fromstring(str(render_icon("printer", size, '  extra   <script>"  ')))
        assert svg.attrib["width"] == svg.attrib["height"] == str(pixels)
        assert svg.attrib["class"] == f'ecp-icon ecp-icon--{size} extra <script>"'
    with pytest.raises(ValueError):
        render_icon("printer", "huge")


def test_label_escaping():
    label = '<script>"&\'</script>'
    result = str(render_icon("printer", label=label))
    assert "<script>" not in result
    svg = ET.fromstring(result)
    assert "aria-hidden" not in svg.attrib
    assert svg.attrib["role"] == "img"
    assert svg.attrib["aria-label"] == label
    assert svg[0].tag == "title" and svg[0].text == label


def test_unknown_name(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ICONS_STRICT", True)
    with pytest.raises(KeyError):
        render_icon("unknown-p1-test")
    monkeypatch.setattr(settings, "ICONS_STRICT", False)
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.icons"):
        for _ in range(2):
            assert render_icon("unknown-p1-test") == render_icon("help-square")
    assert len(caplog.records) == 1
    assert "unknown-p1-test" in caplog.text


@pytest.mark.parametrize("status", ["rot", "orange", "gelb", "gruen", "blau", "grau", "schwarz", "weiss"])
def test_status_dot(status):
    label = '<script>"&'
    result = str(status_dot(status, label, ' extra " '))
    span = ET.fromstring(result)
    assert span.attrib["class"] == f'status-dot status-dot--{status} extra "'
    assert span.attrib["role"] == "img"
    assert span.attrib["aria-label"] == span.attrib["title"] == label
    assert "<script>" not in result


def test_status_dot_invalid():
    with pytest.raises(ValueError):
        status_dot("purple", "Unbekannt")


def test_jinja_globals():
    assert "ecp-icon--sm" in templates.env.from_string('{{ icon("printer", size="sm") }}').render()
    assert "status-dot--rot" in templates.env.from_string('{{ status_dot("rot", "Stop") }}').render()


@pytest.mark.parametrize("status,farbe,label", [
    ("gefunden", "rot", "Gefunden"),
    ("versorgt", "orange", "Versorgt"),
    ("abtransportiert", "gruen", "Abtransportiert"),
    ("verstorben", "schwarz", "Verstorben"),
    ('<unbekannt>&"', "grau", '<unbekannt>&"'),
])
def test_person_status_filter(status, farbe, label):
    assert templates.env.filters["person_status_label"](status) == label
    rendered = templates.env.from_string('{{ status|person_status_dot }}').render(status=status)
    dot = ET.fromstring(rendered)
    assert dot.attrib["class"] == f"status-dot status-dot--{farbe}"
    assert dot.attrib["aria-label"] == dot.attrib["title"] == label
    assert "<unbekannt>" not in rendered


def test_literal_icon_calls():
    # bis P8: Diese Templates importieren das alte Makro mit eigenen Namen.
    legacy = {
        "public/ueber.html", "public/start.html", "public/_pub_nav.html",
        "public/roadmap.html", "public/_pub_footer.html", "public/_macros.html",
    }
    template_dir = ROOT / "app/templates"
    calls = []
    for path in template_dir.rglob("*.html"):
        if path.relative_to(template_dir).as_posix() not in legacy:
            calls.extend(re.findall(r'''\bicon\(\s*["']([^"']+)["']''', path.read_text()))
    for path in (STATIC / "js").glob("*.js"):
        if path.name != "icons.js":
            calls.extend(re.findall(r'''\becp(?:Marker)?Icon\(\s*["']([^"']+)["']''', path.read_text()))
    assert set(calls) <= icon_namen()


def test_generated_js_names():
    source = (STATIC / "js/icons.js").read_text()
    data = json.loads(source.split("const icons = ", 1)[1].split(";\n", 1)[0])
    assert set(data) == selection()
    assert list(data) == sorted(data)


def test_modul_icons():
    assert set(MODUL_ICONS.values()) <= icon_namen()
    for key, name in MODUL_ICONS.items():
        assert modul_icon(key) == render_icon(name)
    assert modul_icon("profil", "lg", "extra", "Profil") == render_icon("user", "lg", "extra", "Profil")
    with pytest.raises(KeyError):
        modul_icon("unbekannt")
    assert templates.env.globals["modul_icon"] is modul_icon


@pytest.mark.parametrize("template", [
    "base.html", "index.html", "admin/_layout.html", "admin/index.html",
    "_profil_aktionen.html", "errors/fehler.html", "hilfe.html",
])
def test_rahmen_ohne_emoji_auch_in_html_entities(template):
    from html import unescape

    from scripts.icon_inventar import EMOJI_RE, ZUORDNUNG

    source = unescape((ROOT / "app/templates" / template).read_text())
    remaining = {ch for ch in EMOJI_RE.findall(source) if ZUORDNUNG.get(ch) != "=text"}
    assert remaining == set()
