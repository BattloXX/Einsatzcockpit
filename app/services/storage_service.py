"""Org-scoped storage quota: atomic reserve / release via raw SQL UPDATE.

Pattern: UPDATE … SET used_bytes = used_bytes + n WHERE used_bytes + n <= quota
rowcount == 0  →  quota exceeded  →  raise 413.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

_UNLIMITED = 2**62  # sentinel: effectively no upper bound


def _dialect(db: Session) -> str:
    return db.get_bind().dialect.name  # type: ignore[union-attr]


def _ensure_row(db: Session, org_id: int) -> None:
    """Idempotent: ensure usage row exists (dialect-aware)."""
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()
    if _dialect(db) == "sqlite":
        db.execute(
            text(
                "INSERT OR IGNORE INTO org_storage_usage (org_id, used_bytes, updated_at)"
                " VALUES (:org_id, 0, :now)"
            ),
            {"org_id": org_id, "now": now},
        )
    else:
        db.execute(
            text(
                "INSERT IGNORE INTO org_storage_usage (org_id, used_bytes, updated_at)"
                " VALUES (:org_id, 0, NOW())"
            ),
            {"org_id": org_id},
        )


def _quota_for_org(db: Session, org_id: int) -> int:
    from app.models.master import FireDept
    org = db.get(FireDept, org_id)
    if org and org.storage_quota_bytes is not None:
        return org.storage_quota_bytes
    return _UNLIMITED


def reserve_storage(db: Session, org_id: int, n_bytes: int) -> None:
    """Atomically add n_bytes to usage counter; raise 413 if quota exceeded."""
    if org_id is None or n_bytes <= 0:
        return
    _ensure_row(db, org_id)
    quota = _quota_for_org(db, org_id)
    if _dialect(db) == "sqlite":
        from datetime import UTC, datetime
        result = db.execute(
            text(
                "UPDATE org_storage_usage"
                " SET used_bytes = used_bytes + :n, updated_at = :now"
                " WHERE org_id = :org_id"
                "   AND used_bytes + :n <= :quota"
            ),
            {"n": n_bytes, "org_id": org_id, "quota": quota,
             "now": datetime.now(UTC).isoformat()},
        )
    else:
        result = db.execute(
            text(
                "UPDATE org_storage_usage"
                " SET used_bytes = used_bytes + :n, updated_at = NOW()"
                " WHERE org_id = :org_id"
                "   AND used_bytes + :n <= :quota"
            ),
            {"n": n_bytes, "org_id": org_id, "quota": quota},
        )
    if result.rowcount != 1:
        raise HTTPException(status_code=413, detail="Speicher-Quota der Organisation erschöpft.")


def release_storage(db: Session, org_id: int, n_bytes: int) -> None:
    """Atomically subtract n_bytes (floor at 0); silent if row missing."""
    if org_id is None or n_bytes <= 0:
        return
    _ensure_row(db, org_id)
    if _dialect(db) == "sqlite":
        from datetime import UTC, datetime
        db.execute(
            text(
                "UPDATE org_storage_usage"
                " SET used_bytes = CASE WHEN used_bytes - :n < 0 THEN 0"
                "                       ELSE used_bytes - :n END,"
                "     updated_at = :now"
                " WHERE org_id = :org_id"
            ),
            {"n": n_bytes, "org_id": org_id, "now": datetime.now(UTC).isoformat()},
        )
    else:
        db.execute(
            text(
                "UPDATE org_storage_usage"
                " SET used_bytes = GREATEST(0, used_bytes - :n), updated_at = NOW()"
                " WHERE org_id = :org_id"
            ),
            {"n": n_bytes, "org_id": org_id},
        )


def get_org_storage_info(db: Session, org_id: int) -> dict:
    """Returns {'used_bytes': int, 'quota_bytes': int | None}."""
    _ensure_row(db, org_id)
    from app.models.master import FireDept, OrgStorageUsage
    row = db.get(OrgStorageUsage, org_id)
    org = db.get(FireDept, org_id)
    return {
        "used_bytes": row.used_bytes if row else 0,
        "quota_bytes": org.storage_quota_bytes if org else None,
    }


def reconcile_storage(db: Session, org_id: int) -> int:
    """Recompute used_bytes from all media tables. Returns new used_bytes."""
    _ensure_row(db, org_id)
    result = db.execute(
        text(
            "SELECT COALESCE(SUM(b), 0) FROM ("
            "  SELECT bytes AS b FROM task_media"
            "   WHERE incident_id IN (SELECT id FROM incident WHERE primary_org_id = :oid)"
            "  UNION ALL"
            "  SELECT bytes FROM message_media"
            "   WHERE incident_id IN (SELECT id FROM incident WHERE primary_org_id = :oid)"
            "  UNION ALL"
            "  SELECT bytes FROM person_media"
            "   WHERE incident_id IN (SELECT id FROM incident WHERE primary_org_id = :oid)"
            "  UNION ALL"
            "  SELECT COALESCE(bytes, 0) FROM site_media"
            "   WHERE incident_site_id IN ("
            "     SELECT id FROM incident_site WHERE major_incident_id IN ("
            "       SELECT id FROM major_incident WHERE org_id = :oid"
            "     )"
            "   )"
            ") AS t"
        ),
        {"oid": org_id},
    )
    total = int(result.scalar() or 0)
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat() if _dialect(db) == "sqlite" else None
    if now:
        db.execute(
            text("UPDATE org_storage_usage SET used_bytes = :total, updated_at = :now WHERE org_id = :oid"),
            {"total": total, "now": now, "oid": org_id},
        )
    else:
        db.execute(
            text("UPDATE org_storage_usage SET used_bytes = :total, updated_at = NOW() WHERE org_id = :oid"),
            {"total": total, "oid": org_id},
        )
    return total
