"""verleih_geraetetyp – Geraetetypen, Artikel-FK, Stueckliste-FK, eindeutige Artikelnr

Revision ID: 0096
Revises: 0095
Create Date: 2026-06-22
"""
from alembic import op
from sqlalchemy import text

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Neue Tabelle fuer Geraetetypen
    conn.execute(text("""
        CREATE TABLE verleih_geraetetyp (
            id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
            org_id      BIGINT       NOT NULL,
            name        VARCHAR(200) NOT NULL,
            beschreibung TEXT         NULL,
            aktiv       TINYINT(1)   NOT NULL DEFAULT 1,
            created_at  DATETIME     NOT NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX ix_verleih_geraetetyp_org ON verleih_geraetetyp(org_id)"
    ))

    # 2. FK-Spalte in verleih_artikel
    conn.execute(text("""
        ALTER TABLE verleih_artikel
            ADD COLUMN geraetetyp_id BIGINT NULL
    """))

    # 3. FK-Spalte in verleih_stueckliste_position
    conn.execute(text("""
        ALTER TABLE verleih_stueckliste_position
            ADD COLUMN geraetetyp_id BIGINT NULL
    """))

    # Eindeutigkeit der Artikelnr wird nur in der Applikationsschicht geprueft
    # (aktive Artikel pro Org). MySQL unterstuetzt keinen partiellen UNIQUE INDEX
    # mit WHERE-Klausel, daher kein DB-Constraint hier.


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE verleih_stueckliste_position DROP COLUMN geraetetyp_id"))
    conn.execute(text("ALTER TABLE verleih_artikel DROP COLUMN geraetetyp_id"))
    conn.execute(text("DROP TABLE verleih_geraetetyp"))
