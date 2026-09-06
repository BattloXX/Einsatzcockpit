"""Systemvorlage "Vollprobe – Checkliste" fuer alle bestehenden Organisationen.

Revision ID: 0233
Revises: 0232
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0233"
down_revision = "0232"
branch_labels = None
depends_on = None

TEMPLATE_CODE = "vollprobe_checklist"
TEMPLATE_NAME = "Vollprobe – Checkliste"
TEMPLATE_DESCRIPTION = "Checkliste zur organisatorischen und taktischen Vorbereitung einer Vollprobe."

# (Bereich, Punkte). Hinweise liegen als Hilfetext am zugehörigen Punkt, weil die
# bestehende Snapshot-Struktur Abschnittsbeschreibungen nicht mitkopiert.
SECTIONS = (
    ("Allgemeine Angaben", (("Namen der Ausführenden", "text", None),)),
    (
        "Eigentümer und Bewohner",
        (
            ("Vereinbart mit Eigentümer", "text", None),
            ("Vereinbart mit Eigentümer am", "datum", None),
            ("Bewohner verständigt", "ja_nein", None),
            ("Bewohner verständigt am", "datum", None),
            ("Getroffene Sondervereinbarungen", "langtext", None),
        ),
    ),
    (
        "Verletzte / Feuerwehrjugend",
        (("Jugendleiter verständigt", "ja_nein", "Bei jeder Vollprobe nehmen 3–4 Feuerwehrjugendliche teil. Wenn nicht notwendig, beim Jugendleiter abmelden."),),
    ),
    (
        "Externe Personen / Kantine",
        (("Kantine verständigt", "ja_nein", "Externe Personen (Feuerwehr, Verletzte, …) bei der Kantine anmelden."),),
    ),
    (
        "Eingesetzte Hilfsmittel",
        (
            ("Eingesetzte Hilfsmittel", "mehrfachauswahl", json.dumps(["Fahnen", "Blitzleuchte", "Nebelmaschine", "Farbnebel", "Sonstige"], ensure_ascii=False)),
            ("Sonstige Hilfsmittel", "langtext", "Bei Auswahl von „Sonstige“ hier ergänzen."),
        ),
    ),
    ("Verletzte", (("Verletzte mit Warnweste markiert", "ja_nein", None),)),
    (
        "Pläne",
        (("Pläne vorhanden", "mehrfachauswahl", json.dumps(["Brandschutzplan", "Übungsskizze", "Sonstige"], ensure_ascii=False)),),
    ),
    (
        "Aufgabenvorstellung",
        (("Aufgabenvorstellung", "langtext", "Aufgabenvorstellung für EL, RLF, Steiger, LFB-C, Tank, LF, VF und MTF."),),
    ),
    ("Besondere Aufgaben", (("Besondere Aufgaben", "langtext", None),)),
    (
        "Infos an die Dienstgrade",
        (("Infos an die Dienstgrade", "langtext", "Spätestens um 19:30 Uhr beim Lagewürfel abgeben."),),
    ),
    ("Alarmierung", (("Alarmtext", "langtext", "Alarmtext für die Vollprobe."),)),
    (
        "Besonderheiten und Gefahren",
        (("Besonderes", "langtext", "Besondere Gefahren, gefährliche Stoffe, verschlossene Türen, Schlüssel, Änderung der Lage, …"),),
    ),
    (
        "Skizze",
        (("Skizze", "bild", "Skizze oder Plan über die vorhandene Bild-/Dateifunktion der Probe anhängen."),),
    ),
)


def _ensure_code_column(bind) -> None:
    columns = {column["name"] for column in sa.inspect(bind).get_columns("checklist_template")}
    if "code" not in columns:
        with op.batch_alter_table("checklist_template") as batch:
            batch.add_column(sa.Column("code", sa.String(80), nullable=True))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("checklist_template")}
    unique = {entry["name"] for entry in sa.inspect(bind).get_unique_constraints("checklist_template")}
    if "uq_checklist_template_org_code" not in indexes | unique:
        op.create_index("uq_checklist_template_org_code", "checklist_template", ["org_id", "code"], unique=True)


def _next_id(bind, table: str) -> int:
    """BigInteger-PKs autoincrementieren auf SQLite nicht zuverlässig."""
    return bind.execute(sa.text(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}")).scalar_one()


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_code_column(bind)
    for (org_id,) in bind.execute(sa.text("SELECT id FROM fire_dept")).fetchall():
        template_id = bind.execute(
            sa.text(
                "SELECT id FROM checklist_template WHERE org_id=:org_id "
                "AND (code=:code OR name=:name) ORDER BY CASE WHEN code=:code THEN 0 ELSE 1 END"
            ),
            {"org_id": org_id, "code": TEMPLATE_CODE, "name": TEMPLATE_NAME},
        ).scalar()
        if template_id is not None:
            # Ein gleichnamiger Benutzerbestand wird nie verändert oder überschrieben.
            continue
        template_id = _next_id(bind, "checklist_template")
        bind.execute(
            sa.text(
                "INSERT INTO checklist_template (id, org_id, name, code, beschreibung, aktiv, erstellt_am) "
                "VALUES (:id, :org_id, :name, :code, :beschreibung, 1, CURRENT_TIMESTAMP)"
            ),
            {"id": template_id, "org_id": org_id, "name": TEMPLATE_NAME, "code": TEMPLATE_CODE, "beschreibung": TEMPLATE_DESCRIPTION},
        )
        version_id = _next_id(bind, "checklist_template_version")
        bind.execute(
            sa.text(
                "INSERT INTO checklist_template_version (id, org_id, template_id, version, veroeffentlicht_am, erstellt_am) "
                "VALUES (:id, :org_id, :template_id, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": version_id, "org_id": org_id, "template_id": template_id},
        )
        for section_order, (title, items) in enumerate(SECTIONS):
            section_id = _next_id(bind, "checklist_template_section")
            bind.execute(
                sa.text(
                    "INSERT INTO checklist_template_section (id, org_id, version_id, titel, sortierung) "
                    "VALUES (:id, :org_id, :version_id, :titel, :sortierung)"
                ),
                {"id": section_id, "org_id": org_id, "version_id": version_id, "titel": title, "sortierung": section_order},
            )
            for item_order, (title, item_type, extra) in enumerate(items):
                # Bei Auswahlfeldern ist extra der JSON-Optionssatz, sonst Hilfetext.
                options = extra if item_type == "mehrfachauswahl" else None
                help_text = None if item_type == "mehrfachauswahl" else extra
                bind.execute(
                    sa.text(
                        "INSERT INTO checklist_template_item "
                        "(id, org_id, section_id, titel, hilfetext, typ, optionen, pflicht, sortierung) "
                        "VALUES (:id, :org_id, :section_id, :titel, :hilfetext, :typ, :optionen, 0, :sortierung)"
                    ),
                    {"id": _next_id(bind, "checklist_template_item"), "org_id": org_id, "section_id": section_id, "titel": title, "hilfetext": help_text, "typ": item_type, "optionen": options, "sortierung": item_order},
                )
        bind.execute(
            sa.text("UPDATE checklist_template SET aktive_version_id=:version_id WHERE id=:template_id"),
            {"version_id": version_id, "template_id": template_id},
        )
        # Bestehende, individuell konfigurierte Probearten bleiben unverändert.
        bind.execute(
            sa.text(
                "UPDATE probeart SET checklist_template_id=:template_id, checkliste_erforderlich=1 "
                "WHERE org_id=:org_id AND name='Vollprobe' AND checklist_template_id IS NULL"
            ),
            {"template_id": template_id, "org_id": org_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    templates = bind.execute(
        sa.text("SELECT id FROM checklist_template WHERE code=:code"), {"code": TEMPLATE_CODE}
    ).scalars().all()
    for template_id in templates:
        # Ein erzeugter Snapshot darf beim Downgrade nie beschädigt werden.
        has_snapshot = bind.execute(
            sa.text("SELECT 1 FROM probe_checkliste WHERE template_id=:template_id LIMIT 1"),
            {"template_id": template_id},
        ).first()
        if has_snapshot:
            continue
        bind.execute(
            sa.text("UPDATE probeart SET checklist_template_id=NULL WHERE checklist_template_id=:template_id"),
            {"template_id": template_id},
        )
        bind.execute(sa.text("DELETE FROM checklist_template WHERE id=:template_id"), {"template_id": template_id})
