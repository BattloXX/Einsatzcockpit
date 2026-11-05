"""ASUEW Phase 1: Org-Einstellungen, Archivfelder und Kontrollen."""
import sqlalchemy as sa

from alembic import op

revision = "0347"
down_revision = "0346"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.add_column(sa.Column(
            "asuew_erwartete_einsatzzeit_min", sa.Integer(), nullable=False, server_default="30"
        ))
        batch.add_column(sa.Column("asuew_rueckzug_untergrenze_bar", sa.Integer(), nullable=False, server_default="60"))
        batch.add_column(sa.Column("asuew_gelb_prozent", sa.Integer(), nullable=False, server_default="75"))
        batch.add_column(sa.Column("asuew_erholzeit_min", sa.Integer(), nullable=False, server_default="30"))
    for table in ("breathing_troop", "breathing_deployment"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("ueberwacher_name", sa.String(150), nullable=True))
            batch.add_column(sa.Column("angriffsweg", sa.String(300), nullable=True))
            batch.add_column(sa.Column("funkkanal", sa.String(50), nullable=True))
            batch.add_column(sa.Column("funk_verloren_at", sa.DateTime(), nullable=True))
            batch.add_column(sa.Column("notfall_at", sa.DateTime(), nullable=True))
            if table == "breathing_troop":
                batch.add_column(sa.Column("ueberwacher_user_id", sa.BigInteger(), nullable=True))
                batch.add_column(sa.Column("notfall_von_user_id", sa.BigInteger(), nullable=True))
                batch.create_foreign_key(
                    "fk_breathing_troop_ueberwacher_user_id", "user", ["ueberwacher_user_id"], ["id"]
                )
                batch.create_foreign_key(
                    "fk_breathing_troop_notfall_von_user_id", "user", ["notfall_von_user_id"], ["id"]
                )
    op.create_table(
        "breathing_kontrolle",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("troop_id", sa.BigInteger(), sa.ForeignKey("breathing_troop.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "deployment_id", sa.BigInteger(),
            sa.ForeignKey("breathing_deployment.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("stufe", sa.String(12), nullable=False),
        sa.Column("faellig_at", sa.DateTime(), nullable=True),
        sa.Column("erledigt_at", sa.DateTime(), nullable=False),
        sa.Column("niedrigster_druck", sa.Float(), nullable=True),
        sa.Column(
            "troop_member_id", sa.BigInteger(), sa.ForeignKey("troop_member.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("art", sa.String(20), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_breathing_kontrolle_troop_deployment", "breathing_kontrolle", ["troop_id", "deployment_id"])


def downgrade() -> None:
    op.drop_index("ix_breathing_kontrolle_troop_deployment", table_name="breathing_kontrolle")
    op.drop_table("breathing_kontrolle")
    for table in ("breathing_deployment", "breathing_troop"):
        with op.batch_alter_table(table) as batch:
            if table == "breathing_troop":
                batch.drop_constraint("fk_breathing_troop_notfall_von_user_id", type_="foreignkey")
                batch.drop_constraint("fk_breathing_troop_ueberwacher_user_id", type_="foreignkey")
                batch.drop_column("notfall_von_user_id")
                batch.drop_column("ueberwacher_user_id")
            batch.drop_column("notfall_at")
            batch.drop_column("funk_verloren_at")
            batch.drop_column("funkkanal")
            batch.drop_column("angriffsweg")
            batch.drop_column("ueberwacher_name")
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("asuew_erholzeit_min")
        batch.drop_column("asuew_gelb_prozent")
        batch.drop_column("asuew_rueckzug_untergrenze_bar")
        batch.drop_column("asuew_erwartete_einsatzzeit_min")
