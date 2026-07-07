"""Übernahme von Objekt-Dokumentseiten als einsatzgebundene Bild-Kopie.

Nicht-destruktiv: Es wird IMMER eine physische Kopie erzeugt (neue Media-Datei +
Datensatz), nie eine Referenz auf die Objektdatei. Die Kopie gehört genau diesem
Einsatz (Task/Message). Herkunft (source_*) wird in media_annotation festgehalten
und im UI angezeigt. Es gibt bewusst keinen Rückweg ins Objekt.

Quelle ist das bereits gerenderte Seitenbild (objekt_dokument_seite.bild_pfad) —
kein erneutes PDF-Rendering nötig.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.incident import MessageMedia, TaskMedia
from app.models.media_annotation import MediaAnnotation
from app.models.objekt import ObjektDokumentSeite
from app.models.user import User

MAX_SEITEN = 20  # Deckel je Übernahme (offener Punkt 3 im Konzept)


def _rel(p, root) -> str:
    return str(p.resolve().relative_to(root)).replace("\\", "/")


def uebernehme_seiten(
    db: Session, host_typ: str, host, seiten_ids: list[int], user: User, org_id: int | None,
) -> list:
    """Kopiert die gewählten Objekt-Seiten als Bild-Media an den Host (task|message).

    Reihenfolge folgt seiten_ids. Gibt die neu angelegten Media-Zeilen zurück.
    """
    from app.services.media_service import (
        _entity_dir,
        _process_image,
        _reserve,
        _storage_root,
        _task_dir,
    )
    from app.services.objekt_dokument_service import absolute_pfad

    ids = list(dict.fromkeys(seiten_ids))[:MAX_SEITEN]  # dedup + Deckel
    rows = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.id.in_(ids))
        .all()
    )
    by_id = {s.id: s for s in rows}
    root = _storage_root().resolve()
    erstellt: list = []

    for sid in ids:
        seite = by_id.get(sid)
        if seite is None or not seite.bild_pfad:
            continue
        if org_id is not None and seite.org_id != org_id:
            continue  # Tenant-Grenze
        src = absolute_pfad(seite.bild_pfad)
        if not src.exists():
            continue
        data = src.read_bytes()

        if host_typ == "task":
            dest = _task_dir(host.incident_id, host.id, org_id)
        else:
            dest = _entity_dir(host.incident_id, "message", host.id, org_id)

        main_p, thumb_p, w, h, out_mime = _process_image(data, dest)
        stored = main_p.stat().st_size
        _reserve(db, org_id, stored)

        common = dict(
            incident_id=host.incident_id, uploaded_by_user_id=user.id, kind="image",
            original_filename=f"Objekt-Seite-{seite.seiten_nr}.jpg",
            storage_path=_rel(main_p, root), thumb_path=_rel(thumb_p, root),
            mime_type=out_mime, bytes=stored, width=w, height=h,
        )
        media = (TaskMedia(task_id=host.id, **common) if host_typ == "task"
                 else MessageMedia(message_id=host.id, **common))
        db.add(media)
        db.flush()

        db.add(MediaAnnotation(
            media_typ=host_typ, media_id=media.id, org_id=org_id,
            source_objekt_id=seite.objekt_id, source_dokument_id=seite.dokument_id,
            source_seite=seite.seiten_nr,
        ))
        erstellt.append(media)

    return erstellt
