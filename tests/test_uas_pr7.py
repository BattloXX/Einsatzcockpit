"""PR 7: PDF-Export – Service-Logik (ohne WeasyPrint-Aufruf)."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_flug():
    return SimpleNamespace(
        id=1, datum=date(2026, 6, 20),
        pilot_id=1, device_id=1,
        durchfuehrung="vlos", grundlage="open_a1",
        bescheid_nr=None, geplante_flughoehe_m=50.0,
        contingency_volume_m=5.0, ground_risk_buffer_m=55.0,
        abstand_menschenansammlung_m=175.0, flughoehe_konform=True,
        nachtbetrieb=False, dauer_min=12, status="abgeschlossen",
    )


def _make_ereignis():
    return SimpleNamespace(
        id=1, typ="unfall", kategorie="Absturz",
        datum_lokal=date(2026, 6, 20), zeit_lokal="14:30",
        datum_utc=date(2026, 6, 20), zeit_utc="12:30",
        ort_icao="LOWI", koordinaten=None,
        klassifizierung="Unfall",
        beschreibung="Testhergang",
        massnahmen=None,
    )


def _make_checkliste():
    return SimpleNamespace(
        id=1, typ="vorflug",
        punkte=[{"text": "Akku laden", "erledigt": True}, {"text": "GPS prüfen", "erledigt": False}],
        erledigt_von_pilot="Max", erledigt_von_zweitperson="Anna",
        abgeschlossen_at=datetime(2026, 6, 20, 12, 0, 0),
    )


def _fake_render(html: str) -> bytes:
    return html.encode()


# ── Flugbuch HTML-Inhalte ─────────────────────────────────────────────────────

def test_flugbuch_pdf_html_inhalte():
    from app.services.uas_pdf import flugbuch_pdf

    flug = _make_flug()
    pilot = SimpleNamespace(vorname="Max", nachname="Muster")
    device = SimpleNamespace(bezeichnung="DJI Mavic 3")

    with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
        result = flugbuch_pdf(flug, pilot, device)

    html = result.decode()
    assert "Anh. 8.1" in html
    assert "Max Muster" in html
    assert "DJI Mavic 3" in html
    assert "50.0" in html


def test_flugbuch_pdf_ohne_pilot_device():
    from app.services.uas_pdf import flugbuch_pdf

    with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
        result = flugbuch_pdf(_make_flug(), pilot=None, device=None)

    html = result.decode()
    assert "Anh. 8.1" in html


# ── Checkliste ────────────────────────────────────────────────────────────────

def test_checkliste_pdf_punkte():
    from app.services.uas_pdf import checkliste_pdf

    with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
        result = checkliste_pdf(_make_checkliste(), flug_id=42)

    html = result.decode()
    assert "vorflug" in html
    assert "Akku laden" in html
    assert "GPS prüfen" in html
    assert "Max" in html
    assert "Anh. 8.2" in html


# ── Ereignis/Protokoll ────────────────────────────────────────────────────────

def test_ereignis_pdf_inhalte():
    from app.services.uas_pdf import ereignis_pdf

    with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
        result = ereignis_pdf(_make_ereignis())

    html = result.decode()
    assert "Anh. 8.3" in html
    assert "unfall" in html
    assert "Testhergang" in html
    assert "LOWI" in html


def test_acg_unfall_pdf_inhalte():
    from app.services.uas_pdf import acg_unfall_pdf

    with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
        result = acg_unfall_pdf(_make_ereignis())

    html = result.decode()
    assert "Anh. 8.4" in html
    assert "ACG" in html
    assert "LOWI" in html


# ── Wartungsbuch ──────────────────────────────────────────────────────────────

def test_wartungsbuch_pdf_leer():
    from app.services.uas_pdf import wartungsbuch_pdf

    device = SimpleNamespace(bezeichnung="DJI Air 3")
    with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
        result = wartungsbuch_pdf([], device)

    html = result.decode()
    assert "Anh. 8.5" in html
    assert "DJI Air 3" in html
    assert "Keine Einträge" in html


def test_wartungsbuch_pdf_eintraege():
    from app.services.uas_pdf import wartungsbuch_pdf

    w = SimpleNamespace(
        faellig_am=date(2026, 7, 1), typ="monatlich",
        durchgefuehrt_am=date(2026, 6, 30),
        ergebnis="OK", techniker="H. Muster",
    )
    device = SimpleNamespace(bezeichnung="DJI Air 3")
    with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
        result = wartungsbuch_pdf([w], device)

    html = result.decode()
    assert "H. Muster" in html
    assert "monatlich" in html


# ── Eintreffmeldung ───────────────────────────────────────────────────────────

def test_eintreffmeldung_pdf():
    from app.services.uas_pdf import eintreffmeldung_pdf

    einsatz = SimpleNamespace(
        id=5, status="im_einsatz",
        betreibernummer="AT-BOS-001", tetra_rufname="DROHNE-1",
        gesamteinsatzleiter="HBI Mustermann",
    )
    pilot = SimpleNamespace(
        vorname="Anna", nachname="Müller",
        bos_ausweisnummer="BOS-123", zertifikat_a2="A2-456",
    )
    with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
        result = eintreffmeldung_pdf(einsatz, [pilot])

    html = result.decode()
    assert "Anh. 8.6" in html
    assert "Anna" in html
    assert "DROHNE-1" in html
    assert "HBI Mustermann" in html


def test_eintreffmeldung_pdf_ohne_piloten():
    from app.services.uas_pdf import eintreffmeldung_pdf

    einsatz = SimpleNamespace(
        id=6, status="alarmiert",
        betreibernummer=None, tetra_rufname=None,
        gesamteinsatzleiter=None,
    )
    with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
        result = eintreffmeldung_pdf(einsatz, [])

    html = result.decode()
    assert "Keine Piloten" in html
