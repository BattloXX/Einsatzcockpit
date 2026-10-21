"""P9: System-Icons im PDF, geschuetzte Fachzeichen und xhtml2pdf-Fallback."""
import html
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.core.icons import render_icon
from app.core.templating import templates
from app.core.timezones import format_local_datetime
from app.models.incident import Task, TaskMedia
from app.services.pdf_service import strip_font_face_for_xhtml2pdf, strip_icons_for_xhtml2pdf
from scripts.icon_inventar import EMOJI_RE, ZUORDNUNG
from tests.test_druck_route import db, incident  # noqa: F401


UI_EMOJIS = {ch for ch, name in ZUORDNUNG.items() if name not in ("=fw", "=text")}
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="


def _context(incident):
    incident.is_exercise = True
    incident.tasks = [Task(title="Medien", media=[
        TaskMedia(incident_id=incident.id, kind=kind, original_filename=f"Medium-{kind}",
                  storage_path="test", mime_type="application/octet-stream")
        for kind in ("pdf", "datei", "video")
    ])]
    objekt = NS(
        anzeige_nummer="1", name="Testobjekt", status="bestaetigt", gefahren=[],
        bma=NS(bma_nummer="123"), kontakte=[NS(zentraler_kontakt=None)],
        merkmale=[NS(merkmal=NS(icon="", name="Testmerkmal"), hinweis="")],
        wohnanlage=NS(wohneinheiten=4, geschosse=2, stiegen=1),
        zusatzadressen=[NS(bezeichnung="Zugang", strasse="Teststrasse")],
        anfahrtsweg="Zufahrt", informationen="Information", adresse_zeile="Teststrasse 1",
    )
    return dict(
        incident=incident, objekt=objekt, now=datetime.now(UTC), org=None,
        karte_datauri=PNG, qr_datauri=None, symbol_legende=[], gefahr_piktogramme={},
        kontakt_arten={}, mit_kontakten=True, mit_hinweisen=True,
        teilnahmen=[], journal=[], objekte=[], extra_vehicles=[], fahrten_details={},
        vehicles_positions=[], zeichen_legende=[("test", "Testzeichen")], events=[],
        format_local_datetime=format_local_datetime, event_label=lambda e: e.event_typ,
        media_exists=lambda m: False, media_b64=lambda m: "", base_url="",
    )


@pytest.mark.parametrize("template", ["objektblatt", "incident_report", "lagebericht"])
def test_pdf_render_ohne_ui_emojis(template, incident):
    """Echte zentrale Environment und WeasyPrint; kein Fallback kann Fehler verdecken."""
    rendered = templates.env.get_template(f"pdf/{template}.html").render(**_context(incident))
    assert 'class="ecp-icon ' in rendered
    # Font-Partial enthaelt Emoji-Namen nur im Jinja-Kommentar, nicht im HTML.
    assert not UI_EMOJIS.intersection(html.unescape(rendered))
    weasyprint = pytest.importorskip("weasyprint")
    pdf = weasyprint.HTML(string=rendered).write_pdf()
    assert pdf.startswith(b"%PDF-")
    try:
        from pypdf import PdfReader
    except ImportError:
        return
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    expected = "Testobjekt" if template == "objektblatt" else "Einsatz"
    assert expected in text
    assert not UI_EMOJIS.intersection(text)
    assert ".notdef" not in text
    assert "\ufffd" not in text


def test_objektblatt_behaelt_fachzeichen_und_db_icons(incident):
    ctx = _context(incident)
    ctx["objekt"].gefahren = [NS(gefahr=NS(piktogramm_typ="test", name="Gefahr"))]
    ctx["gefahr_piktogramme"] = {"test": "⚡ Gefahr"}
    ctx["objekt"].merkmale[0].merkmal.icon = "🔥"
    rendered = templates.env.get_template("pdf/objektblatt.html").render(**ctx)
    assert "⚡" in rendered
    assert "🔥" in rendered
    assert "@font-face" in rendered
    assert "Noto Emoji" in rendered


@pytest.mark.parametrize("quote", ['"', "'"])
def test_strip_icons_nur_systemklasse(quote):
    icon = f"<svg\nwidth='10' class={quote}extra ecp-icon ecp-icon--xs{quote}><path/>\n</svg>"
    fachzeichen = '<svg class="taktisch"><path/></svg>'
    aehnlich = '<svg class="ecp-icon-extra"><path/></svg><svg data-class="ecp-icon"><path/></svg>'
    source = "vor" + icon + fachzeichen + icon + aehnlich + "nach"
    assert strip_icons_for_xhtml2pdf(source) == "vor" + fachzeichen + aehnlich + "nach"


def test_xhtml2pdf_fallback_render_mit_systemicons():
    pisa = pytest.importorskip("xhtml2pdf.pisa")
    source = '<style>@font-face {font-family:test;src:url(data:font/ttf;base64,INVALID);}'
    source += templates.env.get_template("pdf/_icon_styles.html").render() + '</style><p>Inhalt '
    source += str(render_icon("printer", size="xs")) + '</p>'
    cleaned = strip_font_face_for_xhtml2pdf(source)
    assert "<svg" not in cleaned
    assert "@font-face" not in cleaned
    result = io.BytesIO()
    assert not pisa.CreatePDF(io.StringIO(cleaned), dest=result).err
    assert result.getvalue().startswith(b"%PDF-")


def test_pdf_und_druck_quellen_ohne_hart_codierte_ui_emojis():
    paths = list(Path("app/templates/pdf").glob("*.html"))
    paths += list(Path("app/templates/incident_major").glob("*druck*.html"))
    paths += [Path(f"app/templates/{p}") for p in (
        "incident/lagefuehrung_druck.html", "termin/druck.html", "kontakte/druckausgabe.html",
    )]
    for path in paths:
        if path.name == "_emoji_font_face.html":
            continue
        source = re.sub(r"{#.*?#}", "", path.read_text(), flags=re.DOTALL)
        # GEFAHR_PIKTOGRAMME und seine unveraenderten Fallbacks gehoeren zu P10.
        source = re.sub(r"{{ gefahr_piktogramme\.get.*?}}", "", source)
        assert not UI_EMOJIS.intersection(EMOJI_RE.findall(html.unescape(source))), path


def test_incident_service_xhtml2pdf_fallback(incident, monkeypatch):
    weasyprint = pytest.importorskip("weasyprint")
    pisa = pytest.importorskip("xhtml2pdf.pisa")
    from app.services import pdf_service

    _context(incident)
    monkeypatch.setattr(pdf_service, "_load_pdf_context", lambda i: (None, [], [], []))
    monkeypatch.setattr(pdf_service, "load_fahrtenbuch_report", lambda *a: ({}, []))

    def unavailable(*args, **kwargs):
        raise OSError("WeasyPrint fehlt im Fallback-Test")

    monkeypatch.setattr(weasyprint, "HTML", unavailable)
    original = pisa.CreatePDF
    inputs = []

    def capture(source, **kwargs):
        rendered = source.read()
        inputs.append(rendered)
        return original(io.StringIO(rendered), **kwargs)

    monkeypatch.setattr(pisa, "CreatePDF", capture)
    pdf = pdf_service.render_incident_pdf(incident)
    assert pdf.startswith(b"%PDF-")
    assert len(inputs) == 1
    assert "<svg" not in inputs[0]
    assert "@font-face" not in inputs[0]
    assert "Medium-pdf" in inputs[0]
