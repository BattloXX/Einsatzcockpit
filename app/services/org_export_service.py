"""Tenant-gescopter Voll-Export einer Organisation als ZIP (Self-Service-Backup).

Anders als der serverweite mariadb-dump (alle Orgs) sichert dieser Export NUR die
Daten EINER Organisation - logisch ueber die ORM-/Metadata-Schicht, damit die
Tenant-Grenze sauber eingehalten wird.

Sammel-Strategie (generisch statt 120-Zeilen-Handliste):
1. **Roots**: jede Tabelle mit einer Org-Spalte (org_id/dept_id/primary_org_id) wird
   direkt auf die Org gefiltert; Incident zusaetzlich ueber die Kollaboration
   (incident_org).
2. **Closure**: Kindtabellen ohne Org-Spalte werden ueber ihre Fremdschluessel auf die
   bereits gesammelten Eltern-IDs eingeschraenkt (Fixpunkt-Iteration). So werden auch
   tief verschachtelte Kinder (z. B. troop_member -> breathing_troop -> incident_vehicle)
   automatisch und vollstaendig erfasst.

EXCLUDE (nie je Org exportiert): globale/geteilte Kataloge (fire_dept, role,
qualification, seed_template, system_settings, rettungsdatenblatt_cache) und
fluechtige/geraetegebundene Sicherheits-Artefakte (Tokens, Sessions, Push/Device).
Fernet-verschluesselte `*_enc`-Spalten werden redigiert (mit dem Server-Key
verschluesselt -> off-instance unbrauchbar, sensibel).
"""
from __future__ import annotations

import base64
import json
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Table, or_, select
from sqlalchemy.orm import Session

from app.db import Base

FORMAT_VERSION = 1

# Org-Spalten, an denen eine Tabelle als "Root" erkannt wird.
_SCOPE_COLUMNS = ("org_id", "dept_id", "primary_org_id")

# Globale/geteilte Kataloge: gehoeren nicht zu EINER Org.
_GLOBAL_TABLES = frozenset({
    "fire_dept", "role", "qualification", "seed_template",
    "system_settings", "rettungsdatenblatt_cache", "alembic_version",
})

# Fluechtige/geraetegebundene Sicherheits-Artefakte: kein Bestandteil eines
# Daten-Backups (Tokens/Sessions/Push sind server-/geraetegebunden).
_EPHEMERAL_TABLES = frozenset({
    "password_reset_token", "login_pin", "device_token", "fcm_token",
    "push_subscription", "push_log", "incident_token", "alarm_token",
    "lage_token", "lagekarte_token", "weather_dashboard_token",
    "alarm_infoscreen_token", "api_key", "sms_gateway_token",
})

EXCLUDE_TABLES = _GLOBAL_TABLES | _EPHEMERAL_TABLES

# Eltern-Verweise OHNE FK-Constraint (plain Spalte). Werden fuer die Closure wie ein
# FK behandelt. Der Coverage-Test erzwingt, dass so verlinkte Tabellen hier stehen.
_EXTRA_LINKS: dict[str, list[tuple[str, str]]] = {
    "media_annotation_version": [("annotation_id", "media_annotation")],
}


# Umschaltbare Backup-Bereiche (partielles Backup). Root-Tabellen, die in KEINEM
# Bereich stehen, sind "Kern" (Stammdaten/Konfig/Nutzer/Audit) und immer enthalten.
AREA_ROOTS: dict[str, set[str]] = {
    "einsaetze": {
        "incident", "incident_org", "teams_card_post", "lis_synced_object",
        "atemschutz_pruefung", "atemschutz_pruef_benachrichtigung",
        "lagefuehrung_feature", "lagefuehrung_event", "lagefuehrung_berechtigung",
        "lagefuehrung_snapshot",
    },
    "gsl": {
        "major_incident", "incident_site", "gsl_staff_role", "gsl_staff_assignment",
        "lage_dokument", "cross_site_marker", "cross_marker_media", "site_media",
        "lage_journal_media", "vehicle_position",
    },
    "objekte": {
        "objekt", "objekt_auswahl", "objekt_bma", "objekt_change", "objekt_dokument",
        "objekt_dokument_seite", "objekt_einsatz", "objekt_gefahr", "objekt_karten_objekt",
        "objekt_kategorie", "objekt_kontakt", "objekt_merkmal", "objekt_seite_ki_vorschlag",
        "objekt_symbol", "objekt_wohnanlage", "objekt_zusatzadresse",
        "gefahren_katalog", "merkmal_katalog", "infoscreen_url",
    },
    "fahrtenbuch": {"fahrt", "fahrt_benachrichtigung", "fahrt_media", "fahrtzweck", "zielort"},
    "uas": {
        "uas_checkliste", "uas_device", "uas_einsatz", "uas_einsatz_rolle", "uas_ereignis",
        "uas_flug", "uas_flugbewegung", "uas_kartenobjekt", "uas_medien", "uas_pilot", "uas_wartung",
    },
    "verleih": {
        "verleih_artikel", "verleih_ausleihe", "verleih_foto", "verleih_geraetetyp",
        "verleih_position", "verleih_stueckliste",
    },
    "wetter": {"weather_alert_log", "weather_alert_rule", "weather_alert_state", "weather_station"},
    "teilnahme": {"termin", "funktion", "teilnahme"},
    "mannschaft": {"member"},
    "sms": {"sms_einsatzinfo_recipient", "sms_forward_rule", "sms_group", "sms_inbox", "sms_log"},
}

