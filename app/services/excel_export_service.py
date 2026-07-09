"""Excel-Export des Fahrtenbuchs (openpyxl)."""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from app.core.timezones import format_local_datetime
from app.models.fahrtenbuch import Fahrt


def _val(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Ja" if v else "Nein"
    if isinstance(v, datetime):
        return v.strftime("%d.%m.%Y %H:%M")
    return str(v)


def exportiere_fahrten(fahrten: list[Fahrt], org=None) -> bytes:
    """Erstellt eine Excel-Datei mit allen übergebenen Fahrten.

    Gibt die rohen Bytes zurück (geeignet für StreamingResponse).
    """
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        raise RuntimeError("openpyxl ist nicht installiert. `pip install openpyxl` ausführen.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fahrtenbuch"

    HEADER_FILL = PatternFill("solid", fgColor="2D2D2D")
    HEADER_FONT = Font(bold=True, color="FFFFFF")

    headers = [
        "Datum/Zeit", "Fahrzeug", "Kennzeichen",
        "Maschinist", "2. Maschinist",
        "km-Stand", "gefahrene km", "BH-Stand", "BH-Delta",
        "Seilwinde-BH", "Seilwinde-Delta",
        "Zielort", "Zweck", "Zweck-Freitext", "Fahrttyp", "Einsatz-Nr",
        "Ausbildner", "Gruppenkommandant", "Einsatzleiter",
        "Schaden", "betriebsfähig", "Schadenbeschreibung",
        "statistikrelevant", "Status",
        "erfasst via", "erfasst von", "Bemerkung",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
    ws.row_dimensions[1].height = 18

    for f in fahrten:
        fz = f.fahrzeug
        zweck = f.zweck
        zielort_text = (f.zielort.name if f.zielort else None) or f.zielort_freitext or ""
        row = [
            format_local_datetime(f.zeitpunkt, org) if f.zeitpunkt else "",
            fz.code if fz else "",
            fz.kennzeichen if fz and fz.kennzeichen else "",
            f.maschinist_name or "",
            f.maschinist2_name or "",
            f.km_stand_neu if f.km_stand_neu is not None else "",
            f.km_delta if f.km_delta is not None else "",
            str(f.betriebsstunden_neu) if f.betriebsstunden_neu is not None else "",
            str(f.betriebsstunden_delta) if f.betriebsstunden_delta is not None else "",
            str(f.seilwinde_bh_neu) if f.seilwinde_bh_neu is not None else "",
            str(f.seilwinde_bh_delta) if f.seilwinde_bh_delta is not None else "",
            zielort_text,
            zweck.name if zweck else "",
            f.zweck_freitext or "",
            f.fahrttyp.label if f.fahrttyp else "",
            str(f.incident_id) if f.incident_id else "",
            f.ausbildner_name or "",
            f.gruppenkommandant_name or "",
            f.einsatzleiter_name or "",
            "Ja" if f.schaden_vorhanden else "Nein",
            ("Ja" if f.schaden_betriebsfaehig else "Nein") if f.schaden_vorhanden else "",
            f.schaden_beschreibung or "",
            "Nein" if f.nicht_statistikrelevant else "Ja",
            f.status.value if f.status else "",
            f.erfasst_via.value if f.erfasst_via else "",
            f.token_label or "",
            f.bemerkung or "",
        ]
        ws.append(row)

    # Spaltenbreiten
    COL_WIDTHS = [16, 12, 12, 22, 22, 10, 12, 10, 10, 12, 14, 22, 22, 24, 10, 12,
                  22, 22, 22, 8, 12, 30, 14, 12, 12, 22, 30]
    for i, w in enumerate(COL_WIDTHS, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def exportiere_fahrzeug_links(fahrzeuge: list, org_token: str, base_url: str) -> bytes:
    """Excel mit allen Fahrzeugen inkl. direktem Fahrtenbuch-Link (QR-Deep-Link).

    Der Link öffnet das Erfassungsformular mit vorausgewähltem Fahrzeug:
    {base_url}/f/{org_token}/v/{qr_token}. Fahrzeuge ohne QR-Token erhalten
    keinen Link (Spalte bleibt leer).
    """
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        raise RuntimeError("openpyxl ist nicht installiert. `pip install openpyxl` ausführen.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fahrzeuge"

    HEADER_FILL = PatternFill("solid", fgColor="2D2D2D")
    HEADER_FONT = Font(bold=True, color="FFFFFF")

    headers = ["Fahrzeug", "Name", "Kennzeichen", "Typ", "Fahrtenbuch-Link"]
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
    ws.row_dimensions[1].height = 18

    base = (base_url or "").rstrip("/")
    for fz in fahrzeuge:
        link = f"{base}/f/{org_token}/v/{fz.qr_token}" if fz.qr_token else ""
        row_idx = ws.max_row + 1
        ws.append([fz.code or "", fz.name or "", fz.kennzeichen or "", fz.type or "", link])
        if link:
            cell = ws.cell(row=row_idx, column=5)
            cell.hyperlink = link
            cell.style = "Hyperlink"

    for i, w in enumerate([16, 26, 14, 20, 70], 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
