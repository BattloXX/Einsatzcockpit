"""PR 12: Funkjournal für Normaleinsatz (incident_comm_log)

Revision ID: 0059
Revises: 0058
Create Date: 2026-06-12 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS incident_comm_log (
            id               BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
            incident_id      BIGINT       NOT NULL,
            ts               DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            direction        VARCHAR(4)   NOT NULL,
            channel          VARCHAR(40)  NULL,
            partner          VARCHAR(120) NULL,
            message          TEXT         NOT NULL,
            is_request       TINYINT(1)   NOT NULL DEFAULT 0,
            is_lage_relevant TINYINT(1)   NOT NULL DEFAULT 0,
            handled          TINYINT(1)   NOT NULL DEFAULT 0,
            user_id          BIGINT       NULL,
            author_name      VARCHAR(120) NULL,
            INDEX idx_icl_incident (incident_id),
            CONSTRAINT fk_icl_incident FOREIGN KEY (incident_id)
                REFERENCES incident(id) ON DELETE CASCADE,
            CONSTRAINT fk_icl_user FOREIGN KEY (user_id)
                REFERENCES user(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade():
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS incident_comm_log"))
