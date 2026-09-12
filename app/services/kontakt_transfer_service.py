"""Export helpers for the central contacts interchange format (v1)."""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models.kontakt import Kontakt, KontaktImportVorschau
from app.models.objekt import Objekt, ObjektKontakt
from app.services import kontakt_service

FORMAT_VERSION = "1"


def parse_import(content: bytes, filename: str) -> list[dict[str, Any]]:
    """Read the v1 CSV or XLSX contact sheet into normalized import rows."""
    if filename.lower().endswith(".csv"):
        text = content.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    elif filename.lower().endswith(".xlsx"):
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        if "Kontakte" not in workbook.sheetnames:
            raise ValueError("XLSX braucht ein Blatt 'Kontakte'")
        sheet = workbook["Kontakte"]
        values = list(sheet.values)
        if not values:
            return []
        headers = [str(value or "").strip() for value in values[0]]
        rows = [dict(zip(headers, values_, strict=False)) for values_ in values[1:]]
    else:
        raise ValueError("Bitte eine CSV- oder XLSX-Datei hochladen")
    if len(rows) > 1000:
        raise ValueError("Hoechstens 1000 Kontakte pro Import")
    return [{key: str(value or "").strip() for key, value in row.items()} for row in rows]


def preview_import(db: Session, org_id: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify rows without changing data; all candidate queries stay in-org."""
    preview = []
    for row in rows:
        name = row.get("anzeigename", "").strip()
        if not name:
            preview.append({"status": "fehler", "row": row, "message": "Anzeigename fehlt"})
            continue
        raw_id = row.get("id", "")
        kontakt = None
        if raw_id.isdigit():
            kontakt = db.query(Kontakt).filter(Kontakt.org_id == org_id, Kontakt.id == raw_id).first()
        if raw_id and kontakt is None:
            preview.append({"status": "fehler", "row": row, "message": "Kontakt-ID gehoert nicht zur Organisation"})
        elif kontakt is not None:
            fields = ("typ", "anzeigename", "organisation", "funktion", "email", "erreichbarkeit")
            changed = any((getattr(kontakt, field) or "") != row.get(field, "") for field in fields)
            preview.append({"status": "geaendert" if changed else "unveraendert", "row": row, "kontakt_id": kontakt.id})
        else:
            duplicates = kontakt_service.find_duplicate_candidates(
                db, anzeigename=name, organisation=row.get("organisation"), email=row.get("email"),
            )
            preview.append({"status": "dublette" if duplicates else "neu", "row": row,
                            "kandidaten": [candidate.id for candidate in duplicates]})
    return preview


def save_preview(
    db: Session, org_id: int, user_id: int, preview: list[dict[str, Any]],
) -> KontaktImportVorschau:
    entry = KontaktImportVorschau(org_id=org_id, user_id=user_id, zeilen_json=json.dumps(preview))
    db.add(entry)
    db.commit()
    return entry


def load_preview(
    db: Session, org_id: int, user_id: int, preview_id: int,
) -> tuple[KontaktImportVorschau, list[dict[str, Any]]]:
    entry = db.query(KontaktImportVorschau).filter(
        KontaktImportVorschau.id == preview_id, KontaktImportVorschau.org_id == org_id,
        KontaktImportVorschau.user_id == user_id,
    ).first()
    if entry is None:
        raise LookupError("Importvorschau nicht gefunden")
    return entry, json.loads(entry.zeilen_json)


def apply_preview(db: Session, org_id: int, user_id: int, preview_id: int) -> int:
    entry, preview = load_preview(db, org_id, user_id, preview_id)
    fields = ("typ", "anzeigename", "vorname", "nachname", "funktion", "organisation", "email", "erreichbarkeit", "notizen")
    changed = 0
    for item in preview:
        row = item["row"]
        data = {field: row[field] for field in fields if field in row}
        data.setdefault("typ", "person")
        if item["status"] == "neu":
            kontakt_service.create_kontakt(db, data, [], [], org_id=org_id, user_id=user_id)
            changed += 1
        elif item["status"] == "geaendert":
            kontakt = db.query(Kontakt).filter(Kontakt.id == item["kontakt_id"], Kontakt.org_id == org_id).first()
            if kontakt is not None:
                phones = [
                    {"nummer": phone.nummer, "label": phone.label, "bevorzugt": phone.bevorzugt,
                     "sms_eignung": phone.sms_eignung}
                    for phone in kontakt.telefone
                ]
                categories = [assignment.kategorie.name for assignment in kontakt.kategorien]
                kontakt_service.update_kontakt(
                    db, kontakt.id, data, phones, categories, version=kontakt.version, org_id=org_id, user_id=user_id,
                )
                changed += 1
    db.delete(entry)
    db.commit()
    return changed


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
