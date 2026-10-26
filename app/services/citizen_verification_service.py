"""Org-bezogene Aufbewahrung kurzlebiger Buergerportal-Verifizierungen."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.citizen_verification import CitizenReportVerification


def purge_citizen_verifications(db: Session, org_id: int, now: datetime | None = None) -> int:
    """Abgelaufene oder verbrauchte Zeilen nach einem Tag entfernen; kein Commit."""
    reference = now or datetime.now(UTC).replace(tzinfo=None)
    cutoff = reference - timedelta(days=1)
    return db.query(CitizenReportVerification).filter(
        CitizenReportVerification.org_id == org_id,
        or_(
            CitizenReportVerification.expires_at < cutoff,
            CitizenReportVerification.consumed_at < cutoff,
        ),
    ).delete(synchronize_session=False)
