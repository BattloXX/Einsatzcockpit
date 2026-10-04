"""Whitelisted DTOs fuer den read-only Einsatz-Feed."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

FORBIDDEN_FIELDS = frozenset({
    "caller_name", "caller_phone", "report_text", "reason", "tokens", "access_pin_hash",
    "alarm_token", "auto_geojson_token", "member_names", "urls",
})


class FeedObjektBasis(BaseModel):
    id: int
    name: str


class FeedEinsatzBasis(BaseModel):
    id: int
    nummer: int | None
    alarm_type_code: str
    status: str
    is_exercise: bool
    phase: str
    started_at: str
    closed_at: str | None
    taken_over_at: str | None
    departed_at: str | None
    on_scene_at: str | None
    ready_again_at: str | None
    address_street: str | None
    address_no: str | None
    address_city: str | None
    lat: float | None
    lng: float | None
    objekt: FeedObjektBasis | None
    unit_count: int
    timezone: str


class FeedEinsatzListe(BaseModel):
    schema_version: Literal[1] = 1
    server_time: str
    einsaetze: list[FeedEinsatzBasis]
