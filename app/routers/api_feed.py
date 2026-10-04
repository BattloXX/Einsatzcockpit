"""Read-only Einsatz-Pull-Feed (Basisumfang)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core.dependencies import require_feed_scope
from app.core.rate_limit import get_api_key_identifier
from app.core.rate_limit import limiter as _limiter
from app.db import get_db
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.objekt import ObjektEinsatz
from app.models.user import ApiKey
from app.routers.api_v1 import _api_key_scoped_incidents
from app.schemas.feed import FeedEinsatzBasis, FeedEinsatzListe
from app.services.feed_service import build_feed_einsatz_basis

router = APIRouter(prefix="/api/v1/feed", tags=["feed"])

_CACHE_HEADERS = {"Cache-Control": "private, no-cache", "Vary": "X-API-Key"}


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_since(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="since muss ein ISO-8601-Zeitpunkt sein") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _parse_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="limit muss eine ganze Zahl zwischen 1 und 200 sein") from exc
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit muss zwischen 1 und 200 liegen")
    return limit


def _filtered_incidents(
    db: Session, api_key: ApiKey, *, status: str, since: datetime | None,
    include_exercises: bool,
):
    query = _api_key_scoped_incidents(db, api_key)
    if status != "all":
        query = query.filter(Incident.status == status)
    if since is not None:
        query = query.filter((Incident.started_at >= since) | (Incident.closed_at >= since))
    if not include_exercises:
        query = query.filter(Incident.is_exercise.is_(False))
    return query


def _etag(rows, *, route: str, status: str, since: datetime | None, limit: int | None,
          include_exercises: bool, scopes: str, incident_id: int | None = None) -> str:
    source = {
        "schema_version": 1,
        "route": route,
        "incident_id": incident_id,
        "status": status,
        "since": _utc_iso(since) if since is not None else None,
        "limit": limit,
        "include_exercises": include_exercises,
        "scopes": sorted(scope for scope in scopes.split(",") if scope),
        "incidents": [(row.id, row.feed_rev, row.status) for row in rows],
    }
    return sha256(json.dumps(source, separators=(",", ":"), sort_keys=True).encode()).hexdigest()[:32]


def _not_modified(request: Request, etag: str) -> bool:
    value = request.headers.get("If-None-Match", "")
    if value.strip() == "*":
        return True
    candidates = [item.strip().removeprefix("W/") for item in value.split(",")]
    return f'"{etag}"' in candidates


def _feed_response(request: Request, etag: str, payload: object) -> Response:
    headers = {**_CACHE_HEADERS, "ETag": f'"{etag}"'}
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=payload, headers=headers)


@router.get("/einsaetze", response_model=FeedEinsatzListe)
@(_limiter.limit(lambda: settings.FEED_RATELIMIT, key_func=get_api_key_identifier) if _limiter else lambda f: f)
def list_einsaetze(
    request: Request,
    status: str = Query("active"),
    since: str | None = Query(None),
    limit: str = Query("50"),
    include_exercises: bool = Query(False),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_feed_scope("einsatz:read")),
):
    if status not in {"active", "closed", "all"}:
        raise HTTPException(status_code=422, detail="status muss active, closed oder all sein")
    parsed_since = _parse_since(since)
    parsed_limit = _parse_limit(limit)
    query = _filtered_incidents(
        db, api_key, status=status, since=parsed_since, include_exercises=include_exercises,
    )
    rev_rows = query.with_entities(Incident.id, Incident.feed_rev, Incident.status).order_by(
        Incident.started_at.desc(), Incident.id.desc()
    ).limit(parsed_limit).all()
    etag = _etag(rev_rows, route="list", status=status, since=parsed_since, limit=parsed_limit,
                 include_exercises=include_exercises, scopes=api_key.scopes)
    if _not_modified(request, etag):
        return _feed_response(request, etag, None)
    incidents = query.options(
        selectinload(Incident.vehicles),
        selectinload(Incident.objekt_links).selectinload(ObjektEinsatz.objekt),
    ).order_by(Incident.started_at.desc(), Incident.id.desc()).limit(parsed_limit).all()
    org = db.get(FireDept, api_key.org_id)
    assert org is not None
    payload = FeedEinsatzListe(
        server_time=_utc_iso(datetime.now(UTC)),
        einsaetze=[build_feed_einsatz_basis(incident, org) for incident in incidents],
    ).model_dump()
    return _feed_response(request, etag, payload)


@router.get("/einsaetze/{incident_id}", response_model=FeedEinsatzBasis)
@(_limiter.limit(lambda: settings.FEED_RATELIMIT, key_func=get_api_key_identifier) if _limiter else lambda f: f)
def get_einsatz(
    incident_id: int,
    request: Request,
    include_exercises: bool = Query(False),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_feed_scope("einsatz:read")),
):
    query = _filtered_incidents(
        db, api_key, status="all", since=None, include_exercises=include_exercises,
    ).filter(Incident.id == incident_id)
    rev_rows = query.with_entities(Incident.id, Incident.feed_rev, Incident.status).all()
    if not rev_rows:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")
    etag = _etag(rev_rows, route="detail", status="all", since=None, limit=None,
                 include_exercises=include_exercises, scopes=api_key.scopes, incident_id=incident_id)
    if _not_modified(request, etag):
        return _feed_response(request, etag, None)
    incident = query.options(
        selectinload(Incident.vehicles),
        selectinload(Incident.objekt_links).selectinload(ObjektEinsatz.objekt),
    ).one()
    org = db.get(FireDept, api_key.org_id)
    assert org is not None
    return _feed_response(request, etag, build_feed_einsatz_basis(incident, org).model_dump())


@router.get("/head")
@(_limiter.limit(lambda: settings.FEED_RATELIMIT, key_func=get_api_key_identifier) if _limiter else lambda f: f)
def feed_head(
    request: Request,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_feed_scope("einsatz:read")),
):
    query = _filtered_incidents(db, api_key, status="active", since=None, include_exercises=False)
    rev_rows = query.with_entities(Incident.id, Incident.feed_rev, Incident.status).order_by(
        Incident.started_at.desc(), Incident.id.desc()
    ).limit(50).all()
    etag = _etag(rev_rows, route="list", status="active", since=None, limit=50,
                 include_exercises=False, scopes=api_key.scopes)
    active_count = query.count()
    return _feed_response(request, etag, {
        "rev": etag,
        "server_time": _utc_iso(datetime.now(UTC)),
        "active_count": active_count,
        "schema_version": 1,
    })
