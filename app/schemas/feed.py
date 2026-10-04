"""Whitelisted DTOs fuer den read-only Einsatz-Feed."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

FORBIDDEN_FIELDS = frozenset({
    "caller_name", "caller_phone", "report_text", "reason", "tokens", "access_pin_hash",
    "alarm_token", "auto_geojson_token", "member_names", "urls",
    "commander_name", "fahrer_name", "fahrer2_name", "incident_leader_name",
    "section_leader_name", "detail", "author_name", "status_text_raw", "km_gefahren",
    "name_of_person", "rescued", "persons", "storage_path", "original_filename",
})


class FeedObjektBasis(BaseModel):
    id: int
    name: str


class FeedKraft(BaseModel):
    id: int
    vehicle_code: str
    name: str
    type: str
    is_external: bool
    org_short: str | None
    unit_status: str
    added_at: str


class FeedWache(BaseModel):
    wache_unid: str
    wache_name: str | None
    status: str
    status_at: str | None


class FeedBoardColumn(BaseModel):
    code: str
    title: str
    column_kind: str
    display_order: int


class FeedBoardTask(BaseModel):
    id: int
    title: str
    status: str
    is_done: bool
    done_at: str | None
    is_cancelled: bool
    cancelled_at: str | None
    due_at: str | None
    created_at: str
    column_code: str | None
    vehicle_id: int | None


class FeedBoardMessage(BaseModel):
    id: int
    title: str
    status: str
    is_done: bool
    done_at: str | None
    is_cancelled: bool
    created_at: str
    column_code: str | None
    vehicle_id: int | None


class FeedBoard(BaseModel):
    columns: list[FeedBoardColumn]
    tasks: list[FeedBoardTask]
    messages: list[FeedBoardMessage]


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
    kraefte: list[FeedKraft] | None
    wachen: list[FeedWache] | None
    board: FeedBoard | None
    unit_count: int
    timezone: str


class FeedEinsatzListe(BaseModel):
    schema_version: Literal[1] = 1
    server_time: str
    einsaetze: list[FeedEinsatzBasis]
