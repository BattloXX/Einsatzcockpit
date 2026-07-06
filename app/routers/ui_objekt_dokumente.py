"""Objektverwaltung Router: Dokumente (Upload, Galerie, Klassifikation, Viewer).

Datei-Auslieferung ausschliesslich ueber /objekt-medien/* mit Org-Check
(Muster ui_media.py, UAS-Variante) — Storage liegt ausserhalb von app/static.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.objekt import (
    AUSWAHL_DOKUMENTART,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
)
from app.models.user import User
from app.routers.ui_objekt import _LESE_ROLLEN, _objekt_or_404, require_objekt_enabled
from app.services.objekt_dokument_service import (
    absolute_pfad,
    delete_dokument,
    reindex_objekt,
    sammel_pdf,
    store_dokument_upload,
    verarbeite_dokument,
)
from app.services.objekt_service import lade_auswahl, write_objekt_change

router = APIRouter(tags=["objekt-dokumente"])


# ── Galerie-Kontext ────────────────────────────────────────────────────────────

def _snippet(text: str, term: str, umfeld: int = 60) -> str:
    """Kontext-Ausschnitt um den ersten Treffer (fuer die Trefferliste)."""
    if not text:
        return ""
    pos = text.lower().find(term.lower())
    if pos < 0:
        return text[: umfeld * 2] + ("…" if len(text) > umfeld * 2 else "")
    start = max(0, pos - umfeld)
    ende = min(len(text), pos + len(term) + umfeld)
    ausschnitt = text[start:ende]
    return ("…" if start > 0 else "") + ausschnitt + ("…" if ende < len(text) else "")


def _galerie_context(
    request: Request,
    db: Session,
    user: User,
    objekt: Objekt,
    art: str = "",
    suche: str = "",
) -> dict:
    from app.core.permissions import is_objekt_verwalter

    seiten_query = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.objekt_id == objekt.id)
        .order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)
    )
    if art == "unklassifiziert":
        seiten_query = seiten_query.filter(ObjektDokumentSeite.dokumentart.is_(None))
    elif art:
        seiten_query = seiten_query.filter(ObjektDokumentSeite.dokumentart == art)
    if suche.strip():
        term = f"%{suche.strip()}%"
        from sqlalchemy import or_
        seiten_query = seiten_query.filter(or_(
            ObjektDokumentSeite.titel.like(term),
            ObjektDokumentSeite.melderlinien.like(term),
            ObjektDokumentSeite.volltext.like(term),
        ))
    seiten = seiten_query.all()

    # Zaehler je Dokumentart (fuer Filter-Chips, unabhaengig vom aktiven Filter)
    zaehler: dict[str, int] = {
        code: cnt
        for code, cnt in (
            db.query(ObjektDokumentSeite.dokumentart, func.count(ObjektDokumentSeite.id))
            .filter(ObjektDokumentSeite.objekt_id == objekt.id)
            .group_by(ObjektDokumentSeite.dokumentart)
            .all()
        )
        if code is not None
    }
    gesamt = (
        db.query(func.count(ObjektDokumentSeite.id))
        .filter(ObjektDokumentSeite.objekt_id == objekt.id)
        .scalar()
    ) or 0
    unklassifiziert = (
        db.query(func.count(ObjektDokumentSeite.id))
        .filter(
            ObjektDokumentSeite.objekt_id == objekt.id,
            ObjektDokumentSeite.dokumentart.is_(None),
        )
        .scalar()
    ) or 0

    dokumente = (
        db.query(ObjektDokument)
        .filter(ObjektDokument.objekt_id == objekt.id)
        .order_by(ObjektDokument.hochgeladen_am.desc())
        .all()
    )
    in_verarbeitung = any(d.status in ("neu", "verarbeitung") for d in dokumente)

    # KI-Review (PR8): offene Vorschlaege + Opt-in-Status
    from app.models.objekt import KI_VORSCHLAG_OFFEN, ObjektSeiteKiVorschlag
    from app.services.objekt_ki_service import ki_klassifikation_enabled
    ki_vorschlaege = (
        db.query(ObjektSeiteKiVorschlag)
        .join(ObjektDokumentSeite, ObjektSeiteKiVorschlag.seite_id == ObjektDokumentSeite.id)
        .filter(
            ObjektDokumentSeite.objekt_id == objekt.id,
            ObjektSeiteKiVorschlag.status == KI_VORSCHLAG_OFFEN,
        )
        .order_by(ObjektSeiteKiVorschlag.id)
        .all()
    )

    return {
        "ki_vorschlaege": ki_vorschlaege,
        "ki_enabled": ki_klassifikation_enabled(objekt.org_id, db),
        "user": user,
        "objekt": objekt,
        "seiten": seiten,
        "dokumente": dokumente,
        "dokumentarten": lade_auswahl(db, objekt.org_id, AUSWAHL_DOKUMENTART),
        "zaehler": zaehler,
        "gesamt": gesamt,
        "unklassifiziert": unklassifiziert,
        "filter_art": art,
        "filter_suche": suche,
        "in_verarbeitung": in_verarbeitung,
        "ist_verwalter": is_objekt_verwalter(user),
    }


@router.get("/objekte/{objekt_id}/dokumente", response_class=HTMLResponse)
def dokumente_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    art: str = "",
    suche: str = "",
    ki_warte: int = 0,
):
    objekt = _objekt_or_404(db, objekt_id, user)
    ctx = _galerie_context(request, db, user, objekt, art=art, suche=suche)
    # KI-Analyse laeuft im Hintergrund: solange noch keine Vorschlaege da sind,
    # weiter automatisch nachladen (kein F5). Nach ~3 min (45 x 4 s) aufgeben —
    # ein Vision-Lauf ueber bis zu 20 Seiten kann laenger als eine Minute dauern.
    if (
        ki_warte
        and ki_warte < 45
        and ctx["ki_enabled"]
        and not ctx["ki_vorschlaege"]
        and ctx["unklassifiziert"] > 0
    ):
        ctx["ki_analyse_laeuft"] = True
        ctx["ki_warte"] = ki_warte + 1
    return templates.TemplateResponse(request, "objekt/_dokumente.html", ctx)


@router.post("/objekte/{objekt_id}/dokumente/upload", response_class=HTMLResponse)
async def dokumente_upload(
    objekt_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    dateien: list[UploadFile] = File(...),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    fehler: list[str] = []
    neu: list[int] = []
    for datei in dateien:
        try:
            dokument = await store_dokument_upload(datei, objekt, user, db)
            neu.append(dokument.id)
        except HTTPException as exc:
            fehler.append(f"{datei.filename}: {exc.detail}")
    if neu:
        write_objekt_change(db, objekt.id, objekt.org_id, "dokumente", "dokument_upload",
                            before=None, after=f"{len(neu)} Datei(en)", user_id=user.id)
        write_audit(db, "objekt.dokument_uploaded", org_id=user.org_id, user_id=user.id,
                    entity_type="objekt", entity_id=objekt.id,
                    payload={"anzahl": len(neu)})
    db.commit()
    for dokument_id in neu:
        background_tasks.add_task(verarbeite_dokument, dokument_id)

    ctx = _galerie_context(request, db, user, objekt)
    ctx["upload_fehler"] = fehler
    return templates.TemplateResponse(request, "objekt/_dokumente.html", ctx)


@router.post("/objekte/{objekt_id}/dokumente/reindex", response_class=HTMLResponse)
def dokumente_reindex(
    objekt_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    """Volltext bestehender Dokumentseiten dieses Objekts neu aufbauen (Hintergrund)."""
    objekt = _objekt_or_404(db, objekt_id, user)
    background_tasks.add_task(reindex_objekt, objekt.id)
    ctx = _galerie_context(request, db, user, objekt)
    ctx["reindex_gestartet"] = True
    return templates.TemplateResponse(request, "objekt/_dokumente.html", ctx)


@router.get("/objekte/{objekt_id}/dokumente/suche.json")
def dokumente_suche_json(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    q: str = "",
):
    """Volltextsuche in den Dokumentseiten eines Objekts (fuer die Einsatzinfo).

    Liefert Treffer-Seiten mit Kontext-Snippet und Viewer-Deep-Link.
    """
    from sqlalchemy import or_

    from app.models.objekt import DOKUMENTARTEN
    objekt = _objekt_or_404(db, objekt_id, user)
    term = q.strip()
    if len(term) < 2:
        return {"treffer": []}
    like = f"%{term}%"
    seiten = (
        db.query(ObjektDokumentSeite)
        .filter(
            ObjektDokumentSeite.objekt_id == objekt.id,
            or_(
                ObjektDokumentSeite.titel.like(like),
                ObjektDokumentSeite.melderlinien.like(like),
                ObjektDokumentSeite.volltext.like(like),
            ),
        )
        .order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)
        .limit(30)
        .all()
    )
    treffer = []
    for s in seiten:
        treffer.append({
            "seiten_nr": s.seiten_nr,
            "dokumentart": DOKUMENTARTEN.get(s.dokumentart or "", s.dokumentart or ""),
            "titel": s.titel or "",
            "snippet": _snippet(s.volltext or s.melderlinien or s.titel or "", term),
            "viewer_url": f"/objekte/{objekt.id}/dokumente/viewer?seite={s.id}&suche={term}",
        })
    return {"treffer": treffer}


@router.post("/objekte/{objekt_id}/dokumente/seiten/bulk", response_class=HTMLResponse)
def seiten_bulk_klassifizieren(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    seiten_ids: str = Form(...),
    dokumentart: str = Form(""),
    titel: str = Form(""),
    melderlinien: str = Form(""),
    stand: str = Form(""),
    bei_einsatz_drucken: str = Form(""),
    art: str = Form(""),
    suche: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    try:
        ids = [int(s) for s in seiten_ids.split(",") if s.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungueltige Seiten-Auswahl") from None
    if not ids:
        raise HTTPException(status_code=400, detail="Keine Seiten ausgewaehlt")
    dokumentarten = lade_auswahl(db, objekt.org_id, AUSWAHL_DOKUMENTART)
    if dokumentart and dokumentart not in dokumentarten:
        raise HTTPException(status_code=400, detail="Unbekannte Dokumentart")

    seiten = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.id.in_(ids), ObjektDokumentSeite.objekt_id == objekt.id)
        .all()
    )
    stand_datum = datetime.strptime(stand, "%Y-%m-%d").date() if stand.strip() else None
    jetzt = datetime.now(UTC)
    for seite in seiten:
        if dokumentart:
            seite.dokumentart = dokumentart
        if titel.strip():
            seite.titel = titel.strip()[:200]
        if melderlinien.strip():
            seite.melderlinien = melderlinien.strip()[:100]
        if stand_datum:
            seite.stand = stand_datum
        seite.bei_einsatz_drucken = bool(bei_einsatz_drucken)
        seite.klassifiziert_von_id = user.id
        seite.klassifiziert_am = jetzt
    art_label = dokumentarten.get(dokumentart, dokumentart or "unveraendert")
    write_objekt_change(db, objekt.id, objekt.org_id, "dokumente", "seiten_klassifiziert",
                        before=None, after=f"{len(seiten)} Seite(n) → {art_label}",
                        user_id=user.id)
    db.commit()

    return templates.TemplateResponse(
        request, "objekt/_dokumente.html",
        _galerie_context(request, db, user, objekt, art=art, suche=suche),
    )


@router.post("/objekte/{objekt_id}/dokumente/seite/{seite_id}/drehen", response_class=HTMLResponse)
def seite_drehen(
    objekt_id: int,
    seite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    richtung: str = Form(...),   # "links" | "rechts"
    art: str = Form(""),
    suche: str = Form(""),
):
    """Persistente 90°-Drehung einer Seite (Bearbeitungsmodus)."""
    objekt = _objekt_or_404(db, objekt_id, user)
    seite = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.id == seite_id, ObjektDokumentSeite.objekt_id == objekt.id)
        .first()
    )
    if seite is None:
        raise HTTPException(status_code=404, detail="Seite nicht gefunden")
    delta = 90 if richtung == "rechts" else -90
    seite.rotation = ((seite.rotation or 0) + delta) % 360
    seite.klassifiziert_von_id = user.id
    seite.klassifiziert_am = datetime.now(UTC)
    db.commit()

    return templates.TemplateResponse(
        request, "objekt/_dokumente.html",
        _galerie_context(request, db, user, objekt, art=art, suche=suche),
    )


@router.post("/objekte/{objekt_id}/dokumente/{dokument_id}/loeschen", response_class=HTMLResponse)
def dokument_loeschen(
    objekt_id: int,
    dokument_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    dokument = (
        db.query(ObjektDokument)
        .filter(ObjektDokument.id == dokument_id, ObjektDokument.objekt_id == objekt.id)
        .first()
    )
    if dokument is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    write_objekt_change(db, objekt.id, objekt.org_id, "dokumente", "dokument_geloescht",
                        before=dokument.dateiname_original, after=None, user_id=user.id)
    write_audit(db, "objekt.dokument_deleted", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id,
                payload={"dateiname": dokument.dateiname_original})
    delete_dokument(dokument, db)
    db.commit()
    return templates.TemplateResponse(
        request, "objekt/_dokumente.html",
        _galerie_context(request, db, user, objekt),
    )


# ── Sammel-PDF ─────────────────────────────────────────────────────────────────

@router.get("/objekte/{objekt_id}/dokumente/sammel-pdf")
def dokumente_sammel_pdf(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    art: str = "",
    nur_einsatzdruck: int = 0,
):
    objekt = _objekt_or_404(db, objekt_id, user)
    seiten_query = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.objekt_id == objekt.id)
        .order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)
    )
    if art:
        seiten_query = seiten_query.filter(ObjektDokumentSeite.dokumentart == art)
    if nur_einsatzdruck:
        seiten_query = seiten_query.filter(ObjektDokumentSeite.bei_einsatz_drucken.is_(True))
    seiten = seiten_query.all()
    if not seiten:
        raise HTTPException(status_code=404, detail="Keine Seiten fuer Sammel-PDF")

    pdf = sammel_pdf(seiten)
    # inline: Browser-PDF-Viewer zeigt direkt an (Speichern dort weiterhin moeglich)
    dokumentarten = lade_auswahl(db, objekt.org_id, AUSWAHL_DOKUMENTART)
    name = f"{objekt.anzeige_nummer}_{dokumentarten.get(art, 'dokumente')}.pdf".replace(" ", "_")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


# ── Viewer ─────────────────────────────────────────────────────────────────────

@router.get("/objekte/{objekt_id}/dokumente/viewer", response_class=HTMLResponse)
def dokumente_viewer(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    seite: int = 0,
    art: str = "",
    suche: str = "",
):
    objekt = _objekt_or_404(db, objekt_id, user)
    ctx = _galerie_context(request, db, user, objekt, art=art, suche=suche)
    seiten = ctx["seiten"]
    if not seiten:
        # Filter/Suche ohne Treffer: Viewer mit Leer-Hinweis statt 404,
        # damit Suchfeld und "Alle Dokumente" erreichbar bleiben.
        if suche or art:
            ctx["start_index"] = 0
            return templates.TemplateResponse(request, "objekt/viewer.html", ctx)
        raise HTTPException(status_code=404, detail="Keine Seiten vorhanden")
    start_index = 0
    if seite:
        for i, s in enumerate(seiten):
            if s.id == seite:
                start_index = i
                break
    ctx["start_index"] = start_index
    return templates.TemplateResponse(request, "objekt/viewer.html", ctx)


# ── Geschuetzte Datei-Auslieferung (Org-Check) ────────────────────────────────

def _bild_response(pfad, media_type: str, rotation: int) -> Response:
    """Liefert die Bilddatei aus; bei rotation != 0 (Uhrzeigersinn) gedreht.

    rotation == 0 (Regelfall) → unveraendert per FileResponse (kein PIL-Overhead).
    """
    rot = rotation % 360
    if rot == 0:
        return FileResponse(pfad, media_type=media_type)
    import io

    from PIL import Image
    with Image.open(pfad) as img:
        # PIL.rotate dreht gegen den Uhrzeigersinn → negativ fuer Uhrzeigersinn.
        gedreht = img.rotate(-rot, expand=True)
        buf = io.BytesIO()
        fmt = "JPEG" if media_type == "image/jpeg" else "PNG"
        if fmt == "JPEG" and gedreht.mode not in ("RGB", "L"):
            gedreht = gedreht.convert("RGB")
        gedreht.save(buf, fmt)
    return Response(content=buf.getvalue(), media_type=media_type)


def _seite_fuer_user(db: Session, seite_id: int, user: User) -> ObjektDokumentSeite:
    seite = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.id == seite_id)
        .first()
    )
    if seite is None:
        raise HTTPException(status_code=404, detail="Seite nicht gefunden")
    if not user.is_system_admin and seite.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Seite nicht gefunden")
    return seite


@router.get("/objekt-medien/seite/{seite_id}/thumb")
def seite_thumb(
    seite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    seite = _seite_fuer_user(db, seite_id, user)
    if not seite.thumb_pfad:
        raise HTTPException(status_code=404, detail="Kein Thumbnail vorhanden")
    pfad = absolute_pfad(seite.thumb_pfad)
    if not pfad.exists():
        raise HTTPException(status_code=404, detail="Datei fehlt")
    return _bild_response(pfad, "image/jpeg", seite.rotation)


@router.get("/objekt-medien/seite/{seite_id}/bild")
def seite_bild(
    seite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    seite = _seite_fuer_user(db, seite_id, user)
    if not seite.bild_pfad:
        raise HTTPException(status_code=404, detail="Kein Rendering vorhanden")
    pfad = absolute_pfad(seite.bild_pfad)
    if not pfad.exists():
        raise HTTPException(status_code=404, detail="Datei fehlt")
    return _bild_response(pfad, "image/png", seite.rotation)


@router.get("/objekt-medien/seite/{seite_id}/pdf")
def seite_pdf(
    seite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    seite = _seite_fuer_user(db, seite_id, user)
    if not seite.einzel_pdf_pfad:
        raise HTTPException(status_code=404, detail="Keine Einzelseite vorhanden")
    pfad = absolute_pfad(seite.einzel_pdf_pfad)
    if not pfad.exists():
        raise HTTPException(status_code=404, detail="Datei fehlt")
    # content_disposition_type="inline": direkt im Browser-PDF-Viewer anzeigen
    return FileResponse(
        pfad, media_type="application/pdf",
        filename=f"seite_{seite.seiten_nr:04d}.pdf",
        content_disposition_type="inline",
    )


@router.get("/objekt-medien/dokument/{dokument_id}/original")
def dokument_original(
    dokument_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    dokument = (
        db.query(ObjektDokument)
        .filter(ObjektDokument.id == dokument_id)
        .first()
    )
    if dokument is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    if not user.is_system_admin and dokument.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    pfad = absolute_pfad(dokument.pfad)
    if not pfad.exists():
        raise HTTPException(status_code=404, detail="Datei fehlt")
    return FileResponse(
        pfad, media_type="application/pdf",
        filename=dokument.dateiname_original,
        content_disposition_type="inline",
    )


@router.get("/objekt-medien/symbol/{symbol_id}")
def symbol_bild(
    symbol_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    """Liefert ein hochgeladenes Karten-Symbolbild (org-scoped).

    Restriktiver CSP-Header + Auslieferung als <img>-Quelle: hochgeladene SVGs
    koennen kein Skript ausfuehren (zusaetzlich zur Server-Sanitisierung).
    """
    from app.models.objekt import ObjektSymbol
    from app.services.objekt_symbol_service import bild_media_type, symbol_bild_absolut

    symbol = db.query(ObjektSymbol).filter(ObjektSymbol.id == symbol_id).first()
    if symbol is None or not symbol.bild_pfad:
        raise HTTPException(status_code=404, detail="Symbolbild nicht gefunden")
    if not user.is_system_admin and symbol.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Symbolbild nicht gefunden")
    pfad = symbol_bild_absolut(symbol.bild_pfad)
    if not pfad.exists():
        raise HTTPException(status_code=404, detail="Datei fehlt")
    return FileResponse(
        pfad,
        media_type=bild_media_type(symbol.bild_pfad),
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
    )


# ── KI-Klassifizierung (PR8): Analyse + Review-Queue ──────────────────────────

@router.post("/objekte/{objekt_id}/dokumente/ki-analyse", response_class=HTMLResponse)
def ki_analyse_starten(
    objekt_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.services.objekt_ki_service import (
        analysiere_unklassifizierte_seiten,
        ki_klassifikation_enabled,
    )

    objekt = _objekt_or_404(db, objekt_id, user)
    ctx = _galerie_context(request, db, user, objekt)
    if not ki_klassifikation_enabled(objekt.org_id, db):
        ctx["upload_fehler"] = ["KI-Klassifizierung ist für diese Organisation nicht aktiviert."]
        return templates.TemplateResponse(request, "objekt/_dokumente.html", ctx)

    background_tasks.add_task(analysiere_unklassifizierte_seiten, objekt.id)
    write_audit(db, "objekt.ki_analyse_gestartet", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id)
    db.commit()
    ctx["ki_analyse_laeuft"] = True
    ctx["ki_warte"] = 1
    return templates.TemplateResponse(request, "objekt/_dokumente.html", ctx)


def _vorschlag_oder_404(db: Session, objekt_id: int, vorschlag_id: int):
    from app.models.objekt import ObjektSeiteKiVorschlag
    vorschlag = (
        db.query(ObjektSeiteKiVorschlag)
        .join(ObjektDokumentSeite, ObjektSeiteKiVorschlag.seite_id == ObjektDokumentSeite.id)
        .filter(
            ObjektSeiteKiVorschlag.id == vorschlag_id,
            ObjektDokumentSeite.objekt_id == objekt_id,
        )
        .first()
    )
    if vorschlag is None:
        raise HTTPException(status_code=404, detail="Vorschlag nicht gefunden")
    return vorschlag


def _vorschlag_uebernehmen(db: Session, vorschlag, user: User) -> None:
    from app.models.objekt import KI_VORSCHLAG_UEBERNOMMEN
    seite = db.get(ObjektDokumentSeite, vorschlag.seite_id)
    if seite is None:
        return
    if vorschlag.dokumentart:
        seite.dokumentart = vorschlag.dokumentart
    if vorschlag.titel:
        seite.titel = vorschlag.titel
    if vorschlag.melderlinien:
        seite.melderlinien = vorschlag.melderlinien
    if vorschlag.stand:
        seite.stand = vorschlag.stand
    seite.klassifiziert_von_id = user.id
    seite.klassifiziert_am = datetime.now(UTC)
    vorschlag.status = KI_VORSCHLAG_UEBERNOMMEN
    vorschlag.entschieden_von_id = user.id
    vorschlag.entschieden_am = datetime.now(UTC)
    dokumentarten = lade_auswahl(db, seite.org_id, AUSWAHL_DOKUMENTART)
    # Uebernommene Klassifikation in den Volltext einfliessen lassen, damit sie
    # in der Dokument- und Einsatzinfo-Suche gefunden wird (auch die Dokumentart-
    # Bezeichnung, nicht nur Titel/Melderlinie).
    klass_teile = [
        dokumentarten.get(vorschlag.dokumentart or "", ""),
        vorschlag.titel or "",
        (f"Melderlinie {vorschlag.melderlinien}" if vorschlag.melderlinien else ""),
    ]
    klass_text = " ".join(teil for teil in klass_teile if teil).strip()
    if klass_text:
        bestehend = seite.volltext or ""
        if klass_text not in bestehend:
            seite.volltext = (klass_text + "\n" + bestehend).strip()[:100000]
    write_objekt_change(
        db, seite.objekt_id, seite.org_id, "dokumente", "ki_vorschlag_uebernommen",
        before=None,
        after=f"Seite {seite.seiten_nr}: {dokumentarten.get(vorschlag.dokumentart or '', vorschlag.dokumentart)}",
        user_id=user.id,
    )


@router.post("/objekte/{objekt_id}/dokumente/ki-review/{vorschlag_id}/uebernehmen",
             response_class=HTMLResponse)
def ki_vorschlag_uebernehmen(
    objekt_id: int,
    vorschlag_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    vorschlag = _vorschlag_oder_404(db, objekt.id, vorschlag_id)
    _vorschlag_uebernehmen(db, vorschlag, user)
    db.commit()
    return templates.TemplateResponse(
        request, "objekt/_dokumente.html", _galerie_context(request, db, user, objekt)
    )


@router.post("/objekte/{objekt_id}/dokumente/ki-review/{vorschlag_id}/verwerfen",
             response_class=HTMLResponse)
def ki_vorschlag_verwerfen(
    objekt_id: int,
    vorschlag_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.models.objekt import KI_VORSCHLAG_VERWORFEN
    objekt = _objekt_or_404(db, objekt_id, user)
    vorschlag = _vorschlag_oder_404(db, objekt.id, vorschlag_id)
    vorschlag.status = KI_VORSCHLAG_VERWORFEN
    vorschlag.entschieden_von_id = user.id
    vorschlag.entschieden_am = datetime.now(UTC)
    db.commit()
    return templates.TemplateResponse(
        request, "objekt/_dokumente.html", _galerie_context(request, db, user, objekt)
    )


@router.post("/objekte/{objekt_id}/dokumente/ki-review/alle-uebernehmen",
             response_class=HTMLResponse)
def ki_vorschlaege_alle_uebernehmen(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.models.objekt import KI_VORSCHLAG_OFFEN, ObjektSeiteKiVorschlag
    objekt = _objekt_or_404(db, objekt_id, user)
    offene = (
        db.query(ObjektSeiteKiVorschlag)
        .join(ObjektDokumentSeite, ObjektSeiteKiVorschlag.seite_id == ObjektDokumentSeite.id)
        .filter(
            ObjektDokumentSeite.objekt_id == objekt.id,
            ObjektSeiteKiVorschlag.status == KI_VORSCHLAG_OFFEN,
        )
        .all()
    )
    for vorschlag in offene:
        _vorschlag_uebernehmen(db, vorschlag, user)
    db.commit()
    return templates.TemplateResponse(
        request, "objekt/_dokumente.html", _galerie_context(request, db, user, objekt)
    )


# ── Offline-Sync-API (PR9: Precaching in der Android-App) ─────────────────────

@router.get("/api/objekte/sync")
def objekte_sync_manifest(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    """Manifest fuer das Offline-Precaching (Android-App/PWA).

    Session-Auth wie alle UI-Routen (die Capacitor-App teilt die WebView-Session).
    Nur freigegebene Objekte der eigenen Org; Dateien sind unveraenderlich
    (UUID-Pfade), Delta ergibt sich aus der ID-Menge + aktualisiert_am.
    """
    from app.services.objekt_service import build_sync_manifest
    if user.org_id is None:
        raise HTTPException(status_code=404, detail="Keine Organisation")
    return build_sync_manifest(db, user.org_id)