AREA_LABELS: dict[str, str] = {
    "einsaetze": "Einsätze / Archiv / Lageführung",
    "gsl": "Großschadenslage (GSL)",
    "objekte": "Objektverwaltung",
    "fahrtenbuch": "Fahrtenbuch",
    "uas": "Drohnen (UAS)",
    "verleih": "Geräteverleih",
    "wetter": "Wetterstation / -warnungen",
    "teilnahme": "Teilnehmerlisten",
    "mannschaft": "Mannschaft",
    "sms": "SMS-Gruppen / -Log",
}

_ALLE_AREA_TABELLEN: set[str] = set().union(*AREA_ROOTS.values())


def areas_aus_string(s: str | None) -> set[str] | None:
    """Parst die gespeicherte Bereichsauswahl. None -> alle (kein partielles Backup)."""
    if s is None:
        return None
    return {a for a in (x.strip() for x in s.split(",")) if a}


def _root_erlaubt(tname: str, areas: set[str] | None) -> bool:
    """True, wenn diese Root-Tabelle exportiert werden soll (Kern immer, sonst je Bereich)."""
    if areas is None:
        return True
    if tname not in _ALLE_AREA_TABELLEN:
        return True  # Kern (Stammdaten/Konfig) — immer enthalten
    return any(tname in AREA_ROOTS[a] for a in areas)


def _pk_columns(table: Table) -> list:
    return list(table.primary_key.columns)


def _single_pk(table: Table):
    cols = _pk_columns(table)
    return cols[0] if len(cols) == 1 else None


def scope_rules() -> dict[str, str]:
    """{Tabellenname: Org-Spalte} fuer alle Root-Tabellen (ausser EXCLUDE)."""
    rules: dict[str, str] = {}
    for table in Base.metadata.tables.values():
        if table.name in EXCLUDE_TABLES:
            continue
        for col in _SCOPE_COLUMNS:
            if col in table.columns:
                rules[table.name] = col
                break
    return rules


def _redigiert(spaltenname: str) -> bool:
    """Server-gebundene Secrets (*_enc) werden nicht exportiert."""
    return spaltenname.endswith("_enc")


def _json_wert(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"__bytes_b64__": base64.b64encode(bytes(v)).decode("ascii")}
    return str(v)


def collect_ids(db: Session, org_id: int, areas: set[str] | None = None) -> dict[str, set]:
    """Sammelt je Single-PK-Tabelle die zu exportierenden Primaerschluessel.

    areas=None -> vollstaendig; sonst nur Kern-Tabellen + die gewaehlten Bereiche
    (partielles Backup). Assoziationstabellen (zusammengesetzter PK) werden hier NICHT
    gesammelt; sie sind Blaetter und werden beim Schreiben ueber ihre FKs gefiltert.
    """
    md = Base.metadata
    rules = scope_rules()
    collected: dict[str, set] = {}

    # 1. Roots
    for tname, col in rules.items():
        if not _root_erlaubt(tname, areas):
            continue
        table = md.tables[tname]
        pk = _single_pk(table)
        if pk is None:
            continue  # zusammengesetzter PK -> als Blatt behandelt
        ids = set(db.execute(select(pk).where(table.c[col] == org_id)).scalars())
        collected[tname] = ids

    # Incident zusaetzlich ueber Kollaboration (incident_org)
    inc = md.tables.get("incident")
    io = md.tables.get("incident_org")
    if inc is not None and io is not None and _root_erlaubt("incident", areas):
        extra = db.execute(
            select(inc.c.id).where(
                inc.c.id.in_(select(io.c.incident_id).where(io.c.org_id == org_id))
            )
        ).scalars()
        collected.setdefault("incident", set()).update(extra)

    # 2. Closure ueber Kindtabellen (Single-PK, keine Org-Spalte, nicht EXCLUDE)
    kinder = [
        t for t in md.tables.values()
        if t.name not in rules and t.name not in EXCLUDE_TABLES
        and _single_pk(t) is not None
    ]
    geaendert = True
    while geaendert:
        geaendert = False
        for table in kinder:
            conds = _fk_bedingungen(table, collected)
            if not conds:
                continue
            pk = _single_pk(table)
            ids = set(db.execute(select(pk).where(or_(*conds))).scalars())
            vorher = collected.get(table.name, set())
            if ids - vorher:
                collected[table.name] = vorher | ids
                geaendert = True
    return collected


