"""Alte Mindest-Alarmstufen in explizite Alarmstufen einer Druckregel ueberfuehren.

Revision ID: 0288
Revises: 0287
"""
from __future__ import annotations

import json
import re

from alembic import op
import sqlalchemy as sa

revision = "0288"
down_revision = "0287"
branch_labels = None
depends_on = None


def _json_objekt(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        wert = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return wert if isinstance(wert, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    regeln = bind.execute(sa.text("SELECT id, org_id, filters FROM print_rule")).mappings()
    for regel in regeln:
        filters = _json_objekt(regel["filters"])
        if "min_alarmstufe" not in filters or "alarmstufen" in filters:
            continue
        try:
            minimum = int(filters["min_alarmstufe"])
        except (TypeError, ValueError):
            continue
        codes = bind.execute(
            sa.text("SELECT code FROM alarm_type WHERE org_id=:org_id"),
            {"org_id": regel["org_id"]},
        ).scalars()
        passende_codes = {
            code.strip().upper() for code in codes if isinstance(code, str)
            and (treffer := re.search(r"\d+", code)) and int(treffer.group()) >= minimum
        }
        if not passende_codes:
            continue
        filters["alarmstufen"] = sorted(passende_codes)
        del filters["min_alarmstufe"]
        bind.execute(
            sa.text("UPDATE print_rule SET filters=:filters WHERE id=:id"),
            {"filters": json.dumps(filters), "id": regel["id"]},
        )


def downgrade() -> None:
    """No-op: Auswahllisten koennen nicht verlustfrei in Mindestwerte zurueck."""
