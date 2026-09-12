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


def _text(value: object) -> str:
    return str(value or "").strip()


def _bool(value: object) -> bool:
    return _text(value).lower() in {"1", "true", "ja", "yes", "x"}


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
        by_id: dict[str, dict[str, Any]] = {_text(row.get("id")): row for row in rows if _text(row.get("id"))}
        for sheet_name, key in (("Telefonnummern", "_telefone"), ("Objektzuordnungen", "_zuordnungen")):
            if sheet_name not in workbook.sheetnames:
                continue
            sheet_values = list(workbook[sheet_name].values)
            if not sheet_values:
                continue
            sheet_headers = [_text(value) for value in sheet_values[0]]
            for values_ in sheet_values[1:]:
                related = dict(zip(sheet_headers, values_, strict=False))
                parent = by_id.get(_text(related.get("kontakt_id")))
                if parent is not None:
                    parent.setdefault(key, []).append(related)
    else:
        raise ValueError("Bitte eine CSV- oder XLSX-Datei hochladen")
    if len(rows) > 1000:
        raise ValueError("Hoechstens 1000 Kontakte pro Import")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                key: ([_normalize_related(item) for item in value] if isinstance(value, list) else _text(value))
                for key, value in row.items()
            }
        )
    return normalized


def _normalize_related(row: dict[str, Any]) -> dict[str, str]:
    return {key: _text(value) for key, value in row.items()}


