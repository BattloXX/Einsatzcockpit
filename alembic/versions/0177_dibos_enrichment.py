"""DIBOS-Einsatzanreicherung: Org-Opt-in + Zusatzfelder am Incident

Ergaenzt:
- org_dibos_config.enrich_incidents (Boolean, Default False) — explizites
  Org-Opt-in, damit DIBOS fuer bestehende Orgs weiterhin ein reines
  Tracing/Diagnose-Feature bleibt (siehe app/services/dibos/dibos_enrich.py).
- incident.dibos_tycod / dibos_diagnose / dibos_bma_no / dibos_event_comment —
  reine Zusatzanzeige aus dem DIBOS-EventHub-Feed (GetCurrentEvents), ueber die
  Einsatznummer (lis_operation_number) zugeordnet. Niemals Teil von Matching/
  Dedup, siehe find_matching_incident().

Revision ID: 0177
Revises: 0176
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "0177"
down_revision = "0176"
branch_labels = None
depends_on = None

_INCIDENT_COLUMNS = [
    ("dibos_tycod", sa.String(10)),
    ("dibos_diagnose", sa.String(300)),
    ("dibos_bma_no", sa.String(40)),
    ("dibos_event_comment", sa.Text()),
]


def upgrade() -> None:
    bind = op.get_bind()

    dibos_cols = {c["name"] for c in sa_inspect(bind).get_columns("org_dibos_config")}
    if "enrich_incidents" not in dibos_cols:
        op.add_column(
            "org_dibos_config",
            sa.Column("enrich_incidents", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )

    incident_cols = {c["name"] for c in sa_inspect(bind).get_columns("incident")}
    for name, coltype in _INCIDENT_COLUMNS:
        if name not in incident_cols:
            op.add_column("incident", sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    incident_cols = {c["name"] for c in sa_inspect(bind).get_columns("incident")}
    for name, _ in _INCIDENT_COLUMNS:
        if name in incident_cols:
            op.drop_column("incident", name)

    dibos_cols = {c["name"] for c in sa_inspect(bind).get_columns("org_dibos_config")}
    if "enrich_incidents" in dibos_cols:
        op.drop_column("org_dibos_config", "enrich_incidents")
