"""PDF-Export für UAS-Formulare via WeasyPrint (ÖBFV Anh. 8.1–8.5, RL-UAS LFV Vbg. Jan 2024)."""
from __future__ import annotations

import io
from typing import TYPE_CHECKING

from app.core.timezones import format_local_datetime

if TYPE_CHECKING:
    pass

_CSS_BASE = """
@page { margin: 1.5cm; font-size: 11pt; }
body { font-family: Arial, sans-serif; color: #111; }
h1 { font-size: 14pt; border-bottom: 2px solid #111; padding-bottom: 4px; margin-bottom: 8px; }
h2 { font-size: 12pt; margin: 12px 0 4px; }
table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
th { background: #e8e8e8; text-align: left; padding: 4px 6px; font-size: 10pt; }
td { padding: 4px 6px; border-bottom: 1px solid #ccc; font-size: 10pt; }
.label { font-weight: bold; width: 38%; }
.check { font-size: 10pt; }
.check-ok  { color: #166534; }
.check-nok { color: #991b1b; }
.footer { font-size: 8pt; color: #666; margin-top: 12px; }
"""


def _render_pdf(html: str) -> bytes:
    from weasyprint import CSS, HTML  # lazy import – optional dependency
    buf = io.BytesIO()
    HTML(string=html).write_pdf(buf, stylesheets=[CSS(string=_CSS_BASE)])
    return buf.getvalue()


# ── Anh. 8.1: Flugbuch-Seite ─────────────────────────────────────────────────

def flugbuch_pdf(flug, pilot=None, device=None) -> bytes:
    rows = [
        ("Datum", str(flug.datum or "")),
        ("Pilot", pilot.vorname + " " + pilot.nachname if pilot else "–"),
        ("Gerät", device.bezeichnung if device else "–"),
        ("Durchführung", flug.durchfuehrung or "–"),
        ("Grundlage", flug.grundlage or "–"),
        ("Bescheid-Nr.", flug.bescheid_nr or "–"),
        ("Höhe (m)", str(flug.geplante_flughoehe_m or "–")),
        ("Cont. Vol. (m)", str(flug.contingency_volume_m or "–")),
        ("GRB (m)", str(flug.ground_risk_buffer_m or "–")),
        ("Abstand (m)", str(flug.abstand_menschenansammlung_m or "–")),
        ("1:1-Regel konform", "Ja" if flug.flughoehe_konform else "Nein"),
        ("Nachtbetrieb", "Ja" if flug.nachtbetrieb else "Nein"),
        ("Dauer (min)", str(flug.dauer_min or "–")),
        ("Status", flug.status or "–"),
    ]
    body = "\n".join(
        f'<tr><td class="label">{k}</td><td>{v}</td></tr>' for k, v in rows
    )
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<h1>UAS-Flugbuch – Flug #{flug.id} (Anh. 8.1)</h1>
<table><tbody>{body}</tbody></table>
<p class="footer">Erstellt gem. RL-UAS LFV Vorarlberg Jan 2024 | Formular Anh. 8.1</p>
</body></html>"""
    return _render_pdf(html)


# ── Anh. 8.2: Checkliste Vor-/Nachflug ───────────────────────────────────────

def _parse_punkte(punkte_field) -> list:
    """Parst punkte aus JSON-String oder gibt direkt die Liste zurück."""
    import json as _j
    if isinstance(punkte_field, list):
        return punkte_field
    if isinstance(punkte_field, str) and punkte_field:
        try:
            return _j.loads(punkte_field)
        except Exception:
            pass
    return []


def checkliste_pdf(checkliste, flug_id: int | None = None, org=None) -> bytes:
    punkte = _parse_punkte(checkliste.punkte)
    rows = ""
    for p in punkte:
        ok = "&#x2713;" if p.get("erledigt") else "&#x2717;"
        css = "check-ok" if p.get("erledigt") else "check-nok"
        text = p.get("label") or p.get("text") or ""
        bem = p.get("bemerkung") or ""
        bem_str = f" <em style='color:#555'>({bem})</em>" if bem else ""
        rows += f'<tr><td class="check {css}">{ok}</td><td>{text}{bem_str}</td></tr>'
    if not rows:
        rows = '<tr><td colspan="2" style="color:#666">Keine Prüfpunkte erfasst.</td></tr>'
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<h1>Checkliste {checkliste.typ} – Flug #{flug_id} (Anh. 8.2)</h1>
<table><thead><tr><th width="30"></th><th>Pr&uuml;fpunkt</th></tr></thead>
<tbody>{rows}</tbody></table>
<p>Erledigt von (Pilot): {checkliste.erledigt_von_pilot or "–"}<br>
   Zweitperson: {checkliste.erledigt_von_zweitperson or "–"}<br>
   Abgeschlossen: {format_local_datetime(checkliste.abgeschlossen_at, org) or "–"}</p>
<p class="footer">Erstellt gem. RL-UAS LFV Vorarlberg Jan 2024 | Formular Anh. 8.2</p>
</body></html>"""
    return _render_pdf(html)


