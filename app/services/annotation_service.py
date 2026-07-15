"""Resolver + Persistenz fuer die Bild-Annotation (media_typ-agnostisch).

Kapselt den Zugriff auf die sechs Media-Typen hinter einer Registry, sodass
Editor/Save/Serve/Lock typunabhaengig arbeiten:
  - Einsatz: task_media, message_media, person_media   (media_service-Schema)
  - GSL:     site_media, cross_marker_media, lage_journal_media (lage_media_service-Schema)

Das flache PNG wird IMMER neben das Original geschrieben (…/{stem}_annotated.png);
annotated_file traegt nur den Dateinamen als Marker -> unabhaengig vom Storage-Root.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.permissions import has_role
from app.models.media_annotation import MediaAnnotation, MediaAnnotationVersion
from app.models.user import User

LOCK_TTL_SEC = 300  # Soft-Lock 5 Minuten (per Heartbeat verlaengert)
_EDIT_ROLLEN = ("incident_leader", "admin", "recorder")


@dataclass(frozen=True)
class MediaSpec:
    typ: str
    model: type
    kind_of: Callable[[object], str]          # media -> 'image'|'pdf'|'video'
    # abs_path/thumb_path: Any statt object -- die zugewiesenen Funktionen (absolute_path etc.)
    # sind auf konkrete Medientypen (TaskMedia, SiteMedia, ...) typisiert; Callable-Parameter
    # sind kontravariant, ein Callable[[TaskMedia], Path] ist also kein Callable[[object], Path].
    # Zur Laufzeit ist das korrekt (jede Funktion wird nur mit ihrem eigenen Typ aufgerufen).
    abs_path: Callable[[Any], Path]           # media -> Originaldatei
    access: Callable[[Session, User, object], bool]
    thumb_path: Callable[[Any], Path | None]  # media -> Thumbnail-Datei (fuer Regenerierung)
    org_of: Callable[[Session, object], int | None]  # media -> org_id (fuer Quota-Buchung)


def _may_access_incident(user: User, incident) -> bool:  # type: ignore[no-untyped-def]
    if incident is None:
        return False
    if has_role(user, "system_admin"):
        return True
    if user.org_id and incident.primary_org_id == user.org_id:
        return True
    return any(io.org_id == user.org_id for io in (incident.collaborating_orgs or []))


def _access_incident(db: Session, user: User, media) -> bool:  # type: ignore[no-untyped-def]
    from app.models.incident import Incident
    return _may_access_incident(user, db.get(Incident, media.incident_id))


def _access_org(db: Session, user: User, media) -> bool:  # type: ignore[no-untyped-def]
    return has_role(user, "system_admin") or bool(user.org_id and media.org_id == user.org_id)


def _org_of_incident(db: Session, media) -> int | None:  # type: ignore[no-untyped-def]
    """Org-Ermittlung fuer Task/Message/Person-Medien: keine eigene org_id-Spalte,
    Org kommt ueber den verknuepften Einsatz."""
    incident_id = getattr(media, "incident_id", None)
    if incident_id is None:
        return None
    from app.models.incident import Incident
    incident = db.get(Incident, incident_id)
    return incident.primary_org_id if incident else None


def _org_of_direct(db: Session, media) -> int | None:  # type: ignore[no-untyped-def]
    """Org-Ermittlung fuer GSL-Medien: org_id steht direkt auf der Zeile."""
    return getattr(media, "org_id", None)


def _build_registry() -> dict[str, MediaSpec]:
    from app.models.incident import MessageMedia, PersonMedia, TaskMedia
    from app.models.major_incident import CrossMarkerMedia, LageJournalMedia, SiteMedia
    from app.services.lage_media_service import (
        cross_media_path,
        cross_media_thumb_path,
        journal_media_path,
        journal_thumb_path,
        site_media_path,
        site_thumb_path,
    )
    from app.services.media_service import absolute_path, absolute_thumb_path

    def kind_ec(m): return getattr(m, "kind", None)          # task/message/person
    def kind_gsl(m): return getattr(m, "media_type", None)   # site/cross/journal

    return {
        "task":         MediaSpec("task", TaskMedia, kind_ec, absolute_path,
                                   _access_incident, absolute_thumb_path, _org_of_incident),
        "message":      MediaSpec("message", MessageMedia, kind_ec, absolute_path,
                                   _access_incident, absolute_thumb_path, _org_of_incident),
        "person":       MediaSpec("person", PersonMedia, kind_ec, absolute_path,
                                   _access_incident, absolute_thumb_path, _org_of_incident),
        "site":         MediaSpec("site", SiteMedia, kind_gsl, site_media_path,
                                   _access_org, site_thumb_path, _org_of_direct),
        "cross_marker": MediaSpec("cross_marker", CrossMarkerMedia, kind_gsl, cross_media_path,
                                   _access_org, cross_media_thumb_path, _org_of_direct),
        "lage_journal": MediaSpec("lage_journal", LageJournalMedia, kind_gsl, journal_media_path,
                                   _access_org, journal_thumb_path, _org_of_direct),
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
    spec = spec_for(media_typ)
    return db.get(spec.model, media_id) if spec else None


def is_annotatable(media_typ: str, media) -> bool:  # type: ignore[no-untyped-def]
    spec = spec_for(media_typ)
    return bool(spec and media is not None and spec.kind_of(media) == "image")


def can_read(db: Session, user: User, media_typ: str, media) -> bool:  # type: ignore[no-untyped-def]
    spec = spec_for(media_typ)
    return bool(spec and media is not None and spec.access(db, user, media))


def can_write(db: Session, user: User, media_typ: str, media) -> bool:  # type: ignore[no-untyped-def]
    return can_read(db, user, media_typ, media) and has_role(user, *_EDIT_ROLLEN)


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


def original_abs_path(media_typ: str, media) -> Path:  # type: ignore[no-untyped-def]
    spec = spec_for(media_typ)
    if spec is None:
        raise ValueError(f"Unbekannter media_typ: {media_typ!r}")
    return spec.abs_path(media)


def _annotated_abs_path(media_typ: str, media) -> Path:  # type: ignore[no-untyped-def]
    orig = original_abs_path(media_typ, media)
    return orig.with_name(orig.stem + "_annotated.png")


def save_annotation(
    db: Session, user: User, media_typ: str, media,  # type: ignore[no-untyped-def]
    annotation_json: str, png_data_url: str | None,
) -> MediaAnnotation:
    """Speichert Vektordaten + optional das flache PNG (neben dem Original).
    Archiviert den vorherigen annotation_json-Stand.

    Quota: das flache PNG belegt echten Speicherplatz, der bisher nie gegen die
    Org-Quota gebucht wurde (Bug, siehe Session 2026-07-12) -- reserve/release
    laeuft ueber das Groessen-Delta zur vorherigen Annotation (Datei wird am
    selben Pfad ueberschrieben, ein zweites Speichern derselben Annotation soll
    also nicht nochmal die volle Groesse buchen). reserve_storage() wird VOR
    dem Schreiben aufgerufen, damit bei ueberschrittener Quota (413) keine
    Datei-Leiche auf der Platte zurueckbleibt."""
    spec = spec_for(media_typ)
    org_id = spec.org_of(db, media) if spec else None

    ann = get_or_create(db, media_typ, media.id, org_id)
    if org_id is not None:
        ann.org_id = org_id  # self-heilend: korrigiert historisch leere org_id (Task/Message/Person)
    if ann.annotation_json:
        db.add(MediaAnnotationVersion(
            annotation_id=ann.id, annotation_json=ann.annotation_json, created_by=user.id,
        ))
    ann.annotation_json = annotation_json
    ann.annotated_at = datetime.now(UTC)
    ann.annotated_by = user.id

    if png_data_url and "," in png_data_url:
        try:
            raw = base64.b64decode(png_data_url.split(",", 1)[1])
        except (binascii.Error, ValueError):
            raw = None
        if raw:
            abs_path = _annotated_abs_path(media_typ, media)
            old_bytes = abs_path.stat().st_size if abs_path.exists() else 0
            delta = len(raw) - old_bytes
            if org_id is not None and delta > 0:
                from app.services.storage_service import reserve_storage
                reserve_storage(db, org_id, delta)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(raw)
            ann.annotated_file = abs_path.name
            if org_id is not None and delta < 0:
                from app.services.storage_service import release_storage
                release_storage(db, org_id, -delta)
            _regenerate_thumb(media_typ, media, raw)
    db.flush()
    return ann


def _regenerate_thumb(media_typ: str, media, png_bytes: bytes) -> None:  # type: ignore[no-untyped-def]
    """Ueberschreibt das bestehende Thumbnail der Medienzeile mit einer aus dem
    geflachten Annotations-PNG neu berechneten Miniaturansicht (gleicher Dateipfad
    wie bisher, ueber die typ-spezifische thumb_path-Aufloesung der Registry -- so
    funktioniert es sowohl fuer task/message/person (thumb_path-DB-Spalte) als auch
    fuer site/cross_marker/lage_journal (Dateiname-Konvention, keine eigene Spalte).
    Ohne dieses Update zeigt /medien/thumb/{id} (und die GSL-Pendants) nach einer
    Bearbeitung weiterhin das unbearbeitete Original, da das Thumb-JPEG sonst nur
    einmal beim Upload erzeugt wird (Bug, siehe Session 2026-07-12)."""
    from app.services.media_service import regenerate_thumbnail_from_bytes

    spec = spec_for(media_typ)
    if spec is None:
        return
    thumb_abs = spec.thumb_path(media)
    if thumb_abs is not None:
        regenerate_thumbnail_from_bytes(png_bytes, thumb_abs)


def delete_annotation_and_files(db: Session, media_typ: str, media) -> None:  # type: ignore[no-untyped-def]
    """Raeumt die Annotation einer Medienzeile auf, wenn diese selbst geloescht
    wird: gibt die dafuer reservierte Quota frei, loescht die annotierte PNG-
    Datei und die MediaAnnotation-Zeile (+Versionsverlauf). Ohne diesen Aufruf
    blieb die _annotated.png bisher als Datei-Leiche liegen UND die dafuer
    reservierte Quota wurde nie wieder freigegeben (Bug, siehe Session
    2026-07-12). Muss von JEDER Medien-Loeschfunktion aufgerufen werden, die
    ein annotierbares Bild (Task/Message/Person/Site/CrossMarker/LageJournal)
    entfernt."""
    ann = get_annotation(db, media_typ, media.id)
    if ann is None:
        return
    if ann.annotated_file:
        abs_path = _annotated_abs_path(media_typ, media)
        if abs_path.exists():
            n_bytes = abs_path.stat().st_size
            spec = spec_for(media_typ)
            org_id = spec.org_of(db, media) if spec else None
            if org_id is not None and n_bytes > 0:
                from app.services.storage_service import release_storage
                release_storage(db, org_id, n_bytes)
            abs_path.unlink(missing_ok=True)
    db.query(MediaAnnotationVersion).filter(MediaAnnotationVersion.annotation_id == ann.id).delete()
    db.delete(ann)


def total_annotated_bytes(db: Session, org_id: int) -> int:
    """Summe der Dateigroessen aller aktuell vorhandenen annotierten PNGs einer
    Org. Wird von storage_service.reconcile_storage() addiert: annotierte
    Dateien stehen nicht in der bytes-Spalte der Medientabellen (die bleibt
    beim Original), ein reiner SQL-SUM ueber die Medientabellen erfasst sie
    also nie (Bug, siehe Session 2026-07-12)."""
    total = 0
    rows = db.query(MediaAnnotation).filter(MediaAnnotation.annotated_file.isnot(None)).all()
    for ann in rows:
        spec = spec_for(ann.media_typ)
        if spec is None:
            continue
        media = db.get(spec.model, ann.media_id)
        if media is None:
            continue
        if spec.org_of(db, media) != org_id:
            continue
        abs_path = _annotated_abs_path(ann.media_typ, media)
        if abs_path.exists():
            total += abs_path.stat().st_size
    return total


def display_abs_path(db: Session, media_typ: str, media) -> Path | None:  # type: ignore[no-untyped-def]
    """Flaches annotiertes PNG falls vorhanden, sonst das Original."""
    ann = get_annotation(db, media_typ, media.id)
    if ann and ann.annotated_file:
        p = _annotated_abs_path(media_typ, media)
        if p.exists():
            return p
    return original_abs_path(media_typ, media)


def annotated_media_ids(db: Session, media_typ: str, media_ids: list[int]) -> set[int]:
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


def annotated_versions(db: Session, media_typ: str, media_ids: list[int]) -> dict[int, int]:
    """Wie annotated_media_ids(), liefert zusaetzlich einen Versions-Zeitstempel je
    media_id (Unix-Sekunden von annotated_at). Templates haengen ihn als
    Cache-Buster an die Thumb-URL (?v=...), damit ein per HTMX neu gerenderter
    <img>-Tag mit unveraendertem src nicht aus dem Browser-Cache bedient wird,
    obwohl das Thumb-File gerade neu geschrieben wurde (siehe _regenerate_thumb)."""
    if not media_ids:
        return {}
    rows = (
        db.query(MediaAnnotation.media_id, MediaAnnotation.annotated_at)
        .filter(
            MediaAnnotation.media_typ == media_typ,
            MediaAnnotation.media_id.in_(media_ids),
            MediaAnnotation.annotated_file.isnot(None),
        )
        .all()
    )
    return {mid: int(ts.timestamp()) for mid, ts in rows if ts is not None}


# ── Soft-Lock (Heartbeat, TTL 5 min; Last-write-wins mit Warnung) ────────────

def _lock_frisch(ann: MediaAnnotation) -> bool:
    if not ann.locked_by or not ann.locked_at:
        return False
    la = ann.locked_at if ann.locked_at.tzinfo else ann.locked_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - la) < timedelta(seconds=LOCK_TTL_SEC)


def lock_info(db: Session, ann: MediaAnnotation, user: User) -> dict:
    """Fremd-Lock-Status (fuer die 'wird gerade bearbeitet'-Warnung)."""
    if ann and _lock_frisch(ann) and ann.locked_by != user.id:
        other = db.get(User, ann.locked_by)
        return {"locked_by_other": True, "name": other.display_name if other else "jemand"}
    return {"locked_by_other": False, "name": None}


def acquire_lock(db: Session, media_typ: str, media_id: int, org_id: int | None, user: User) -> dict:
    ann = get_or_create(db, media_typ, media_id, org_id)
    info = lock_info(db, ann, user)          # Fremd-Lock VOR Uebernahme melden
    ann.locked_by = user.id
    ann.locked_at = datetime.now(UTC)
    db.flush()
    return info


def release_lock(db: Session, media_typ: str, media_id: int, user: User) -> None:
    ann = get_annotation(db, media_typ, media_id)
    if ann and ann.locked_by == user.id:
        ann.locked_by = None
        ann.locked_at = None
        db.flush()
