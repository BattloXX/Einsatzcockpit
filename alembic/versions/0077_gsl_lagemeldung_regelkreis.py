"""GSL-Lagemeldungs-Regelkreis: OrgSettings-Intervalle, Site-Timer, Auto-Auftrag-Kennzeichnung.

Revision ID: 0077
Revises: 0076
Create Date: 2026-06-16
"""
from sqlalchemy import text

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── OrgSettings: Lagemeldungs-Intervalle + Auto-Auftrag-Schalter ──────────
    conn.execute(text("""
        ALTER TABLE org_settings
            ADD COLUMN gsl_lagemeldung_interval_minutes INTEGER NULL DEFAULT 60,
            ADD COLUMN gsl_lagemeldung_interval_sofort_minutes INTEGER NULL DEFAULT 30,
            ADD COLUMN gsl_lagemeldung_auto_auftrag TINYINT(1) NOT NULL DEFAULT 1
    """))

    # ── IncidentSite: Timer für nächste fällige Lagemeldung ───────────────────
    conn.execute(text("""
        ALTER TABLE incident_site
            ADD COLUMN naechste_lagemeldung_at DATETIME NULL
    """))
    conn.execute(text(
        "CREATE INDEX ix_incident_site_naechste_lagemeldung_at "
        "ON incident_site (naechste_lagemeldung_at)"
    ))

    # ── CommLogEntry: Kennzeichnung automatisch erzeugter Aufträge ────────────
    conn.execute(text("""
        ALTER TABLE comm_log_entry
            ADD COLUMN auto_kind VARCHAR(24) NULL
    """))

    # ── Backfill: laufende Einsätze mit aktiver Ressource bekommen einen Timer ─
    # Nur Lagen mit Status 'active', Phase 'in_arbeit', ≥1 nicht freigegebene
    # Ressource und konfiguriertem Org-Default-Intervall.
    conn.execute(text("""
        UPDATE incident_site s
        JOIN major_incident mi ON mi.id = s.major_incident_id AND mi.status = 'active'
        JOIN org_settings os ON os.org_id = s.org_id
                            AND os.gsl_lagemeldung_interval_minutes IS NOT NULL
        SET s.naechste_lagemeldung_at =
                DATE_ADD(UTC_TIMESTAMP(), INTERVAL os.gsl_lagemeldung_interval_minutes MINUTE)
        WHERE s.phase = 'in_arbeit'
          AND EXISTS (
              SELECT 1 FROM site_resource_assignment ra
              WHERE ra.incident_site_id = s.id AND ra.released_at IS NULL
          )
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP INDEX ix_incident_site_naechste_lagemeldung_at ON incident_site"))
    conn.execute(text("ALTER TABLE incident_site DROP COLUMN naechste_lagemeldung_at"))
    conn.execute(text("ALTER TABLE comm_log_entry DROP COLUMN auto_kind"))
    conn.execute(text("""
        ALTER TABLE org_settings
            DROP COLUMN gsl_lagemeldung_interval_minutes,
            DROP COLUMN gsl_lagemeldung_interval_sofort_minutes,
            DROP COLUMN gsl_lagemeldung_auto_auftrag
    """))