# ── Anh. 8.3: Notfallprotokoll / Ereignisbericht ─────────────────────────────

def ereignis_pdf(ereignis) -> bytes:
    rows = [
        ("Typ", ereignis.typ or "–"),
        ("Kategorie", ereignis.kategorie or "–"),
        ("Datum lokal", str(ereignis.datum_lokal or "–")),
        ("Uhrzeit lokal", ereignis.zeit_lokal or "–"),
        ("Datum UTC", str(ereignis.datum_utc or "–")),
        ("Uhrzeit UTC", ereignis.zeit_utc or "–"),
        ("Ort (ICAO)", ereignis.ort_icao or "–"),
        ("Klassifizierung", ereignis.klassifizierung or "–"),
    ]
    body = "\n".join(
        f'<tr><td class="label">{k}</td><td>{v}</td></tr>' for k, v in rows
    )
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<h1>Notfall-/Unfallprotokoll #{ereignis.id} (Anh. 8.3)</h1>
<table><tbody>{body}</tbody></table>
<h2>Hergang / Beschreibung</h2>
<p style="white-space:pre-wrap">{ereignis.beschreibung or "–"}</p>
<h2>Getroffene Maßnahmen</h2>
<p>{ereignis.massnahmen or "–"}</p>
<p class="footer">Erstellt gem. RL-UAS LFV Vorarlberg Jan 2024 | Formular Anh. 8.3</p>
</body></html>"""
    return _render_pdf(html)


# ── Anh. 8.4: ACG Unfall-Meldung ─────────────────────────────────────────────

def acg_unfall_pdf(ereignis) -> bytes:
    rows = [
        ("Datum lokal", str(ereignis.datum_lokal or "–")),
        ("Zeit lokal", ereignis.zeit_lokal or "–"),
        ("Datum UTC", str(ereignis.datum_utc or "–")),
        ("Zeit UTC", ereignis.zeit_utc or "–"),
        ("Ort (ICAO)", ereignis.ort_icao or "–"),
        ("Koordinaten", ereignis.koordinaten or "–"),
        ("Klassifizierung", ereignis.klassifizierung or "–"),
    ]
    body = "\n".join(
        f'<tr><td class="label">{k}</td><td>{v}</td></tr>' for k, v in rows
    )
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<h1>ACG Unfall-Meldung – Ereignis #{ereignis.id} (Anh. 8.4)</h1>
<table><tbody>{body}</tbody></table>
<h2>Beschreibung des Ereignisses</h2>
<p style="white-space:pre-wrap">{ereignis.beschreibung or "–"}</p>
<p class="footer">Erstellt gem. RL-UAS LFV Vorarlberg Jan 2024 | Formular Anh. 8.4<br>
An ACG (Austro Control GmbH) zu melden gem. Luftfahrtgesetz §147a.</p>
</body></html>"""
    return _render_pdf(html)


# ── Anh. 8.5: Wartungsbuch ───────────────────────────────────────────────────

