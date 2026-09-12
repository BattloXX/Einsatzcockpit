"""Export helpers for the central contacts interchange format (v1)."""
from __future__ import annotations

import csv
import io

from sqlalchemy.orm import Session, selectinload

from app.models.kontakt import Kontakt
from app.models.objekt import Objekt, ObjektKontakt

FORMAT_VERSION = "1"


def _kontakte(db: Session, org_id: int) -> list[Kontakt]:
    return db.query(Kontakt).options(selectinload(Kontakt.telefone)).filter(
        Kontakt.org_id == org_id, Kontakt.archiviert.is_(False)
    ).order_by(Kontakt.anzeigename, Kontakt.id).all()


def export_csv(db: Session, org_id: int) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["version", "id", "typ", "anzeigename", "organisation", "funktion", "email", "erreichbarkeit"])
    for kontakt in _kontakte(db, org_id):
        writer.writerow([FORMAT_VERSION, kontakt.id, kontakt.typ, kontakt.anzeigename, kontakt.organisation or "",
                         kontakt.funktion or "", kontakt.email or "", kontakt.erreichbarkeit or ""])
    return out.getvalue().encode("utf-8-sig")


def export_xlsx(db: Session, org_id: int) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    kontakte = _kontakte(db, org_id)
    sheet = wb.active
    sheet.title = "Kontakte"
    sheet.append([
        "version", "id", "typ", "anzeigename", "vorname", "nachname", "funktion",
        "organisation", "email", "erreichbarkeit", "notizen",
    ])
    for k in kontakte:
        sheet.append([FORMAT_VERSION, k.id, k.typ, k.anzeigename, k.vorname, k.nachname, k.funktion,
                      k.organisation, k.email, k.erreichbarkeit, k.notizen])
    phones = wb.create_sheet("Telefonnummern")
    phones.append(["kontakt_id", "nummer", "label", "sort", "bevorzugt", "sms_eignung"])
    for k in kontakte:
        for p in k.telefone:
            phones.append([k.id, p.nummer, p.label, p.sort, p.bevorzugt, p.sms_eignung])
    mappings = wb.create_sheet("Objektzuordnungen")
    mappings.append(["kontakt_id", "objekt_id", "objekt", "rolle", "sort", "erreichbarkeit"])
    for row, objekt_name in db.query(ObjektKontakt, Objekt.name).join(Objekt).filter(
        ObjektKontakt.org_id == org_id, ObjektKontakt.kontakt_id.is_not(None)
    ).order_by(ObjektKontakt.objekt_id, ObjektKontakt.sort).all():
        mappings.append([row.kontakt_id, row.objekt_id, objekt_name, row.art, row.sort, row.erreichbarkeit])
    guide = wb.create_sheet("Anleitung")
    guide.append(["Format", f"Kontakt-Import/Export v{FORMAT_VERSION}"])
    guide.append(["Hinweis", "IDs nur zum Aktualisieren vorhandener Kontakte verwenden."])
    guide.append(["Hinweis", "Freigaben werden nicht exportiert und nie durch einen Import uebernommen."])
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        for column in ws.columns:
            width = min(max(len(str(cell.value or "")) for cell in column) + 2, 48)
            ws.column_dimensions[column[0].column_letter].width = width
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
