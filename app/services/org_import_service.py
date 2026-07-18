"""Restore/Import eines Org-Archivs in eine Ziel-Organisation (PR 4, system_admin).

Spielt ein von org_export_service erzeugtes ZIP in eine (idealerweise leere) Ziel-Org
ein und vergibt dabei durchgaengig NEUE Primaerschluessel (ID-Remapping), damit es zu
keiner Kollision mit Bestandsdaten kommt. FK-Spalten werden auf die neuen IDs
umgeschrieben; Verweise auf globale/geteilte Tabellen (role, qualification, ...) bleiben
unveraendert (deren IDs sind instanzstabil).

Ablauf:
1. Einzel-Pass in FK-topologischer Reihenfolge (Base.metadata.sorted_tables): Eltern vor
   Kind, sodass die meisten FKs sofort aufgeloest werden koennen.
2. Vorwaerts-/zyklische Verweise (der bekannte GSL-Zyklus) werden zunaechst auf NULL
   gesetzt und nach dem Pass per Fixup-UPDATE korrekt verknuepft (diese Spalten sind
   nullable).
3. Medien werden ueber dieselben Resolver wie beim Export (org_export_media) an die nun
   gueltigen Zielpfade zurueckgelegt.

Sicherheit: Import ist eine system_admin-Operation (Ziel-Org-Anlage + org-uebergreifende
Platzierung). In-place-Restore ueber eine laufende Org ist bewusst NICHT vorgesehen.
"""
from __future__ import annotations

import base64
import json
import logging
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db import Base
from app.services.org_export_service import (
    _EXTRA_LINKS,
    EXCLUDE_TABLES,
    FORMAT_VERSION,
    _single_pk,
)

logger = logging.getLogger("einsatzleiter.org_import")

_SCOPE_COLUMNS = ("org_id", "dept_id", "primary_org_id")


def read_manifest(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read("manifest.json"))


def _py_value(col, v):
    """Rueckwandlung der JSON-sicheren Werte in DB-Typen."""
    if v is None:
        return None
    t = col.type
    if isinstance(t, sa.DateTime):
        return datetime.fromisoformat(v) if isinstance(v, str) else v
    if isinstance(t, sa.Date):
        return date.fromisoformat(v) if isinstance(v, str) else v
    if isinstance(t, sa.Numeric):
        return Decimal(v) if isinstance(v, str) else v
    if isinstance(t, sa.LargeBinary):
        if isinstance(v, dict) and "__bytes_b64__" in v:
            return base64.b64decode(v["__bytes_b64__"])
        return v
    if isinstance(t, sa.Boolean):
        return bool(v)
    return v


def _scope_col(table) -> str | None:
    for c in _SCOPE_COLUMNS:
        if c in table.columns:
            return c
    return None


def _dedupe_user(db: Session, values: dict) -> None:
    """Global eindeutige Felder (username/email) beim Import kollisionsfrei machen."""
    from app.models.user import User
    uname = values.get("username")
    if uname:
        basis, n = uname, 1
        while (db.query(User).filter(User.username == values["username"])
               .execution_options(include_all_tenants=True).first()):
            values["username"] = f"{basis}-imp{n}"
            n += 1
    email = values.get("email")
    if email and (db.query(User).filter(User.email == email)
                  .execution_options(include_all_tenants=True).first()):
        values["email"] = None  # Duplikat -> leeren (Nutzer kann es neu setzen)


def _fk_checks(db: Session, on: bool) -> None:
    if db.bind and db.bind.dialect.name == "mysql":
        db.execute(sa.text(f"SET FOREIGN_KEY_CHECKS={1 if on else 0}"))


