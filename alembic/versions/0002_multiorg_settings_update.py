"""multi-org, settings, system_admin, port 8092 compat

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-22 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── fire_dept: neue Organisations-Felder ─────────────────────────────────
    # IF NOT EXISTS guards against partial previous migration runs
    op.execute("ALTER TABLE fire_dept ADD COLUMN IF NOT EXISTS is_home_org BOOL NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE fire_dept ADD COLUMN IF NOT EXISTS is_active BOOL NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE fire_dept ADD COLUMN IF NOT EXISTS logo_path VARCHAR(500)")
    op.execute("ALTER TABLE fire_dept ADD COLUMN IF NOT EXISTS contact_email VARCHAR(200)")
    op.execute("ALTER TABLE fire_dept ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(50)")
    op.execute("ALTER TABLE fire_dept ADD COLUMN IF NOT EXISTS street VARCHAR(200)")
    op.execute("ALTER TABLE fire_dept ADD COLUMN IF NOT EXISTS city VARCHAR(100)")
    # Wolfurt als Home-Org markieren
    op.execute("UPDATE fire_dept SET is_home_org = 1 WHERE slug = 'wolfurt'")

    # ── user: org_id → BIGINT (muss fire_dept.id BIGINT matchen) ─────────────
    # ADD IF NOT EXISTS + MODIFY stellt sicher dass der Typ stimmt,
    # auch wenn ein vorheriger Lauf die Spalte bereits als INT angelegt hat.
    op.execute("ALTER TABLE `user` ADD COLUMN IF NOT EXISTS org_id BIGINT NULL")
    op.execute("ALTER TABLE `user` MODIFY COLUMN org_id BIGINT NULL")
    op.execute("ALTER TABLE `user` DROP FOREIGN KEY IF EXISTS fk_user_org_id")
    op.execute(
        "ALTER TABLE `user` ADD CONSTRAINT fk_user_org_id "
        "FOREIGN KEY (org_id) REFERENCES fire_dept(id) ON DELETE SET NULL"
    )

    # ── api_key: org_id ───────────────────────────────────────────────────────
    op.add_column("api_key", sa.Column("org_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_api_key_org_id", "api_key", "fire_dept", ["org_id"], ["id"],
                          ondelete="SET NULL")

    # ── member: org_id ────────────────────────────────────────────────────────
    op.add_column("member", sa.Column("org_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_member_org_id", "member", "fire_dept", ["org_id"], ["id"],
                          ondelete="SET NULL")

    # ── incident: primary_org_id ──────────────────────────────────────────────
    op.add_column("incident", sa.Column("primary_org_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_incident_primary_org", "incident", "fire_dept",
                          ["primary_org_id"], ["id"], ondelete="SET NULL")

    # ── incident_org (multi-org collaboration) ────────────────────────────────
    op.create_table(
        "incident_org",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="collaborator"),
        sa.Column("joined_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("added_by_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incident.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )

    # ── org_settings ──────────────────────────────────────────────────────────
    op.create_table(
        "org_settings",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("logo_path", sa.String(500), nullable=True),
        sa.Column("primary_color", sa.String(7), nullable=True),
        sa.Column("footer_text", sa.String(500), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )

    # ── system_settings ───────────────────────────────────────────────────────
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )

    # ── roles: system_admin + org_admin hinzufügen ────────────────────────────
    op.execute(
        "INSERT IGNORE INTO role (code, label) VALUES "
        "('system_admin', 'Systemadministrator (organisationsübergreifend)'), "
        "('org_admin', 'Organisations-Administrator')"
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_table("org_settings")
    op.drop_table("incident_org")
    op.drop_constraint("fk_incident_primary_org", "incident", type_="foreignkey")
    op.drop_column("incident", "primary_org_id")
    op.drop_constraint("fk_member_org_id", "member", type_="foreignkey")
    op.drop_column("member", "org_id")
    op.drop_constraint("fk_api_key_org_id", "api_key", type_="foreignkey")
    op.drop_column("api_key", "org_id")
    op.drop_constraint("fk_user_org_id", "user", type_="foreignkey")
    op.drop_column("user", "org_id")
    for col in ["is_home_org", "is_active", "logo_path", "contact_email",
                "contact_phone", "street", "city"]:
        op.drop_column("fire_dept", col)
    op.execute("DELETE FROM role WHERE code IN ('system_admin', 'org_admin')")