def preview_import(db: Session, org_id: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify rows without changing data; all candidate queries stay in-org."""
    preview: list[dict[str, Any]] = []
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
                db,
                anzeigename=name,
                organisation=row.get("organisation"),
                email=row.get("email"),
            )
            preview.append(
                {
                    "status": "dublette" if duplicates else "neu",
                    "row": row,
                    "kandidaten": [candidate.id for candidate in duplicates],
                }
            )
    return preview


def save_preview(
    db: Session,
    org_id: int,
    user_id: int,
    preview: list[dict[str, Any]],
) -> KontaktImportVorschau:
    entry = KontaktImportVorschau(org_id=org_id, user_id=user_id, zeilen_json=json.dumps(preview))
    db.add(entry)
    db.commit()
    return entry


def load_preview(
    db: Session,
    org_id: int,
    user_id: int,
    preview_id: int,
) -> tuple[KontaktImportVorschau, list[dict[str, Any]]]:
    entry = (
        db.query(KontaktImportVorschau)
        .filter(
            KontaktImportVorschau.id == preview_id,
            KontaktImportVorschau.org_id == org_id,
            KontaktImportVorschau.user_id == user_id,
        )
        .first()
    )
    if entry is None:
        raise LookupError("Importvorschau nicht gefunden")
    return entry, json.loads(entry.zeilen_json)


def apply_preview(db: Session, org_id: int, user_id: int, preview_id: int) -> int:
    entry, preview = load_preview(db, org_id, user_id, preview_id)
    fields = (
        "typ",
        "anzeigename",
        "vorname",
        "nachname",
        "funktion",
        "organisation",
        "email",
        "erreichbarkeit",
        "notizen",
    )
    changed = 0
    results: list[dict[str, Any]] = []
    for item in preview:
        kontakt: Kontakt | None = None
        row = item["row"]
        data = {field: row[field] for field in fields if field in row}
        data.setdefault("typ", "person")
        phones = _phones(row.get("_telefone", []))
        if item["status"] == "neu":
            kontakt = kontakt_service.create_kontakt(db, data, phones, [], org_id=org_id, user_id=user_id)
            changed += 1
        elif item["status"] == "geaendert":
            kontakt = db.query(Kontakt).filter(Kontakt.id == item["kontakt_id"], Kontakt.org_id == org_id).first()
            if kontakt is not None:
                existing_phones = [
                    {
                        "nummer": phone.nummer,
                        "label": phone.label,
                        "bevorzugt": phone.bevorzugt,
                        "sms_eignung": phone.sms_eignung,
                    }
                    for phone in kontakt.telefone
                ]
                categories = [assignment.kategorie.name for assignment in kontakt.kategorien]
                kontakt = kontakt_service.update_kontakt(
                    db,
                    kontakt.id,
                    data,
                    phones or existing_phones,
                    categories,
                    version=kontakt.version,
                    org_id=org_id,
                    user_id=user_id,
                )
                changed += 1
            else:
                results.append({"status": "fehler", "row": row, "message": "Kontakt nicht mehr vorhanden"})
                continue
        else:
            results.append({"status": item["status"], "row": row, "message": item.get("message", "Nicht uebernommen")})
            continue
        if kontakt is None:
            continue
        mapping_errors = _apply_mappings(db, org_id, kontakt, row.get("_zuordnungen", []))
        results.append(
            {"status": "uebernommen", "row": row, "kontakt_id": kontakt.id, "message": "; ".join(mapping_errors)}
        )
    entry.ergebnis_json = json.dumps(results)
    db.commit()
    return changed


def _phones(rows: object) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [
        {
            "nummer": row.get("nummer", ""),
            "label": row.get("label", ""),
            "bevorzugt": _bool(row.get("bevorzugt")),
            "sms_eignung": _bool(row.get("sms_eignung")),
        }
        for row in rows
        if isinstance(row, dict) and row.get("nummer")
    ]


def _apply_mappings(db: Session, org_id: int, kontakt: Kontakt, rows: object) -> list[str]:
    """Upsert mappings only for objects in the current tenant; never touch releases."""
    if not isinstance(rows, list):
        return []
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("objekt_id")).isdigit():
            continue
        objekt = db.query(Objekt).filter(Objekt.id == int(_text(row["objekt_id"])), Objekt.org_id == org_id).first()
        if objekt is None:
            errors.append("Objektzuordnung ausserhalb der Organisation ignoriert")
            continue
        assignment = (
            db.query(ObjektKontakt)
            .filter(
                ObjektKontakt.org_id == org_id,
                ObjektKontakt.objekt_id == objekt.id,
                ObjektKontakt.kontakt_id == kontakt.id,
            )
            .first()
        )
        if assignment is None:
            assignment = ObjektKontakt(
                org_id=org_id, objekt_id=objekt.id, kontakt_id=kontakt.id, name=kontakt.anzeigename
            )
            db.add(assignment)
        assignment.art = _text(row.get("rolle")) or "sonstig"
        assignment.sort = int(_text(row.get("sort")) or 0)
        assignment.erreichbarkeit = _text(row.get("erreichbarkeit")) or None
    db.flush()
    return errors


def export_result_csv(db: Session, org_id: int, user_id: int, preview_id: int) -> bytes:
    _entry, rows = load_preview(db, org_id, user_id, preview_id)
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["status", "id", "anzeigename", "meldung"])
    for row in json.loads(_entry.ergebnis_json or "[]"):
        original = row.get("row", {})
        writer.writerow(
            [
                row.get("status", ""),
                row.get("kontakt_id", original.get("id", "")),
                original.get("anzeigename", ""),
                row.get("message", ""),
            ]
        )
    return out.getvalue().encode("utf-8-sig")


def _kontakte(db: Session, org_id: int) -> list[Kontakt]:
    return (
        db.query(Kontakt)
        .options(selectinload(Kontakt.telefone))
        .filter(Kontakt.org_id == org_id, Kontakt.archiviert.is_(False))
        .order_by(Kontakt.anzeigename, Kontakt.id)
        .all()
    )


def export_csv(db: Session, org_id: int) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["version", "id", "typ", "anzeigename", "organisation", "funktion", "email", "erreichbarkeit"])
    for kontakt in _kontakte(db, org_id):
        writer.writerow(
            [
                FORMAT_VERSION,
                kontakt.id,
                kontakt.typ,
                kontakt.anzeigename,
                kontakt.organisation or "",
                kontakt.funktion or "",
                kontakt.email or "",
                kontakt.erreichbarkeit or "",
            ]
        )
    return out.getvalue().encode("utf-8-sig")


def export_template_csv(beispiel: bool = False) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["version", "id", "typ", "anzeigename", "organisation", "funktion", "email", "erreichbarkeit"])
    if beispiel:
        writer.writerow(
            [
                FORMAT_VERSION,
                "",
                "person",
                "Max Mustermann",
                "Muster GmbH",
                "Bereitschaft",
                "max@example.test",
                "tagsueber",
            ]
        )
    return out.getvalue().encode("utf-8-sig")


def export_xlsx(db: Session, org_id: int) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    kontakte = _kontakte(db, org_id)
    sheet = wb.active
    sheet.title = "Kontakte"
    sheet.append(
        [
            "version",
            "id",
            "typ",
            "anzeigename",
            "vorname",
            "nachname",
            "funktion",
            "organisation",
            "email",
            "erreichbarkeit",
            "notizen",
        ]
    )
    for k in kontakte:
        sheet.append(
            [
                FORMAT_VERSION,
                k.id,
                k.typ,
                k.anzeigename,
                k.vorname,
                k.nachname,
                k.funktion,
                k.organisation,
                k.email,
                k.erreichbarkeit,
                k.notizen,
            ]
        )
    phones = wb.create_sheet("Telefonnummern")
    phones.append(["kontakt_id", "nummer", "label", "sort", "bevorzugt", "sms_eignung"])
    for k in kontakte:
        for p in k.telefone:
            phones.append([k.id, p.nummer, p.label, p.sort, p.bevorzugt, p.sms_eignung])
    mappings = wb.create_sheet("Objektzuordnungen")
    mappings.append(["kontakt_id", "objekt_id", "objekt", "rolle", "sort", "erreichbarkeit"])
    for row, objekt_name in (
        db.query(ObjektKontakt, Objekt.name)
        .join(Objekt)
        .filter(ObjektKontakt.org_id == org_id, ObjektKontakt.kontakt_id.is_not(None))
        .order_by(ObjektKontakt.objekt_id, ObjektKontakt.sort)
        .all()
    ):
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


def export_template_xlsx(beispiel: bool = False) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    kontakte = wb.active
    kontakte.title = "Kontakte"
    kontakte.append(
        [
            "version",
            "id",
            "typ",
            "anzeigename",
            "vorname",
            "nachname",
            "funktion",
            "organisation",
            "email",
            "erreichbarkeit",
            "notizen",
        ]
    )
    phones = wb.create_sheet("Telefonnummern")
    phones.append(["kontakt_id", "nummer", "label", "sort", "bevorzugt", "sms_eignung"])
    mappings = wb.create_sheet("Objektzuordnungen")
    mappings.append(["kontakt_id", "objekt_id", "objekt", "rolle", "sort", "erreichbarkeit"])
    guide = wb.create_sheet("Anleitung")
    guide.append(["Format", f"Kontakt-Import/Export v{FORMAT_VERSION}"])
    guide.append(["Ablauf", "Exportieren, bearbeiten, Vorschau pruefen, dann explizit uebernehmen."])
    guide.append(
        [
            "Sicherheit",
            "IDs und Objektzuordnungen gelten nur innerhalb derselben Organisation; Freigaben werden nie importiert.",
        ]
    )
    if beispiel:
        kontakte.append(
            [
                FORMAT_VERSION,
                "",
                "person",
                "Max Mustermann",
                "Max",
                "Mustermann",
                "Bereitschaft",
                "Muster GmbH",
                "max@example.test",
                "tagsueber",
                "",
            ]
        )
        phones.append(["", "+43 664 1234567", "Mobil", 0, True, True])
    for sheet in wb.worksheets:
        sheet.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
