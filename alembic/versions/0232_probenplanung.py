"""Fundament der Probenplanung.

Revision ID: 0232
Revises: 0231
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0232"
down_revision = "0231"
branch_labels = None
depends_on = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _spalten(tabelle: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(tabelle)} if tabelle in _tabellen() else set()


def _indizes(tabelle: str) -> set[str]:
    if tabelle not in _tabellen():
        return set()
    insp = sa.inspect(op.get_bind())
    return {i["name"] for i in insp.get_indexes(tabelle) if i.get("name")} | {
        u["name"] for u in insp.get_unique_constraints(tabelle) if u.get("name")
    }


def _basis(*spalten: sa.Column) -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True),
        *spalten,
    ]


def _erzeuge(name: str, spalten: list[sa.Column], *constraints) -> None:
    if name not in _tabellen():
        op.create_table(name, *spalten, *constraints)
        return
    vorhanden = _spalten(name)
    for column in spalten:
        if column.name not in vorhanden:
            with op.batch_alter_table(name) as batch:
                batch.add_column(column)


def _neue_tabellen() -> None:
    _erzeuge(
        "checklist_template",
        _basis(
            sa.Column("name", sa.String(150), nullable=False, server_default=""),
            sa.Column("beschreibung", sa.Text()),
            sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("aktive_version_id", sa.BigInteger()),
            sa.Column("erstellt_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
            sa.Column("erstellt_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_checklist_template_org_name"),
    )
    _erzeuge(
        "checklist_template_version",
        _basis(
            sa.Column(
                "template_id",
                sa.BigInteger(),
                sa.ForeignKey("checklist_template.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("veroeffentlicht_am", sa.DateTime()),
            sa.Column("notiz", sa.Text()),
            sa.Column("erstellt_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
            sa.Column("erstellt_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        ),
        sa.UniqueConstraint("org_id", "template_id", "version", name="uq_checklist_template_version"),
    )
    # Der Zirkel wird nach beiden Tabellen aufgebaut; auf SQLite bleibt die Spalte
    # ohne FK, weil ALTER CONSTRAINT dort nicht unterstuetzt wird.
    if op.get_bind().dialect.name != "sqlite":
        fks = {f.get("name") for f in sa.inspect(op.get_bind()).get_foreign_keys("checklist_template")}
        if "fk_checklist_template_aktive_version" not in fks:
            with op.batch_alter_table("checklist_template") as batch:
                batch.create_foreign_key(
                    "fk_checklist_template_aktive_version",
                    "checklist_template_version",
                    ["aktive_version_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
    _erzeuge(
        "probeart",
        _basis(
            sa.Column("name", sa.String(150), nullable=False, server_default=""),
            sa.Column("kurz", sa.String(20), nullable=False, server_default=""),
            sa.Column("farbe", sa.String(7), nullable=False, server_default="#2563eb"),
            sa.Column("sortierung", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("standarddauer_minuten", sa.Integer()),
            sa.Column("druckgruppe", sa.String(50)),
            sa.Column("termin_typ", sa.String(20), nullable=False, server_default="uebung"),
            sa.Column("checkliste_erforderlich", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "checklist_template_id", sa.BigInteger(), sa.ForeignKey("checklist_template.id", ondelete="SET NULL")
            ),
            sa.Column("teilnahme_erforderlich", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("nachbereitung_erforderlich", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("uebungseinsatz_erlaubt", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_probeart_org_name"),
    )
    _erzeuge(
        "checklist_template_section",
        _basis(
            sa.Column(
                "version_id",
                sa.BigInteger(),
                sa.ForeignKey("checklist_template_version.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("titel", sa.String(200), nullable=False, server_default=""),
            sa.Column("beschreibung", sa.Text()),
            sa.Column("sortierung", sa.Integer(), nullable=False, server_default="0"),
        ),
    )
    _erzeuge(
        "checklist_template_item",
        _basis(
            sa.Column(
                "section_id",
                sa.BigInteger(),
                sa.ForeignKey("checklist_template_section.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("titel", sa.String(200), nullable=False, server_default=""),
            sa.Column("hilfetext", sa.Text()),
            sa.Column("typ", sa.String(20), nullable=False, server_default="checkbox"),
            sa.Column("optionen", sa.Text()),
            sa.Column("pflicht", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("sortierung", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "default_verantwortlich_member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="SET NULL")
            ),
            sa.Column("faellig_tage_vorher", sa.Integer()),
        ),
    )
    _erzeuge(
        "probe_checkliste",
        _basis(
            sa.Column("termin_id", sa.BigInteger(), sa.ForeignKey("termin.id", ondelete="CASCADE"), nullable=False),
            sa.Column("template_id", sa.BigInteger(), sa.ForeignKey("checklist_template.id", ondelete="SET NULL")),
            sa.Column(
                "template_version_id",
                sa.BigInteger(),
                sa.ForeignKey("checklist_template_version.id", ondelete="SET NULL"),
            ),
            sa.Column("template_name", sa.String(150), nullable=False, server_default=""),
            sa.Column("template_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("erstellt_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        ),
        sa.UniqueConstraint("org_id", "termin_id", name="uq_probe_checkliste_termin"),
    )
    _erzeuge(
        "probe_checklist_section",
        _basis(
            sa.Column(
                "checkliste_id",
                sa.BigInteger(),
                sa.ForeignKey("probe_checkliste.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("titel", sa.String(200), nullable=False, server_default=""),
            sa.Column("sortierung", sa.Integer(), nullable=False, server_default="0"),
        ),
    )
    _erzeuge(
        "probe_media",
        _basis(
            sa.Column("termin_id", sa.BigInteger(), sa.ForeignKey("termin.id", ondelete="CASCADE"), nullable=False),
            sa.Column("art", sa.String(20), nullable=False, server_default="dokument"),
            sa.Column("name", sa.String(255), nullable=False, server_default=""),
            sa.Column("beschreibung", sa.Text()),
            sa.Column("typ", sa.String(50)),
            sa.Column("kind", sa.String(20), nullable=False, server_default="image"),
            sa.Column("mime_type", sa.String(100), nullable=False, server_default="application/octet-stream"),
            sa.Column("path", sa.String(500), nullable=False, server_default=""),
            sa.Column("thumb_path", sa.String(500)),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("hochgeladen_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
            sa.Column("hochgeladen_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        ),
    )
    _erzeuge(
        "probe_checklist_item",
        _basis(
            sa.Column(
                "checkliste_id",
                sa.BigInteger(),
                sa.ForeignKey("probe_checkliste.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("section_id", sa.BigInteger(), sa.ForeignKey("probe_checklist_section.id", ondelete="SET NULL")),
            sa.Column("quelle", sa.String(20), nullable=False, server_default="vorlage"),
            sa.Column("template_item_id", sa.BigInteger()),
            sa.Column("titel", sa.String(200), nullable=False, server_default=""),
            sa.Column("hilfetext", sa.Text()),
            sa.Column("typ", sa.String(20), nullable=False, server_default="checkbox"),
            sa.Column("optionen", sa.Text()),
            sa.Column("pflicht", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("sortierung", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("verantwortlich_member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="SET NULL")),
            sa.Column("faellig_am", sa.Date()),
            sa.Column("zustand", sa.String(20), nullable=False, server_default="offen"),
            sa.Column("begruendung", sa.Text()),
            sa.Column("wert_text", sa.Text()),
            sa.Column("wert_member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="SET NULL")),
            sa.Column("wert_media_id", sa.BigInteger(), sa.ForeignKey("probe_media.id", ondelete="SET NULL")),
            sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("erledigt_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
            sa.Column("erledigt_am", sa.DateTime()),
            sa.Column("aktualisiert_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
            sa.Column("aktualisiert_am", sa.DateTime()),
        ),
    )
    _erzeuge(
        "probe_public_token",
        _basis(
            sa.Column("art", sa.String(20), nullable=False, server_default="plan"),
            sa.Column("termin_id", sa.BigInteger(), sa.ForeignKey("termin.id", ondelete="CASCADE")),
            sa.Column("jahr", sa.Integer()),
            sa.Column("filter_probeart_ids", sa.Text()),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("bezeichnung", sa.String(150)),
            sa.Column("erstellt_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("widerrufen_am", sa.DateTime()),
            sa.Column("zuletzt_genutzt_am", sa.DateTime()),
        ),
    )
    _erzeuge(
        "probe_nachbereitung",
        _basis(
            sa.Column("termin_id", sa.BigInteger(), sa.ForeignKey("termin.id", ondelete="CASCADE"), nullable=False),
            sa.Column("bemerkungen", sa.Text()),
            sa.Column("was_lief_gut", sa.Text()),
            sa.Column("verbesserungen", sa.Text()),
            sa.Column("teilnehmer_vollstaendig", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("abgeschlossen_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
            sa.Column("abgeschlossen_am", sa.DateTime()),
        ),
        sa.UniqueConstraint("org_id", "termin_id", name="uq_probe_nachbereitung_termin"),
    )
    _erzeuge(
        "probe_erkenntnis",
        _basis(
            sa.Column("termin_id", sa.BigInteger(), sa.ForeignKey("termin.id", ondelete="CASCADE"), nullable=False),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("kategorie", sa.String(30), nullable=False, server_default="allgemein"),
            sa.Column("massnahme_text", sa.Text()),
            sa.Column("massnahme_erledigt", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("sortierung", sa.Integer(), nullable=False, server_default="0"),
        ),
    )
    _erzeuge(
        "probe_change",
        _basis(
            sa.Column("termin_id", sa.BigInteger(), sa.ForeignKey("termin.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ts", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("action", sa.String(60), nullable=False, server_default=""),
            sa.Column("bereich", sa.String(30)),
            sa.Column("feld", sa.String(60)),
            sa.Column("before_json", sa.Text()),
            sa.Column("after_json", sa.Text()),
            sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
            sa.Column("ip", sa.String(45)),
        ),
    )


def _bestehende_tabellen() -> None:
    termin = [
        sa.Column("probeart_id", sa.BigInteger(), sa.ForeignKey("probeart.id", ondelete="SET NULL")),
        sa.Column("thema", sa.String(200)),
        sa.Column("objekt", sa.String(200)),
        sa.Column("objekt_id", sa.BigInteger(), sa.ForeignKey("objekt.id", ondelete="SET NULL")),
        sa.Column("info", sa.Text()),
        sa.Column("interne_bemerkung", sa.Text()),
        sa.Column("verantwortlich_member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="SET NULL")),
        sa.Column("unterstuetzung_member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="SET NULL")),
        sa.Column("alarmtext", sa.Text()),
        sa.Column("besondere_gefahren", sa.Text()),
        sa.Column("besondere_hinweise", sa.Text()),
        sa.Column("public_sichtbar", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("public_ort_sichtbar", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("public_info_sichtbar", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ics_uid", sa.String(80)),
        sa.Column("ics_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("geaendert_am", sa.DateTime()),
        sa.Column("exercise_incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="SET NULL")),
        sa.Column("archiviert_am", sa.DateTime()),
        sa.Column("vorbereitung_uebersteuert_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("vorbereitung_uebersteuert_am", sa.DateTime()),
        sa.Column("vorbereitung_uebersteuert_grund", sa.Text()),
    ]
    for column in termin:
        if column.name not in _spalten("termin"):
            with op.batch_alter_table("termin") as batch:
                batch.add_column(column)
    # Native Enum zuerst auf String erweitern, danach Altdaten abbilden.
    status_type = next(
        (c["type"] for c in sa.inspect(op.get_bind()).get_columns("termin") if c["name"] == "status"), None
    )
    if not isinstance(status_type, sa.String) or isinstance(status_type, sa.Enum):
        with op.batch_alter_table("termin") as batch:
            batch.alter_column("status", existing_type=status_type, type_=sa.String(30), existing_nullable=False)
    op.execute(sa.text("UPDATE termin SET status='durchfuehrung_laeuft' WHERE status='laufend'"))
    if "ics_uid" in _spalten("termin"):
        rows = op.get_bind().execute(sa.text("SELECT id FROM termin WHERE ics_uid IS NULL OR ics_uid='' ")).fetchall()
        for row in rows:
            op.get_bind().execute(
                sa.text("UPDATE termin SET ics_uid=:uid WHERE id=:id"), {"uid": uuid.uuid4().hex, "id": row[0]}
            )
        if "uq_termin_ics_uid" not in _indizes("termin"):
            op.create_index("uq_termin_ics_uid", "termin", ["ics_uid"], unique=True)
    for column in [
        sa.Column("status", sa.String(15), nullable=False, server_default="nicht_erfasst"),
        sa.Column("gekommen_um", sa.DateTime()),
        sa.Column("gegangen_um", sa.DateTime()),
    ]:
        if column.name not in _spalten("teilnahme"):
            with op.batch_alter_table("teilnahme") as batch:
                batch.add_column(column)
    op.execute(
        sa.text(
            "UPDATE teilnahme SET status=CASE WHEN ausgerueckt=1 THEN 'anwesend' "
            "WHEN entschuldigt=1 THEN 'entschuldigt' ELSE 'nicht_erfasst' END"
        )
    )
    org_columns = {
        "probenplanung_modul_aktiv": sa.Column(
            "probenplanung_modul_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        "probenplanung_public_aktiv": sa.Column(
            "probenplanung_public_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        "uebung_push_erlaubt": sa.Column(
            "uebung_push_erlaubt", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        "uebung_ws_alarm_erlaubt": sa.Column(
            "uebung_ws_alarm_erlaubt", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        "uebung_nachbar_einladung_erlaubt": sa.Column(
            "uebung_nachbar_einladung_erlaubt", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        "uebung_lis_status_erlaubt": sa.Column(
            "uebung_lis_status_erlaubt", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        "uebung_wordpress_bericht_erlaubt": sa.Column(
            "uebung_wordpress_bericht_erlaubt", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        "uebung_geocoding_erlaubt": sa.Column(
            "uebung_geocoding_erlaubt", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    }
    for name, column in org_columns.items():
        if name not in _spalten("org_settings"):
            with op.batch_alter_table("org_settings") as batch:
                batch.add_column(column)


def _indizes_anlegen() -> None:
    for table, name, columns in (
        ("probeart", "ix_probeart_org_sortierung", ["org_id", "sortierung"]),
        (
            "probe_checklist_item",
            "ix_probe_checklist_item_org_checkliste_sort",
            ["org_id", "checkliste_id", "sortierung"],
        ),
        ("probe_checklist_item", "ix_probe_checklist_item_org_faellig_zustand", ["org_id", "faellig_am", "zustand"]),
    ):
        if name not in _indizes(table):
            op.create_index(name, table, columns)


def _seed_probearten() -> None:
    defaults = [
        ("Schulungsabend", "SCH", "#2563eb", "uebung"),
        ("Infoabend", "INFO", "#0891b2", "veranstaltung"),
        ("Schwerpunktschulung", "SP", "#7c3aed", "uebung"),
        ("Vollprobe", "VP", "#dc2626", "uebung"),
        ("Schlussübung", "SU", "#ea580c", "uebung"),
        ("Dienstgradschulung", "DGS", "#4f46e5", "uebung"),
        ("Ausschusssitzung", "AS", "#475569", "veranstaltung"),
        ("Zusatzübung", "ZU", "#16a34a", "uebung"),
        ("Sonstiger Termin", "SONST", "#64748b", "veranstaltung"),
    ]
    conn = op.get_bind()
    orgs = conn.execute(sa.text("SELECT id FROM fire_dept")).fetchall()
    for org in orgs:
        for sortierung, (name, kurz, farbe, typ) in enumerate(defaults, 10):
            exists = conn.execute(
                sa.text("SELECT id FROM probeart WHERE org_id=:org AND name=:name"), {"org": org[0], "name": name}
            ).first()
            if not exists:
                conn.execute(
                    sa.text(
                        "INSERT INTO probeart "
                        "(org_id,name,kurz,farbe,sortierung,aktiv,termin_typ,checkliste_erforderlich,"
                        "teilnahme_erforderlich,nachbereitung_erforderlich,uebungseinsatz_erlaubt) "
                        "VALUES (:org,:name,:kurz,:farbe,:sort,1,:typ,0,1,0,0)"
                    ),
                    {"org": org[0], "name": name, "kurz": kurz, "farbe": farbe, "sort": sortierung, "typ": typ},
                )
        conn.execute(
            sa.text(
                "UPDATE termin SET probeart_id=(SELECT id FROM probeart "
                "WHERE probeart.org_id=termin.org_id AND name='Schulungsabend') "
                "WHERE org_id=:org AND typ='uebung' AND probeart_id IS NULL"
            ),
            {"org": org[0]},
        )
        conn.execute(
            sa.text(
                "UPDATE termin SET probeart_id=(SELECT id FROM probeart "
                "WHERE probeart.org_id=termin.org_id AND name='Sonstiger Termin') "
                "WHERE org_id=:org AND typ='veranstaltung' AND probeart_id IS NULL"
            ),
            {"org": org[0]},
        )


def upgrade() -> None:
    _neue_tabellen()
    _bestehende_tabellen()
    _indizes_anlegen()
    _seed_probearten()


def downgrade() -> None:
    # Bewusst nicht automatisch rueckwaerts: die Statusabbildung ist verlustbehaftet.
    pass