def import_org(db: Session, zip_path: Path, ziel_org_id: int) -> dict:
    """Importiert das Archiv in ziel_org_id (mit ID-Remapping). Gibt eine Zusammenfassung."""
    md = Base.metadata
    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("format_version") != FORMAT_VERSION:
            raise ValueError(f"Unbekannte Archiv-Version: {manifest.get('format_version')!r}")

        daten: dict[str, list[dict]] = {}
        for name in zf.namelist():
            if name.startswith("data/") and name.endswith(".jsonl"):
                tname = name[len("data/"):-len(".jsonl")]
                daten[tname] = [json.loads(x) for x in
                                zf.read(name).decode("utf-8").splitlines() if x]

        tabellen = [t for t in md.sorted_tables
                    if t.name in daten and t.name not in EXCLUDE_TABLES]
        idmap: dict[str, dict] = {t.name: {} for t in tabellen}
        fixups: list[tuple] = []
        neu_rows: dict[str, list[tuple]] = {t.name: [] for t in tabellen}
        counts: dict[str, int] = {}

        _fk_checks(db, False)
        try:
            for table in tabellen:
                pkcol = _single_pk(table)
                pkname = pkcol.name if pkcol is not None else None
                scope = _scope_col(table)
                fkmap = {fk.parent.name: fk.column.table.name for fk in table.foreign_keys}
                for lcol, ltgt in _EXTRA_LINKS.get(table.name, []):
                    fkmap[lcol] = ltgt

                for row in daten[table.name]:
                    old_pk = row.get(pkname) if pkname else None
                    values: dict = {}
                    for column in table.columns:
                        if column.name == pkname:
                            continue
                        values[column.name] = _py_value(column, row.get(column.name))
                    if scope and scope in values:
                        values[scope] = ziel_org_id

                    row_fixups = []
                    for col, tgt in fkmap.items():
                        if col == scope or values.get(col) is None or tgt not in idmap:
                            continue  # Scope schon gesetzt; globale/leere Refs bleiben
                        m = idmap[tgt]
                        if values[col] in m:
                            values[col] = m[values[col]]
                        else:  # Vorwaerts-/Zyklus-Ref -> spaeter fixen (Spalte nullable)
                            row_fixups.append((col, tgt, values[col]))
                            values[col] = None

                    if table.name == "user":
                        _dedupe_user(db, values)

                    res = db.execute(table.insert().values(**values))
                    new_pk = res.inserted_primary_key[0] if pkname else None  # type: ignore[attr-defined]
                    if pkname and old_pk is not None:
                        idmap[table.name][old_pk] = new_pk
                    for col, tgt, old_ref in row_fixups:
                        if pkname:
                            fixups.append((table, new_pk, col, tgt, old_ref))
                    neu_rows[table.name].append(
                        (old_pk, {**values, **({pkname: new_pk} if pkname else {})}))
                counts[table.name] = len(daten[table.name])

            for table, new_pk, col, tgt, old_ref in fixups:
                new_ref = idmap.get(tgt, {}).get(old_ref)
                if new_ref is not None:
                    pkcol = _single_pk(table)
                    db.execute(table.update().where(pkcol == new_pk).values({col: new_ref}))

            media_count = 0
            for tname, rows in neu_rows.items():
                media_count += _restore_media(zf, tname, rows)

            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            _fk_checks(db, True)

    return {
        "org_id": ziel_org_id,
        "source_org_id": manifest.get("org_id"),
        "tables": counts,
        "rows_total": sum(counts.values()),
        "media_restored": media_count,
    }


def _restore_media(zf: zipfile.ZipFile, table_name: str, neu_rows_for_table: list[tuple]) -> int:
    """Legt die Mediendateien der Tabelle an die neu gueltigen Zielpfade zurueck."""
    from app.services import org_export_media as oem
    if table_name not in oem.MEDIA_TABLES:
        return 0
    mitglieder = set(zf.namelist())
    n = 0
    for old_pk, new_row in neu_rows_for_table:
        new_pk = new_row.get("id")
        for arc_new, abs_new in oem.medien_referenzen(table_name, [new_row]):
            old_arc = arc_new.replace(f"{table_name}/{new_pk}/", f"{table_name}/{old_pk}/", 1)
            member = f"media/{old_arc}"
            if member not in mitglieder:
                continue
            try:
                abs_new.parent.mkdir(parents=True, exist_ok=True)
                abs_new.write_bytes(zf.read(member))
                n += 1
            except OSError:
                logger.warning("Restore: Mediendatei %s konnte nicht geschrieben werden", abs_new)
    return n
