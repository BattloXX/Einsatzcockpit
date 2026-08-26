"""Tests fuer Statuszaehlung, Tenant-Scope und Push-Claim der GSL-Live-Anzeige."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import IncidentSite, MajorIncident, MajorIncidentStatus, SitePhase
from app.models.master import FireDept
from app.services.gsl_live_notify import _claim_push
from app.services.gsl_live_service import build_gsl_live_payload, build_gsl_live_state, format_counts


def _lage(db, *, status=MajorIncidentStatus.active):
    suffix = uuid.uuid4().hex[:10]
    org = FireDept(slug=f"gsl-live-{suffix}", name="GSL Live Test")
    db.add(org)
    db.flush()
    lage = MajorIncident(org_id=org.id, name="Hochwasser", status=status,
                         started_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(lage)
    db.flush()
    return org, lage


def test_payload_gruppiert_und_schliesst_abgebrochen_aus():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org, lage = _lage(db)
        phases = (SitePhase.eingegangen, SitePhase.erkundung, SitePhase.in_arbeit,
                  SitePhase.erledigt, SitePhase.abgebrochen)
        for phase in phases:
            db.add(IncidentSite(major_incident_id=lage.id, org_id=org.id,
                                bezeichnung=phase.value, phase=phase))
        db.commit()
        payload = build_gsl_live_payload(db, lage)
        assert payload["counts"] == {"neu": 1, "in_arbeit": 2, "erledigt": 1, "gesamt": 4}
        assert payload["started_at"].endswith("Z")
        assert format_counts(payload["counts"]) == "1 neu · 2 in Arbeit · 1 erledigt"
    finally:
        db.close()


def test_live_state_ist_explizit_nach_org_gefiltert():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_a, lage = _lage(db)
        org_b, _ = _lage(db, status=MajorIncidentStatus.closed)
        db.commit()
        assert build_gsl_live_state(db, SimpleNamespace(org_id=org_a.id))[0]["id"] == lage.id
        assert build_gsl_live_state(db, SimpleNamespace(org_id=org_b.id)) == (None, 0)
    finally:
        db.close()


def test_claim_drosselt_counts_und_closed_umgeht_drosselung():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        _, lage = _lage(db)
        db.commit()
        assert _claim_push(db, lage, "1-0-0", "counts") is True
        assert _claim_push(db, lage, "2-0-0", "counts") is False
        lage.live_push_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=91)
        db.commit()
        assert _claim_push(db, lage, "2-0-0", "counts") is True
        assert _claim_push(db, lage, "2-0-0", "closed") is True
    finally:
        db.close()
