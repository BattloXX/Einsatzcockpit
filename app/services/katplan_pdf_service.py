"""Rechtegepruefte Offlinefassung aus derselben Fassung wie MCP."""

import base64
import io
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.templating import templates
from app.core.timezones import format_local_datetime
from app.models.katplan import KATPLAN_GEFAHRENARTEN, KatplanAnhang, KatplanPlan, KatplanVersion
from app.models.user import User
from app.services.katplan_mcp_service import _rohdaten, fassung_laden, link_ansichten
from app.services.katplan_quelle_service import absolute_pfad
from app.services.katplan_service import bereinige_beschreibung
from app.services.kontakt_druck_service import organisationslogo_data_uri

logger = logging.getLogger(__name__)


def _kapitelpfade(abschnitte: list[dict]) -> list[str]:
    """Pfade "Kapitel > Unterkapitel" in Baumreihenfolge (Tiefensuche nach sort)."""
    kinder: dict[int | None, list[dict]] = {}
    for a in sorted(abschnitte, key=lambda a: (a.get("sort") or 0, a["id"])):
        kinder.setdefault(a.get("parent_id"), []).append(a)
    pfade: list[str] = []

    def besuche(parent: int | None, praefix: str, tiefe: int) -> None:
        if tiefe > 20:
            return
        for a in kinder.get(parent, []):
            pfad = f"{praefix} > {a['titel']}" if praefix else a["titel"]
            pfade.append(pfad)
            besuche(a["id"], pfad, tiefe + 1)

    besuche(None, "", 0)
    return pfade


def render_katplan_html(db: Session, user: User, plan: KatplanPlan, *,
                       version_id: int | None = None, entwurf: bool = False) -> str:
    kopf, elemente, vid, _ = fassung_laden(
        db, user, plan, "arbeitsstand" if entwurf else "freigegeben", version_id,
    )
    _, roh, _ = _rohdaten(db, plan, entwurf, vid)
    nach_uuid = {e["element_uuid"]: e for e in roh}
    for e in elemente:
        e["beschreibung_html"] = bereinige_beschreibung(nach_uuid[e["element_id"]].get("beschreibung_html"))
        e["bilder"] = []
        for a in e["attachments"]:
            if a.get("vertraulich") or not (a.get("mime") or "").startswith("image/"):
                continue
            datei = db.query(KatplanAnhang).filter_by(
                org_id=plan.org_id, plan_id=plan.id, uuid=a["uuid"], vertraulich=False,
            ).one_or_none()
            if datei is None or not datei.thumb_pfad:
                continue
            try:
                data = absolute_pfad(datei.thumb_pfad).read_bytes()
                if len(data) <= 2_000_000:
                    e["bilder"].append("data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"))
            except (OSError, ValueError):
                logger.warning("Anhang-Vorschaubild nicht verfügbar: %s", a["uuid"])
            if len(e["bilder"]) == 2:
                break
    version = db.query(KatplanVersion).filter_by(org_id=plan.org_id, plan_id=plan.id, id=vid).one_or_none()
    freigeber = db.query(User).filter_by(
        org_id=plan.org_id, id=version.freigegeben_von_id,
    ).one_or_none() if version else None
    gruppen: dict[str, list] = {}
    for e in elemente:
        gruppen.setdefault(e["chapter"] or "Ohne Kapitel", []).append(e)
    # Kapitel in Planreihenfolge (Abschnittsbaum), "Ohne Kapitel" ans Ende.
    pfade = _kapitelpfade(kopf.get("abschnitte", []))
    reihenfolge = {pfad: i for i, pfad in enumerate(pfade)}
    kapitel = {name: gruppen[name] for name in sorted(
        gruppen, key=lambda n: (n == "Ohne Kapitel", reihenfolge.get(n, len(reihenfolge)), n))}
    return templates.env.get_template("pdf/katplan.html").render(
        kopf=kopf, kapitel=kapitel, version=version, entwurf=entwurf, org=user.org,
        freigeber=freigeber.display_name if freigeber else "Gelöschter Benutzer",
        gefahrenarten=KATPLAN_GEFAHRENARTEN, now=datetime.now(UTC),
        format_local_datetime=format_local_datetime, logo=organisationslogo_data_uri(db, user.org) if user.org else "",
        plan_links=link_ansichten(db, user, kopf.get("links", [])),
    )


def render_katplan_pdf(db: Session, user: User, plan: KatplanPlan, *,
                      version_id: int | None = None, entwurf: bool = False) -> bytes:
    html = render_katplan_html(db, user, plan, version_id=version_id, entwurf=entwurf)
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except OSError:
        from xhtml2pdf import pisa

        from app.services.pdf_service import strip_font_face_for_xhtml2pdf
        buf = io.BytesIO()
        result = pisa.CreatePDF(io.StringIO(strip_font_face_for_xhtml2pdf(html)), dest=buf)
        if result.err:
            raise ValueError("PDF konnte nicht erstellt werden.") from None
        return buf.getvalue()
