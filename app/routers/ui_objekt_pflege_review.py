"""Interne Prüfung eingereichter externer Objekt-Pflegeaufträge."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.core.timezones import format_local_datetime
from app.db import get_db
from app.models.objekt import PFLEGEAUFTRAG_STATUS_LABELS, ObjektDokument, ObjektPflegeauftrag
from app.models.user import User
from app.routers.ui_objekt import _objekt_or_404, require_objekt_enabled
from app.services import kontakt_service
from app.services.kontakt_service import KontaktKonflikt
from app.services.mail_service import send_pflegeauftrag_nacharbeit
from app.services.objekt_pflege_service import (
    berechne_stammdaten_diff,
    freigabe_transaktion,
    hole_dokument_ungueltig_meldungen,
    hole_offene_kontakt_vorschlaege,
    hole_wartende_dokumentversionen,
    nacharbeit_anfordern,
    verwerfen_transaktion,
)

router = APIRouter(prefix="/objekte", tags=["objekt-pflege-review"])


def _pflegeauftrag_or_404(db: Session, objekt_id: int, auftrag_id: int) -> ObjektPflegeauftrag:
    auftrag = db.query(ObjektPflegeauftrag).filter(ObjektPflegeauftrag.id == auftrag_id).first()
    if auftrag is None or auftrag.objekt_id != objekt_id:
        raise HTTPException(status_code=404, detail="Pflegeauftrag nicht gefunden")
    return auftrag


def _aktuelle_vorgaenger_version(
    db: Session, auftrag: ObjektPflegeauftrag, dokument: ObjektDokument,
) -> ObjektDokument | None:
    if dokument.dokument_gruppe_id is None:
        return None
    genesis_id = dokument.dokument_gruppe_id
    return (
        db.query(ObjektDokument)
        .filter(
            ObjektDokument.org_id == auftrag.org_id,
            (ObjektDokument.id == genesis_id) | (ObjektDokument.dokument_gruppe_id == genesis_id),
            ObjektDokument.ist_aktuelle_version.is_(True),
        )
        .first()
    )


@router.get("/{objekt_id}/pflegeauftrag/{auftrag_id}/review", response_class=HTMLResponse)
def pflegeauftrag_review(
    objekt_id: int, auftrag_id: int, request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    auftrag = _pflegeauftrag_or_404(db, objekt.id, auftrag_id)
    kontakt_vorschlaege = []
    for vorschlag in hole_offene_kontakt_vorschlaege(db, auftrag):
        try:
            diff = json.loads(vorschlag.diff_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            diff = {}
        kontakt_vorschlaege.append({
            "vorschlag": vorschlag,
            "kontakt": kontakt_service.get_kontakt(db, vorschlag.kontakt_id, include_archiviert=True),
            "diff": diff,
        })
    dokumente_wartend = []
    for dokument in hole_wartende_dokumentversionen(db, auftrag):
        bisherig = _aktuelle_vorgaenger_version(db, auftrag, dokument)
        dokumente_wartend.append({
            "dokument": dokument,
            "bisherig": bisherig,
            "hochgeladen_von": db.get(User, dokument.hochgeladen_von_id) if dokument.hochgeladen_von_id else None,
            "bisherig_hochgeladen_von": (
                db.get(User, bisherig.hochgeladen_von_id)
                if bisherig is not None and bisherig.hochgeladen_von_id else None
            ),
        })
    ungueltig_meldungen = []
    for meldung in hole_dokument_ungueltig_meldungen(db, auftrag):
        try:
            dokument_id = json.loads(meldung.metadaten_json or "{}").get("dokument_id")
        except (TypeError, ValueError, json.JSONDecodeError):
            dokument_id = None
        gemeldetes_dokument = (
            db.query(ObjektDokument)
            .filter(ObjektDokument.id == dokument_id, ObjektDokument.org_id == auftrag.org_id)
            .first()
        )
        ungueltig_meldungen.append({"meldung": meldung, "dokument": gemeldetes_dokument})
    return templates.TemplateResponse(request, "objekt/pflege_review.html", {
        "user": user, "objekt": objekt, "auftrag": auftrag,
        "pflegeauftrag_status_labels": PFLEGEAUFTRAG_STATUS_LABELS,
        "stammdaten_diff": berechne_stammdaten_diff(db, auftrag),
        "kontakt_vorschlaege": kontakt_vorschlaege,
        "dokumente_wartend": dokumente_wartend,
        "ungueltig_meldungen": ungueltig_meldungen,
    })


@router.post("/{objekt_id}/pflegeauftrag/{auftrag_id}/review/freigeben")
def pflegeauftrag_freigeben(
    objekt_id: int, auftrag_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled), kontakt_freigeben: list[int] = Form(default=[]),
    kontakt_verwerfen: list[int] = Form(default=[]), dokument_freigeben: list[int] = Form(default=[]),
    dokument_verwerfen: list[int] = Form(default=[]), dokument_archivieren: list[int] = Form(default=[]),
    revision_intervall_tage: int = Form(365),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    auftrag = _pflegeauftrag_or_404(db, objekt.id, auftrag_id)
    try:
        freigabe_transaktion(
            db, auftrag, user_id=user.id,
            kontakt_vorschlag_freigeben=set(kontakt_freigeben),
            kontakt_vorschlag_verwerfen=set(kontakt_verwerfen),
            dokument_freigeben=set(dokument_freigeben),
            dokument_verwerfen=set(dokument_verwerfen),
            dokument_archivieren=set(dokument_archivieren), revision_intervall_tage=revision_intervall_tage,
        )
    except (ValueError, KontaktKonflikt) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(db, "objekt.pflegeauftrag_freigegeben", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id, payload={"pflegeauftrag_id": auftrag.id})
    db.commit()
    return RedirectResponse(url=f"/objekte/{objekt_id}?tab=datenpflege", status_code=303)


@router.post("/{objekt_id}/pflegeauftrag/{auftrag_id}/review/verwerfen")
def pflegeauftrag_verwerfen(
    objekt_id: int, auftrag_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    auftrag = _pflegeauftrag_or_404(db, objekt.id, auftrag_id)
    try:
        verwerfen_transaktion(db, auftrag, user_id=user.id)
    except (ValueError, KontaktKonflikt) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(db, "objekt.pflegeauftrag_verworfen", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id, payload={"pflegeauftrag_id": auftrag.id})
    db.commit()
    return RedirectResponse(url=f"/objekte/{objekt_id}?tab=datenpflege", status_code=303)


@router.post("/{objekt_id}/pflegeauftrag/{auftrag_id}/review/nacharbeit")
async def pflegeauftrag_nacharbeit(
    objekt_id: int, auftrag_id: int, text: str = Form(...), db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    auftrag = _pflegeauftrag_or_404(db, objekt.id, auftrag_id)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Bitte Nacharbeit beschreiben")
    if not auftrag.kontakt.email:
        raise HTTPException(status_code=400, detail="Der Kontakt hat keine E-Mail-Adresse")
    try:
        raw_token = nacharbeit_anfordern(db, auftrag, user_id=user.id, text=text.strip())
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    link = f"{settings.effective_public_base_url.rstrip('/')}/objektpflege/{raw_token}"
    await send_pflegeauftrag_nacharbeit(
        to=auftrag.kontakt.email, kontakt_name=auftrag.kontakt.anzeigename, objekt_name=objekt.name,
        link=link, text=text.strip(), gueltig_bis_text=format_local_datetime(auftrag.gueltig_bis, user.org),
        db=db, org_id=user.org_id,
    )
    write_audit(db, "objekt.pflegeauftrag_nacharbeit_angefordert", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id, payload={"pflegeauftrag_id": auftrag.id})
    db.commit()
    return RedirectResponse(url=f"/objekte/{objekt_id}?tab=datenpflege", status_code=303)