def wartungsbuch_pdf(wartungen: list, device=None) -> bytes:
    # Feldnamen korrigiert (echter Bug): UASWartung hat weder faellig_am/typ/
    # durchgefuehrt_am/techniker -- die echten Spalten sind naechste_faellig/art/
    # datum/pruefer (siehe app/models/uas.py bzw. das analoge, korrekte Mapping in
    # app/templates/uas/geraet_detail.html). .replace('_',' ') matcht die dortige
    # Anzeige-Konvention fuer den art-Rohwert (z.B. "monatliche_sichtkontrolle").
    rows = ""
    for w in wartungen:
        art_label = (w.art or "–").replace("_", " ")
        rows += (
            f"<tr><td>{w.naechste_faellig or '–'}</td><td>{art_label}</td>"
            f"<td>{w.datum or '–'}</td><td>{w.ergebnis or '–'}</td>"
            f"<td>{w.pruefer or '–'}</td></tr>"
        )
    if not rows:
        rows = '<tr><td colspan="5" style="text-align:center">Keine Einträge</td></tr>'
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<h1>Wartungsbuch – {device.bezeichnung if device else "Gerät"} (Anh. 8.5)</h1>
<table>
<thead><tr><th>Fällig am</th><th>Typ</th><th>Durchgef.</th><th>Ergebnis</th><th>Techniker</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="footer">Erstellt gem. RL-UAS LFV Vorarlberg Jan 2024 | Formular Anh. 8.5</p>
</body></html>"""
    return _render_pdf(html)


# ── Gesamt-PDF: Drohneneinsatz komplett ──────────────────────────────────────

def einsatz_gesamt_pdf(einsatz, incident=None, rollen=None, fluege_daten=None, org=None) -> bytes:
    """Vollständiges Protokoll eines Drohneneinsatzes für den PDF-Druck."""
    import json as _j

    # Einsatz-Header
    inc_info = ""
    if incident:
        grund = (incident.reason or incident.report_text or "")[:120]
        adresse = ""
        if incident.address_street:
            adresse = f"{incident.address_street} {incident.address_no or ''}".strip()
            if incident.address_city:
                adresse += f", {incident.address_city}"
        if grund or adresse:
            inc_info = (
                f'<tr><td class="label">Einsatzgrund</td><td>{grund or "–"}</td></tr>'
                f'<tr><td class="label">Adresse</td><td>{adresse or "–"}</td></tr>'
            )

    einsatz_rows = [
        ("Status", einsatz.status or "–"),
        ("TETRA-Rufname", einsatz.tetra_rufname or "–"),
        ("Betreibernummer", einsatz.betreibernummer or "–"),
        ("Gesamteinsatzleiter", einsatz.gesamteinsatzleiter or "–"),
        ("Alarmierung", format_local_datetime(einsatz.alarmierung_at, org) or "–"),
        ("EL-Anmeldung", format_local_datetime(einsatz.anmeldung_el_at, org) or "–"),
        ("EL-Abmeldung", format_local_datetime(einsatz.abmeldung_el_at, org) or "–"),
        ("Datenschutz best.", "Ja" if einsatz.datenschutz_bestaetigt else "Nein"),
    ]
    if einsatz.einsatzgrund:
        einsatz_rows.append(("Einsatzgrund / Notizen", einsatz.einsatzgrund[:300]))
    einsatz_html = inc_info + "\n".join(
        f'<tr><td class="label">{k}</td><td>{v}</td></tr>' for k, v in einsatz_rows
    )

    # Team-Rollen
    rollen_rows = ""
    for r in (rollen or []):
        name = ""
        if getattr(r, "pilot", None):
            name = f"{r.pilot.vorname} {r.pilot.nachname}"
        else:
            name = r.helfer_name or "–"
        rollen_rows += f"<tr><td>{r.rolle.replace('_',' ').title()}</td><td>{name}</td></tr>"
    rollen_html = rollen_rows or '<tr><td colspan="2">Keine Rollen erfasst</td></tr>'

    # Kommunikationsmatrix
    komm_html = ""
    if einsatz.kommunikationsmatrix:
        try:
            komm = _j.loads(einsatz.kommunikationsmatrix)
            for k, v in komm.items():
                if v:
                    komm_html += f'<tr><td class="label">{k.replace("_"," ").title()}</td><td>{v}</td></tr>'
        except Exception:
            pass
    komm_html = komm_html or "<tr><td>Nicht erfasst</td></tr>"

    # Risikobewertung
    risiko_html = ""
    if einsatz.risikobewertung:
        try:
            risiko = _j.loads(einsatz.risikobewertung)
            labels = {
                "gelande": "Gelände / Hindernisse",
                "menschen": "Menschen / Bevölkerung",
                "luftraum": "Luftraum / bemannte Luftfahrt",
                "wetter": "Wetter / Wind / Sicht",
                "sonstiges": "Sonstiges",
                "gesamt": "Gesamtrisiko",
            }
            for k, label in labels.items():
                if risiko.get(k):
                    risiko_html += f'<tr><td class="label">{label}</td><td>{risiko[k]}</td></tr>'
        except Exception:
            pass
    risiko_html = risiko_html or "<tr><td>Nicht erfasst</td></tr>"

    # Flüge
    fluege_html = ""
    for fd in (fluege_daten or []):
        flug = fd["flug"]
        pilot = fd.get("pilot")
        device = fd.get("device")
        checklisten = fd.get("checklisten", [])

        flug_rows = [
            ("Datum", str(flug.datum or "–")),
            ("Pilot", f"{pilot.vorname} {pilot.nachname}" if pilot else "–"),
            ("Gerät", device.bezeichnung if device else "–"),
            ("Durchführung", flug.durchfuehrung or "–"),
            ("Grundlage", flug.grundlage or "–"),
            ("Höhe geplant (m)", str(flug.geplante_flughoehe_m or "–")),
            ("GRB (m)", str(flug.ground_risk_buffer_m or "–")),
            ("Contingency Vol. (m)", str(flug.contingency_volume_m or "–")),
            ("Nachtbetrieb", "Ja" if flug.nachtbetrieb else "Nein"),
            ("Startort", flug.start_ort or "–"),
            ("Landungsort", flug.landung_ort or "–"),
            ("Start-Zeit", format_local_datetime(flug.start_at, org) or "–"),
            ("Landungs-Zeit", format_local_datetime(flug.landung_at, org) or "–"),
            ("Dauer (min)", str(flug.dauer_min or "–")),
            ("Gesamteinsatzleiter", flug.gesamteinsatzleiter or "–"),
            ("EL Drohne", flug.einsatzleiter_drohne or "–"),
            ("Status", flug.status or "–"),
        ]
        flug_table = "\n".join(
            f'<tr><td class="label">{k}</td><td>{v}</td></tr>' for k, v in flug_rows
        )

        cl_html = ""
        for cl_data in checklisten:
            cl = cl_data["checkliste"]
            punkte = cl_data.get("punkte") or []
            cl_rows = ""
            for p in punkte:
                ok = "&#x2713;" if p.get("erledigt") else "&#x2717;"
                css = "check-ok" if p.get("erledigt") else "check-nok"
                text = p.get("label") or p.get("text") or ""
                bem = p.get("bemerkung") or ""
                cl_rows += (
                    f'<tr><td class="check {css}" width="22">{ok}</td>'
                    f'<td>{text}'
                    + (f' <em style="color:#555">({bem})</em>' if bem else "")
                    + "</td></tr>"
                )
            cl_html += (
                f"<h3 style='margin-top:8px'>Checkliste {cl.typ.title()}</h3>"
                f"<table><thead><tr><th width='22'></th><th>Prüfpunkt</th></tr></thead>"
                f"<tbody>{cl_rows or '<tr><td colspan=2>Keine Einträge</td></tr>'}</tbody></table>"
                f"<p style='font-size:9pt'>Pilot: {cl.erledigt_von_pilot or '–'} | "
                f"Zweitperson: {cl.erledigt_von_zweitperson or '–'} | "
                f"Abgeschlossen: {format_local_datetime(cl.abgeschlossen_at, org) or '–'}</p>"
            )

        fluege_html += (
            f"<h2 style='border-top:1px solid #999;padding-top:6px;margin-top:12px'>"
            f"Flug #{flug.lfd_nr} (ID {flug.id})</h2>"
            f"<table><tbody>{flug_table}</tbody></table>"
            f"{cl_html}"
        )

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<h1>Drohneneinsatz #{einsatz.id} &ndash; Gesamtprotokoll (RL-UAS LFV Vbg.)</h1>
<h2>Einsatzdaten</h2>
<table><tbody>{einsatz_html}</tbody></table>
<h2>Team-Rollen (RL 5.2&ndash;5.7)</h2>
<table>
<thead><tr><th>Rolle</th><th>Person</th></tr></thead>
<tbody>{rollen_html}</tbody>
</table>
<h2>Kommunikationsmatrix (RL 4.5 / 7.4)</h2>
<table><tbody>{komm_html}</tbody></table>
<h2>Risikobewertung (RL 4.11 / 6.2)</h2>
<table><tbody>{risiko_html}</tbody></table>
{fluege_html or "<p>Keine Fl&uuml;ge erfasst.</p>"}
<p class="footer">Erstellt gem. RL-UAS LFV Vorarlberg Jan 2024 | Gesamtprotokoll Anh. 8.1&ndash;8.2</p>
</body></html>"""
    return _render_pdf(html)


