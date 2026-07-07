"""Resolver + Persistenz fuer die Bild-Annotation (media_typ-agnostisch).

Kapselt den Zugriff auf die sechs Media-Typen hinter einer Registry, sodass
Editor/Save/Serve typunabhaengig arbeiten. In PR1 ist nur `task` verdrahtet;
weitere Typen werden in spaeteren PRs ergaenzt (Registry-Eintrag genuegt).
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.core.permissions import has_role
from app.models.media_annotation import MediaAnnotation, MediaAnnotationVersion
from app.models.user import User
from app.services.media_service import _storage_root, absolute_path


@dataclass(frozen=True)
class MediaSpec:
    typ: str
    model: type
    # media-Objekt -> Original-Bild-URL (Editor-Hintergrund / Anzeige)
    bild_url: Callable[[object], str]
    # media-Objekt -> zugehoerige incident_id (fuer Zugriffspruefung); None wenn n/a
    incident_id: Callable[[object], int | None]


def _task_bild_url(m) -> str:  # type: ignore[no-untyped-def]
    return f"/medien/datei/{m.id}"


# Registry: pro media_typ ein Spec. PR1 = nur "task".
def _build_registry() -> dict[str, MediaSpec]:
    from app.models.incident import TaskMedia
    return {
        "task": MediaSpec("task", TaskMedia, _task_bild_url, lambda m: m.incident_id),
    }


_REGISTRY: dict[str, MediaSpec] | None = None


def registry() -> dict[str, MediaSpec]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def spec_for(media_typ: str) -> MediaSpec | None:
    return registry().get(media_typ)


def resolve_media(db: Session, media_typ: str, media_id: int):  # type: ignore[no-untyped-def]
    """Media-Zeile zu (typ, id) laden oder None."""
    spec = spec_for(media_typ)
    if spec is None:
        return None
    return db.get(spec.model, media_id)


def _incident_for(db: Session, spec: MediaSpec, media):  # type: ignore[no-untyped-def]
    from app.models.incident import Incident
    inc_id = spec.incident_id(media)
    return db.get(Incident, inc_id) if inc_id else None


def _may_access_incident(user: User, incident) -> bool:  # type: ignore[no-untyped-def]
    """Muster ui_media._user_may_access_incident (hier inline, um Router-Import
    zu vermeiden)."""
    if incident is None:
        return False
    if has_role(user, "system_admin"):
        return True
    if user.org_id and incident.primary_org_id == user.org_id:
        return True
    return any(io.org_id == user.org_id for io in (incident.collaborating_orgs or []))


def can_read(db: Session, user: User, media_typ: str, media) -> bool:  # type: ignore[no-untyped-def]
    spec = spec_for(media_typ)
    if spec is None or media is None:
        return False
    return _may_access_incident(user, _incident_for(db, spec, media))


def can_write(db: Session, user: User, media_typ: str, media) -> bool:  # type: ignore[no-untyped-def]
    """Schreibrecht = Lesezugriff auf den Host + Bearbeiter-Rolle."""
    if not can_read(db, user, media_typ, media):
        return False
    return has_role(user, "incident_leader", "admin", "recorder")


def is_annotatable(media) -> bool:  # type: ignore[no-untyped-def]
    """Nur Bilder werden annotiert (keine PDFs/Videos)."""
    return getattr(media, "kind", None) == "image"


# ── Annotation-Zeile ─────────────────────────────────────────────────────────

def get_annotation(db: Session, media_typ: str, media_id: int) -> MediaAnnotation | None:
    return (
        db.query(MediaAnnotation)
        .filter(MediaAnnotation.media_typ == media_typ, MediaAnnotation.media_id == media_id)
        .first()
    )


def get_or_create(db: Session, media_typ: str, media_id: int, org_id: int | None) -> MediaAnnotation:
    ann = get_annotation(db, media_typ, media_id)
    if ann is None:
        ann = MediaAnnotation(media_typ=media_typ, media_id=media_id, org_id=org_id)
        db.add(ann)
        db.flush()
    return ann


def _annotated_abs_path(media) -> Path:  # type: ignore[no-untyped-def]
    """Pfad des flachen PNG neben dem Original (…/{stem}_annotated.png)."""
    orig = absolute_path(media)
    return orig.with_name(orig.stem + "_annotated.png")


def _annotated_rel_path(media) -> str:  # type: ignore[no-untyped-def]
    return str(_annotated_abs_path(media).relative_to(_storage_root()))


def save_annotation(
    db: Session,
    user: User,
    media_typ: str,
    media,  # type: ignore[no-untyped-def]
    annotation_json: str,
    png_data_url: str | None,
) -> MediaAnnotation:
    """Speichert Vektordaten + optional das flache PNG (Data-URL). Archiviert den
    vorherigen annotation_json-Stand in media_annotation_version."""
    ann = get_or_create(db, media_typ, media.id, getattr(media, "org_id", None))

    # Vorstand archivieren (Nachvollziehbarkeit)
    if ann.annotation_json:
        db.add(MediaAnnotationVersion(
            annotation_id=ann.id, annotation_json=ann.annotation_json, created_by=user.id,
        ))

    ann.annotation_json = annotation_json
    ann.annotated_at = datetime.now(UTC)
    ann.annotated_by = user.id

    # Flaches PNG aus Data-URL (data:image/png;base64,…) schreiben
    if png_data_url and "," in png_data_url:
        b64 = png_data_url.split(",", 1)[1]
        try:
            raw = base64.b64decode(b64)
        except (binascii.Error, ValueError):
            raw = None
        if raw:
            abs_path = _annotated_abs_path(media)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(raw)
            ann.annotated_file = _annotated_rel_path(media)

    db.flush()
    return ann


def display_abs_path(db: Session, media_typ: str, media) -> Path | None:  # type: ignore[no-untyped-def]
    """Anzuzeigende Datei: flaches annotiertes PNG falls vorhanden, sonst Original."""
    ann = get_annotation(db, media_typ, media.id)
    if ann and ann.annotated_file:
        p = _storage_root() / ann.annotated_file
        if p.exists():
            return p
    return absolute_path(media)


def annotated_media_ids(db: Session, media_typ: str, media_ids: list[int]) -> set[int]:
    """media_ids, fuer die ein flaches annotiertes PNG existiert (fuer Badge/Anzeige)."""
    if not media_ids:
        return set()
    rows = (
        db.query(MediaAnnotation.media_id)
        .filter(
            MediaAnnotation.media_typ == media_typ,
            MediaAnnotation.media_id.in_(media_ids),
            MediaAnnotation.annotated_file.isnot(None),
        )
        .all()
    )
    return {r[0] for r in rows}