def _fk_bedingungen(table: Table, collected: dict[str, set]) -> list:
    """WHERE-Teilbedingungen: FK dieser Tabelle zeigt auf eine gesammelte Eltern-ID."""
    conds = []
    for fk in table.foreign_keys:
        ziel = fk.column.table.name
        eltern = collected.get(ziel)
        if eltern:
            conds.append(table.c[fk.parent.name].in_(eltern))
    # FK-lose Eltern-Verweise (plain Spalte) genauso beruecksichtigen.
    for spalte, ziel in _EXTRA_LINKS.get(table.name, []):
        eltern = collected.get(ziel)
        if eltern:
            conds.append(table.c[spalte].in_(eltern))
    return conds


def _serialisiere_tabelle(db: Session, table: Table, collected: dict[str, set]) -> list[dict]:
    """Alle zu exportierenden Zeilen einer Tabelle als JSON-sichere Dicts."""
    pk = _single_pk(table)
    if pk is not None:
        ids = collected.get(table.name)
        if not ids:
            return []
        stmt = select(table).where(pk.in_(ids))
    else:
        # Assoziationstabelle (zusammengesetzter PK): ueber FKs auf gesammelte Eltern
        conds = _fk_bedingungen(table, collected)
        if not conds:
            return []
        stmt = select(table).where(or_(*conds))

    zeilen = []
    for row in db.execute(stmt).mappings():
        zeilen.append({
            k: (None if _redigiert(k) else _json_wert(v))
            for k, v in row.items()
        })
    return zeilen


def export_org(db: Session, org_id: int, out_dir: Path, include_media: bool = True,
               areas: set[str] | None = None) -> Path:
    """Exportiert die Daten (+ optional Medien) einer Org als ZIP; gibt den Pfad zurueck.

    areas=None -> vollstaendig; sonst partielles Backup (Kern + gewaehlte Bereiche).
    Voraussetzung: der Aufrufer hat set_tenant_context(db, None) gesetzt (System-Modus),
    damit die explizite Org-Filterung hier nicht mit dem Tenant-Listener kollidiert.
    """
    from app.config import settings
    from app.routers.ui_backup import _build_export as _config_export

    md = Base.metadata
    collected = collect_ids(db, org_id, areas)

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    ziel = out_dir / f"org-backup-{org_id}-{stamp}.zip"

    # Alle nicht ausgeschlossenen Tabellen (Roots, Kinder UND Assoziationstabellen).
    exportierbar = [t for t in md.tables.values() if t.name not in EXCLUDE_TABLES]

    tabellen_counts: dict[str, int] = {}
    medien_pfade: list[tuple[str, Path]] = []

    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in exportierbar:
            zeilen = _serialisiere_tabelle(db, table, collected)
            if not zeilen:
                continue
            tabellen_counts[table.name] = len(zeilen)
            inhalt = "\n".join(json.dumps(z, ensure_ascii=False, default=str) for z in zeilen)
            zf.writestr(f"data/{table.name}.jsonl", inhalt + "\n")
            if include_media:
                medien_pfade.extend(_medien_aus_zeilen(table.name, zeilen))

        if include_media:
            _schreibe_medien(zf, medien_pfade)

        # Konfig (bestehender JSON-Export)
        zf.writestr("config.json",
                    json.dumps(_config_export(db, org_id), ensure_ascii=False, indent=2, default=str))

        manifest = {
            "format_version": FORMAT_VERSION,
            "app_version": settings.APP_VERSION,
            "org_id": org_id,
            "exported_at": datetime.now(UTC).isoformat(),
            "tables": tabellen_counts,
            "media_count": len(medien_pfade),
            "redacted_columns": "*_enc",
            "excluded_tables": sorted(EXCLUDE_TABLES),
            "areas": sorted(areas) if areas is not None else "all",
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return ziel


# ── Medien (wird in einem Folgeschritt dieses PRs befuellt) ────────────────────

def _medien_aus_zeilen(tabelle: str, zeilen: list[dict]) -> list[tuple[str, Path]]:
    """Ermittelt (arcname, absoluter Pfad) der Mediendateien einer Tabelle."""
    from app.services.org_export_media import medien_referenzen
    return medien_referenzen(tabelle, zeilen)


def _schreibe_medien(zf: zipfile.ZipFile, medien: list[tuple[str, Path]]) -> None:
    gesehen: set[str] = set()
    for arcname, pfad in medien:
        if arcname in gesehen:
            continue
        gesehen.add(arcname)
        try:
            if pfad.is_file():
                zf.write(pfad, f"media/{arcname}")
        except OSError:
            continue
