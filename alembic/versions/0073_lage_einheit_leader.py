"""LageEinheitLeader: Einheitsführer-Historie + Pointer

Revision ID: 0073
Revises: 0072
Create Date: 2026-06-14
"""
from alembic import op
from sqlalchemy import text

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS lage_einheit_leader (
            id             INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
            einheit_id     INT          NOT NULL,
            member_id      BIGINT       NULL,
            person_name    VARCHAR(120) NULL,
            start_at       DATETIME     NOT NULL,
            end_at         DATETIME     NULL,
            predecessor_id INT          NULL,
            note           TEXT         NULL,
            created_by     BIGINT       NULL,
            created_at     DATETIME     NOT NULL,
            INDEX ix_lel_einheit (einheit_id),
            CONSTRAINT fk_lel_einheit FOREIGN KEY (einheit_id)
                REFERENCES lage_einheit(id) ON DELETE CASCADE,
            CONSTRAINT fk_lel_member FOREIGN KEY (member_id)
                REFERENCES member(id) ON DELETE SET NULL,
            CONSTRAINT fk_lel_pred FOREIGN KEY (predecessor_id)
                REFERENCES lage_einheit_leader(id) ON DELETE SET NULL,
            CONSTRAINT fk_lel_user FOREIGN KEY (created_by)
                REFERENCES user(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # leader_assignment_id FK auf lage_einheit_leader (Tabelle existiert jetzt)
    conn.execute(text("""
        ALTER TABLE lage_einheit
            ADD CONSTRAINT fk_le_leader
                FOREIGN KEY (leader_assignment_id) REFERENCES lage_einheit_leader(id) ON DELETE SET NULL
    """))

    # Bestehende commander_label-Werte als initialen Leader-Eintrag übernehmen
    conn.execute(text("""
        INSERT INTO lage_einheit_leader (einheit_id, person_name, start_at, created_at)
        SELECT id, commander_label, added_at, NOW()
        FROM lage_einheit
        WHERE commander_label IS NOT NULL AND commander_label != ''
    """))

    # Pointer setzen
    conn.execute(text("""
        UPDATE lage_einheit le
        INNER JOIN lage_einheit_leader lel ON lel.einheit_id = le.id AND lel.end_at IS NULL
        SET le.leader_assignment_id = lel.id
        WHERE le.commander_label IS NOT NULL AND le.commander_label != ''
    """))


def downgrade():
    conn = op.get_bind()

    conn.execute(text("UPDATE lage_einheit SET leader_assignment_id = NULL"))
    conn.execute(text("ALTER TABLE lage_einheit DROP FOREIGN KEY fk_le_leader"))
    conn.execute(text("DROP TABLE IF EXISTS lage_einheit_leader"))
