"""Lesende Einsatzinformationen innerhalb einer bereits geprüften eigenen Disposition."""
from sqlalchemy.orm import Session, defer, selectinload

from app.models.major_incident import EinheitSiteDispatch
from app.models.objekt import (
    OBJEKT_EINSATZ_BESTAETIGT,
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_STATUS_UEBERARBEITUNG,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektEinsatz,
)
from app.services.einheit_service import EinheitKontext
from app.services.objekt_service import nur_produktiv, objekt_effective_enabled


def einsatzobjekte(db: Session, ctx: EinheitKontext, dispatch: EinheitSiteDispatch) -> list[Objekt]:
    if dispatch.withdrawn_at or not dispatch.site.incident_id or not objekt_effective_enabled(ctx.org_id, db):
        return []
    return nur_produktiv(db.query(Objekt)).options(
        selectinload(Objekt.gefahren), selectinload(Objekt.merkmale), selectinload(Objekt.bma),
    ).join(ObjektEinsatz, ObjektEinsatz.objekt_id == Objekt.id).filter(
        Objekt.org_id == ctx.org_id,
        Objekt.status.in_([OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_UEBERARBEITUNG]),
        ObjektEinsatz.org_id == ctx.org_id,
        ObjektEinsatz.incident_id == dispatch.site.incident_id,
        ObjektEinsatz.status == OBJEKT_EINSATZ_BESTAETIGT,
    ).order_by(Objekt.id).all()


def einsatzseiten(db: Session, ctx: EinheitKontext, objekt_ids: list[int]):
    return db.query(ObjektDokumentSeite).options(defer(ObjektDokumentSeite.volltext)).join(ObjektDokument).filter(
        ObjektDokumentSeite.org_id == ctx.org_id,
        ObjektDokumentSeite.objekt_id.in_(objekt_ids),
        ObjektDokument.org_id == ctx.org_id,
        ObjektDokument.objekt_id == ObjektDokumentSeite.objekt_id,
        ObjektDokument.ist_aktuelle_version.is_(True),
        ObjektDokument.freigabe_status == "freigegeben",
        ObjektDokument.status == "fertig",
    ).order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)


def _hinweise(objekt: Objekt, org_id: int) -> list[dict]:
    hinweise = [{"name": m.merkmal.name, "text": m.hinweis} for m in objekt.merkmale
                if m.org_id == org_id and m.merkmal.org_id == org_id]
    if objekt.bma and objekt.bma.org_id == org_id:
        for feld, name in (("bmz_standort", "Brandmeldezentrale"), ("fbf_standort", "Feuerwehrbedienfeld"),
                           ("laufkarten_ablageort", "Laufkarten"), ("schluesselsafe_standort", "Schlüsselsafe")):
            if text := getattr(objekt.bma, feld):
                hinweise.append({"name": name, "text": text})
    return hinweise


def objektvorschau(db: Session, ctx: EinheitKontext, dispatch: EinheitSiteDispatch) -> list[dict]:
    objekte = einsatzobjekte(db, ctx, dispatch)
    if not objekte:
        return []
    seiten = einsatzseiten(db, ctx, [objekt.id for objekt in objekte]).all()
    basis = f"/einheit/medien/auftrag/{dispatch.id}/plan"
    return [{
        "id": objekt.id, "bezeichnung": objekt.name, "adresse": objekt.adresse_zeile,
        "informationen": objekt.informationen, "anfahrtsweg": objekt.anfahrtsweg,
        "hinweise": _hinweise(objekt, ctx.org_id),
        "gefahren": [{"name": gefahr.gefahr.name, "detail": gefahr.detail, "un_nummer": gefahr.un_nummer}
                     for gefahr in objekt.gefahren
                     if gefahr.org_id == ctx.org_id and gefahr.gefahr.org_id == ctx.org_id],
        "seiten": [{
            "id": seite.id, "titel": seite.titel or f"Seite {seite.seiten_nr}",
            "url": f"{basis}/{seite.id}/pdf" if seite.einzel_pdf_pfad else f"{basis}/{seite.id}/bild",
            "thumb_url": f"{basis}/{seite.id}/thumb" if seite.thumb_pfad else None,
        } for seite in seiten if seite.objekt_id == objekt.id and (seite.einzel_pdf_pfad or seite.bild_pfad)],
    } for objekt in objekte]