# ── Anh. 8.6: Eintreffmeldung / Pilotenausweis ───────────────────────────────

def eintreffmeldung_pdf(einsatz, piloten: list) -> bytes:
    rows = ""
    for p in piloten:
        rows += (
            f"<tr><td>{p.vorname} {p.nachname}</td><td>{p.bos_ausweisnummer or '–'}</td>"
            f"<td>{p.zertifikat_a2 or '–'}</td></tr>"
        )
    if not rows:
        rows = '<tr><td colspan="3" style="text-align:center">Keine Piloten</td></tr>'
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<h1>Eintreffmeldung UAS-Einheit (Anh. 8.6)</h1>
<table><tbody>
<tr><td class="label">Einsatz-ID</td><td>#{einsatz.id}</td></tr>
<tr><td class="label">Status</td><td>{einsatz.status or "–"}</td></tr>
<tr><td class="label">Betreiber-Nr.</td><td>{einsatz.betreibernummer or "–"}</td></tr>
<tr><td class="label">TETRA-Rufname</td><td>{einsatz.tetra_rufname or "–"}</td></tr>
<tr><td class="label">Gesamteinsatzleiter</td><td>{einsatz.gesamteinsatzleiter or "–"}</td></tr>
</tbody></table>
<h2>Eingesetzte Piloten</h2>
<table>
<thead><tr><th>Name</th><th>BOS-Ausweis</th><th>A2-Zertifikat</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="footer">Erstellt gem. RL-UAS LFV Vorarlberg Jan 2024 | Formular Anh. 8.6</p>
</body></html>"""
    return _render_pdf(html)
