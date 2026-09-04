"""Granulare Aenderungshistorie fuer Proben."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.probenplanung import ProbeChange


def write_probe_change(
    db: Session,
    termin_id: int,
    action: str,
    bereich: str | None,
    feld: str | None,
    before: dict | None,
    after: dict | None,
    *,
    user_id: int | None = None,
    ip: str | None = None,
    ts: datetime | None = None,
) -> None:
    """Schreibt einen ProbeChange; der Aufrufer commitet die Transaktion."""
    db.add(
        ProbeChange(
            termin_id=termin_id,
            action=action,
            bereich=bereich,
            feld=feld,
            before_json=json.dumps(before, ensure_ascii=False, default=str) if before else None,
            after_json=json.dumps(after, ensure_ascii=False, default=str) if after else None,
            user_id=user_id,
            ip=ip,
            ts=ts if ts is not None else datetime.now(UTC),
        )
    )
