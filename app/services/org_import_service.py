"""Restore/Import eines Org-Archivs in eine Ziel-Organisation (PR 4, system_admin).

Spielt ein von org_export_service erzeugtes ZIP in eine (idealerweise leere) Ziel-Org
ein und vergibt dabei durchgaengig NEUE Primaerschluessel (ID-Remapping), damit es zu
keiner Kollision mit Bestandsdaten kommt. FK-Spalten werden auf die neuen IDs
umgeschrieben; Verweise auf globale/geteilte Tabellen (role, qualification, ...) bleiben
unveraendert (deren IDs sind instanzstabil).

Ablauf:
1. Einzel-Pass in Reihenfolge der verpflichtenden FKs: Eltern vor Kind.
   Nullable Rückverweise werden beim Sortieren ausgelassen.
2. Vorwaerts-/zyklische Verweise (der bekannte GSL-Zyklus) werden zunaechst auf NULL
   gesetzt und nach dem Pass per Fixup-UPDATE korrekt verknuepft (diese Spalten sind
   nullable).
3. Medien werden ueber dieselben Resolver wie beim Export (org_export_media) an die nun
   gueltigen Zielpfade zurueckgelegt.

Sicherheit: Import ist eine system_admin-Operation (Ziel-Org-Anlage + org-uebergreifende
Platzierung). Modus:
- Neue Org (Standard): Archiv in eine frische Org importieren.
- In-place (replace=True): bestehende Org ersetzen — VORHER ein Sicherheits-Autobackup
  (Pre-Image), dann alle Org-Daten loeschen und durch den Archiv-Stand ersetzen.
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import zipfile
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db import Base
from app.services.org_export_service import (
    _EXTRA_LINKS,
    EXCLUDE_TABLES,
    FORMAT_VERSION,
    _fk_bedingungen,
    _single_pk,
    collect_ids,
)

logger = logging.getLogger("einsatzleiter.org_import")

_SCOPE_COLUMNS = ("org_id", "dept_id", "primary_org_id")


class RestoreFehler(ValueError):
    """Feste, fuer die Benutzeroberflaeche geeignete Fehlermeldung."""


def _pruefe_groesse(zf: zipfile.ZipFile) -> None:
    from app.config import settings
    if sum(info.file_size for info in zf.infolist()) > settings.ORG_RESTORE_MAX_UNCOMPRESSED_BYTES:
        raise RestoreFehler("Archiv überschreitet die erlaubte entpackte Gesamtgröße.")


def schema_status(manifest: dict) -> str:
    """Altarchive bleiben lesbar; abweichende Revisionen brauchen eine Bestaetigung."""
    from app.services.backup_service import aktueller_alembic_head
    revision = manifest.get("alembic_revision")
    if not revision:
        return "legacy"
    try:
        return "match" if revision == aktueller_alembic_head() else "mismatch"
    except Exception:
        logger.warning("Code-Head fuer Org-Restore nicht verfuegbar")
        return "mismatch"


def read_manifest(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        _pruefe_groesse(zf)
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


@contextmanager
def _fk_guard(connection):
    """SET und Ruecksetzen vor commit/rollback auf derselben Pool-Verbindung."""
    mysql = connection.dialect.name == "mysql"
    try:
        if mysql:
            connection.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
        yield
    finally:
        if mysql:
            try:
                connection.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))
            except Exception as exc:
                connection.invalidate()
                raise RestoreFehler(
                    "Fremdschlüsselprüfung konnte nicht wieder aktiviert werden; Verbindung verworfen.") from exc


def _safety_backup(db: Session, org_id: int) -> str | None:
    """Geprueftes Pre-Image; erst danach alte Sicherheitsarchive derselben Org entfernen."""
    from app.config import settings
    from app.services.org_export_service import export_org
    try:
        ziel = Path(settings.BACKUP_DIR) / "org-safety"
        ziel.mkdir(parents=True, exist_ok=True, mode=0o700)
        ziel.chmod(0o700)
        archiv = export_org(db, org_id, ziel, include_media=True)
        archiv.chmod(0o600)
        with zipfile.ZipFile(archiv) as zf:
            if zf.testzip() is not None:
                raise ValueError("ZIP-Pruefung fehlgeschlagen")
            manifest = json.loads(zf.read("manifest.json"))
            if manifest.get("format_version") != FORMAT_VERSION or manifest.get("org_id") != org_id:
                raise ValueError("Sicherungsmanifest ungueltig")
        archive = sorted(ziel.glob(f"org-backup-{org_id}-*.zip"), key=lambda p: p.name, reverse=True)
        for alt in archive[max(1, settings.ORG_SAFETY_KEEP):]:
            alt.unlink()
        return str(archiv)
    except Exception:
        logger.exception("Sicherheits-Autobackup vor Ersetzen fehlgeschlagen (org %s)", org_id)
        return None


def _delete_org_data(db: Session, org_id: int, connection=None) -> None:
    """Loescht ALLE tenant-gescopten Daten der Org (Kind vor Eltern), FK-Checks aus."""
    connection = connection if connection is not None else db.connection()
    md = Base.metadata
    collected = collect_ids(db, org_id)  # exakt dieselbe Scoping-Logik wie der Export
    for table in reversed(md.sorted_tables):
        if table.name in EXCLUDE_TABLES:
            continue
        pk = _single_pk(table)
        if pk is not None:
            ids = collected.get(table.name)
            if ids:
                connection.execute(table.delete().where(pk.in_(ids)))
        else:
            conds = _fk_bedingungen(table, collected)
            if conds:
                connection.execute(table.delete().where(sa.or_(*conds)))


def _import_reihenfolge(tabellen: list) -> list:
    """GSL-Zyklen nur an nullable Rückverweisen auftrennen, nie an Pflicht-FKs."""
    offen = {t.name: t for t in tabellen}
    result = []
    while offen:
        bereit = [t for t in offen.values() if not any(
            not fk.parent.nullable and fk.parent.name != _scope_col(t)
            and fk.column.table.name in offen and fk.column.table.name != t.name
            for fk in t.foreign_keys)]
        if not bereit:
            raise ValueError("Archiv enthält einen nicht auflösbaren Zyklus verpflichtender Verweise.")
        for table in bereit:
            result.append(table)
            del offen[table.name]
    return result


def import_org(db: Session, zip_path: Path, ziel_org_id: int, replace: bool = False,
               confirm_schema: bool = False) -> dict:
    """Importiert das Archiv in ziel_org_id (mit ID-Remapping). Gibt eine Zusammenfassung.

    replace=True: In-place-Restore — vorher ein Sicherheits-Autobackup, dann werden ALLE
    bestehenden Daten der Ziel-Org geloescht und durch den Archiv-Stand ersetzt.
    """
    connection = db.connection()
    safety_pfad = None
    md = Base.metadata
    with zipfile.ZipFile(zip_path) as zf:
        _pruefe_groesse(zf)
        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("format_version") != FORMAT_VERSION:
            raise RestoreFehler("Unbekannte Archiv-Version.")

        if schema_status(manifest) == "mismatch" and not confirm_schema:
            raise RestoreFehler("Abweichender Schema-Stand: Bitte die zusätzliche Schema-Bestätigung aktivieren.")
        daten: dict[str, list[dict]] = {}
        # Nur exakte Namen bekannter Tabellen aus dem Manifest lesen, nie extrahieren.
        for tname in manifest.get("tables", {}):
            if tname in md.tables and tname not in EXCLUDE_TABLES:
                daten[tname] = [json.loads(x) for x in
                                zf.read(f"data/{tname}.jsonl").decode("utf-8").splitlines() if x]
        if replace:
            safety_pfad = _safety_backup(db, ziel_org_id)
            if not safety_pfad:
                raise RestoreFehler(
                    "Ersetzen abgebrochen: Das erforderliche Sicherheits-Backup ist fehlgeschlagen. "
                    "Es wurden keine Daten gelöscht.")
            # Auch bei alternativen Backup-Implementierungen die Lesbarkeit erzwingen.
            try:
                with zipfile.ZipFile(safety_pfad) as safety:
                    if safety.testzip() is not None:
                        raise ValueError("ZIP-Pruefung fehlgeschlagen")
                    sicher_manifest = json.loads(safety.read("manifest.json"))
                    if (sicher_manifest.get("org_id") != ziel_org_id
                            or sicher_manifest.get("format_version") != FORMAT_VERSION):
                        raise ValueError("Falsche Sicherungsorganisation")
            except Exception as exc:
                raise RestoreFehler(
                    "Ersetzen abgebrochen: Das Sicherheits-Backup ist nicht lesbar. "
                    "Es wurden keine Daten gelöscht.") from exc

        tabellen = _import_reihenfolge([t for t in md.tables.values()
                                      if t.name in daten and t.name not in EXCLUDE_TABLES])
        idmap: dict[str, dict] = {t.name: {} for t in tabellen}
        fixups: list[tuple] = []
        neu_rows: dict[str, list[tuple]] = {t.name: [] for t in tabellen}
        counts: dict[str, int] = {}

        try:
            with _fk_guard(connection):
                if replace:
                    _delete_org_data(db, ziel_org_id, connection)
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
                            if column.name == pkname and pkname != scope:
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

                        # Relative Medien bekommen einen eigenen Ziel-Org-Namensraum.
                        # So ueberschreibt auch ein gueltiger Archivpfad keine Quell-Org-Datei.
                        from app.services.org_export_media import medien_referenzen
                        if "stored_filename" not in values:
                            for arc, pfad in medien_referenzen(table.name, [values]):
                                col = arc.split("/")[2]
                                values[col] = f"{ziel_org_id}/org-restore/{uuid4().hex}/{pfad.name}"
                        res = connection.execute(table.insert().values(**values))
                        inserted_pk = res.inserted_primary_key
                        new_pk = inserted_pk[0] if pkname and inserted_pk is not None else None
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
                        connection.execute(table.update().where(pkcol == new_pk).values({col: new_ref}))

                media_count = 0
                media_skipped: list[str] = []
                for tname, rows in neu_rows.items():
                    media_count += _restore_media(zf, tname, rows, media_skipped)
            db.commit()
        except Exception:
            db.rollback()
            raise

    return {
        "org_id": ziel_org_id,
        "source_org_id": manifest.get("org_id"),
        "tables": counts,
        "rows_total": sum(counts.values()),
        "media_restored": media_count,
        "media_skipped": len(media_skipped),
        "replaced": replace,
        "safety_backup": Path(safety_pfad).name if safety_pfad else None,
    }


def _restore_media(zf: zipfile.ZipFile, table_name: str, neu_rows_for_table: list[tuple],
                   skipped: list[str] | None = None) -> int:
    """Legt die Mediendateien der Tabelle an die neu gueltigen Zielpfade zurueck."""
    from app.services import org_export_media as oem
    if table_name not in oem.MEDIA_TABLES:
        return 0
    mitglieder = set(zf.namelist())
    n = 0
    for old_pk, new_row in neu_rows_for_table:
        new_pk = new_row.get("id")
        for arc_new, abs_new in oem.medien_referenzen(table_name, [new_row], skipped):
            old_arc = arc_new.replace(f"{table_name}/{new_pk}/", f"{table_name}/{old_pk}/", 1)
            member = f"media/{old_arc}"
            if member not in mitglieder:
                continue
            try:
                abs_new.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, abs_new.open("wb") as target:
                    shutil.copyfileobj(source, target)
                n += 1
            except OSError:
                logger.warning("Restore: Mediendatei konnte nicht geschrieben werden (Tabelle %s)", table_name)
                if skipped is not None:
                    skipped.append(table_name)
    return n
