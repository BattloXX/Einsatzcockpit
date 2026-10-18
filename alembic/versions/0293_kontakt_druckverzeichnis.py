"""Kontaktkennzeichnungen, Kategoriepflege und persönliche Druckprofile."""

import sqlalchemy as sa

from alembic import op

revision = "0293"
down_revision = "0292"
branch_labels = None
depends_on = None

KATEGORIEN = [
    ("einsatz", "Feuerwehr & Einsatzorganisationen", True, False),
    ("behoerden", "Gemeinde & Behörden", True, False),
    ("infrastruktur", "Infrastruktur & Versorger", True, False),
    ("dienstleister", "Dienstleister & Unterstützung", False, False),
    ("einrichtungen", "Wichtige Einrichtungen", False, False),
    ("intern", "Feuerwehr intern", False, False),
    ("objekt", "Objektkontakte", False, True),
    ("sonstige", "Sonstige", False, False),
]


def upgrade():
    for column in (
        sa.Column("einsatzrelevant", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("druck_freigegeben", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("druck_prioritaet", sa.String(10), nullable=False, server_default="normal"),
        sa.Column("druckhinweis", sa.String(500)),
        sa.Column("druck_sortierung", sa.Integer(), nullable=False, server_default="0"),
    ):
        op.add_column("kontakt", column)
    for column in (
        sa.Column("schluessel", sa.String(30)),
        sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sortierung", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("druck_standard", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("objektkontakte", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        op.add_column("kontakt_kategorie", column)
    op.create_table(
        "kontakt_druckprofil",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("einstellungen_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("org_id", "user_id", "name", name="uq_kontakt_druckprofil_name"),
    )
    conn = op.get_bind()
    category = sa.table(
        "kontakt_kategorie",
        sa.column("id"),
        sa.column("org_id"),
        sa.column("name"),
        sa.column("schluessel"),
        sa.column("sortierung"),
        sa.column("druck_standard"),
        sa.column("objektkontakte"),
    )
    for org_id in conn.execute(sa.text("SELECT id FROM fire_dept")).scalars():
        for index, (key, name, standard, objekt) in enumerate(KATEGORIEN):
            existing = conn.execute(
                sa.select(category.c.id).where(category.c.org_id == org_id, category.c.name == name)
            ).first()
            values = dict(schluessel=key, sortierung=index * 10, druck_standard=standard, objektkontakte=objekt)
            if existing:
                conn.execute(category.update().where(category.c.id == existing.id).values(**values))
            else:
                conn.execute(category.insert().values(org_id=org_id, name=name, **values))


def downgrade():
    op.drop_table("kontakt_druckprofil")
    # Kategorien und bestehende Zuordnungen werden bewusst erhalten.
    for table, columns in (
        ("kontakt", ["einsatzrelevant", "druck_freigegeben", "druck_prioritaet", "druckhinweis", "druck_sortierung"]),
        ("kontakt_kategorie", ["schluessel", "aktiv", "sortierung", "druck_standard", "objektkontakte"]),
    ):
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.drop_column(column)
