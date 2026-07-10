"""Momentaufnahme-Speicherung ('Lage einfrieren', Phase 3): PNG-Bytes von
render_lagefuehrung_map_png() persistent ablegen.

Muster app/services/lage_media_service.py (Verzeichnisstruktur org/incident), aber ohne
_process_image — die Bytes sind bereits ein fertiges PNG, keine Upload-Nachverarbeitung nötig.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.models.lagefuehrung import LagefuehrungSnapshot

_SNAPSHOT_DIR = "app_storage/lagefuehrung_snapshot"


def _snapshot_dir(org_id: int | None, incident_id: int) -> Path:
    if org_id is not None:
        d = Path(_SNAPSHOT_DIR) / str(org_id) / str(incident_id)
    else:
        d = Path(_SNAPSHOT_DIR) / str(incident_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_path(snapshot: LagefuehrungSnapshot) -> Path:
    return _snapshot_dir(snapshot.org_id, snapshot.incident_id) / snapshot.stored_filename


def save_snapshot_png(org_id: int | None, incident_id: int, png_bytes: bytes) -> str:
    """Schreibt die PNG-Bytes unter einem neuen UUID-Dateinamen, gibt den Dateinamen zurück."""
    dest = _snapshot_dir(org_id, incident_id)
    filename = f"{uuid.uuid4().hex}.png"
    (dest / filename).write_bytes(png_bytes)
    return filename
