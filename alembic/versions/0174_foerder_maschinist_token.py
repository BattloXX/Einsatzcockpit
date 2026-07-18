"""PR 6: Maschinisten-Token für Förderstrecken (login-freie Zettel-Seite)

Revision ID: 0174
Revises: 0173
Create Date: 2026-07-18
"""
from sqlalchemy import text

from alembic import op

revision = "0174"
down_revision = "0173"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE foerder_maschinist_token (
            id                 BIGINT       NOT NULL AUTO_INCREMENT,
            org_id             BIGINT       NULL,
            strecke_id         BIGINT       NOT NULL,
            token_hash         VARCHAR(64)  NOT NULL,
            erstellt_am        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            widerrufen_am      DATETIME     NULL,
            zuletzt_genutzt_am DATETIME     NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_foerder_maschinist_token_hash (token_hash),
            KEY ix_foerder_maschinist_token_hash (token_hash),
            CONSTRAINT fk_foerder_maschinist_token_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_maschinist_token_strecke FOREIGN KEY (strecke_id)
                REFERENCES foerderstrecke (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS foerder_maschinist_token"))
