"""Live-Pruefung indexierter Quellen mit expliziter Mandantenbindung."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.permissions import has_role
from app.models.katplan import KatplanDokumentSeite, KatplanPlan, KatplanVersionElement
from app.models.objekt import OBJEKT_STATUS_LABELS, Objekt, ObjektDokumentSeite
from app.models.user import User
from app.services.katplan_flags import katplan_effective_enabled
from app.services.katplan_quelle_service import sichtbare_quellen
from app.services.katplan_zugriff import darf_bearbeiten, darf_lesen, sieht_vertraulich
from app.services.objekt_service import objekt_effective_enabled
from app.services.suche.adapter import _elemente, _katplan_seiten, _objekt_seiten, _objekte


@dataclass(frozen=True)
class ResolverErgebnis:
    url: str
    status: str
    ist_aktuell: bool = True


def pruefe(db: Session, user: User, quelle: str, quell_id: str, meta: dict) -> ResolverErgebnis | None:
    org_id = user.org_id
    if org_id is None:
        return None
    if quelle in ("objekt", "objekt_dokument_seite"):
        # Lokaler Import vermeidet einen Importzyklus mit den Objekt-Routern.
        from app.routers.ui_objekt import _LESE_ROLLEN

        if not objekt_effective_enabled(org_id, db) or not has_role(user, *_LESE_ROLLEN):
            return None
        if quelle == "objekt":
            objekt = db.scalar(_objekte(org_id).where(Objekt.id == quell_id))
            if objekt is None:
                return None
            url = f"/objekte/{objekt.id}"
        else:
            zeile = db.execute(_objekt_seiten(org_id).where(ObjektDokumentSeite.id == quell_id)).first()
            if zeile is None:
                return None
            seite, _, objekt = zeile
            url = f"/objekte/{objekt.id}/dokumente/viewer?seite={seite.id}"
        return ResolverErgebnis(url, OBJEKT_STATUS_LABELS.get(objekt.status, objekt.status))
    if not katplan_effective_enabled(org_id, db) or not darf_lesen(user):
        return None
    if quelle == "katplan_element":
        plan_id, trenner, uuid = quell_id.partition(":")
        if not trenner:
            return None
        zeile = db.execute(_elemente(org_id).where(
            KatplanPlan.id == plan_id, KatplanVersionElement.element_uuid == uuid,
        )).first()
        if zeile is None:
            return None
        element, version, plan = zeile
        if element.vertraulich and not sieht_vertraulich(user):
            return None
        if meta.get("version_id") != version.id:
            return None
        return ResolverErgebnis(f"/plaene/{plan.id}#element-{uuid}", "Freigegeben")
    if quelle == "katplan_seite":
        zeile = db.execute(_katplan_seiten(org_id).where(KatplanDokumentSeite.id == quell_id)).first()
        if zeile is None:
            return None
        seite, dokument, plan = zeile
        if dokument.id not in {d.id for d in sichtbare_quellen(db, plan, user)}:
            return None
        url = f"/plaene/{plan.id}/quellen/{dokument.id}?seite={seite.seiten_nr}"
        if not darf_bearbeiten(user):
            url += f"&version_id={plan.aktuelle_version_id}"
        return ResolverErgebnis(url, "Freigegeben")
    return None
