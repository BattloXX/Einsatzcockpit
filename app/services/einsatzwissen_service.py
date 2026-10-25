"""Übersicht auf bestehenden, rechtegeprüften Plänen und Freigaben."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.katplan import KatplanNutzung, KatplanPlan, _utcnow
from app.models.user import User


def merke_zugriff(db: Session, user: User, plan: KatplanPlan) -> None:
    if user.org_id != plan.org_id:
        raise ValueError("Plan nicht verfügbar.")
    query = db.query(KatplanNutzung).filter_by(org_id=user.org_id, user_id=user.id, plan_id=plan.id)
    jetzt = _utcnow()
    if not query.update({"zuletzt_am": jetzt, "aufrufe": KatplanNutzung.aufrufe + 1}):
        try:
            with db.begin_nested():
                db.add(KatplanNutzung(org_id=user.org_id, user_id=user.id, plan_id=plan.id,
                                     zuletzt_am=jetzt, aufrufe=1))
                db.flush()
        except IntegrityError:
            # Zwei gleichzeitige Tabs dürfen denselben Zugriff anlegen.
            query.update({"zuletzt_am": jetzt, "aufrufe": KatplanNutzung.aufrufe + 1})


def zuletzt_verwendet(db: Session, user: User, sichtbar: list[dict]) -> list[dict]:
    """Titel und Berechtigungen bei jedem Abruf neu aus der sichtbaren Fassung beziehen."""
    nach_id = {p["id"]: p for p in sichtbar}
    if not nach_id:
        return []
    nutzungen = db.query(KatplanNutzung).filter(
        KatplanNutzung.org_id == user.org_id, KatplanNutzung.user_id == user.id,
        KatplanNutzung.plan_id.in_(nach_id),
        KatplanNutzung.aufrufe > 0,
    ).order_by(KatplanNutzung.zuletzt_am.desc(), KatplanNutzung.id.desc()).limit(8)
    return [nach_id[n.plan_id] for n in nutzungen]


def setze_favorit(db: Session, user: User, plan: KatplanPlan, aktiv: bool) -> None:
    if user.org_id != plan.org_id:
        raise ValueError("Plan nicht verfügbar.")
    query = db.query(KatplanNutzung).filter_by(org_id=user.org_id, user_id=user.id, plan_id=plan.id)
    if not query.update({"favorit": aktiv}):
        try:
            with db.begin_nested():
                db.add(KatplanNutzung(org_id=user.org_id, user_id=user.id, plan_id=plan.id, favorit=aktiv))
                db.flush()
        except IntegrityError:
            query.update({"favorit": aktiv})


def schnellzugriff(db: Session, user: User, sichtbar: list[dict]) -> dict:
    nach_id = {p["id"]: p for p in sichtbar}
    nutzungen = db.query(KatplanNutzung).filter_by(org_id=user.org_id, user_id=user.id).all()
    favoriten = {n.plan_id for n in nutzungen if n.favorit and n.plan_id in nach_id}
    haeufig = sorted((n for n in nutzungen if n.plan_id in nach_id and n.aufrufe > 0
                      and nach_id[n.plan_id]["kategorie"] in ("einsatzplaene", "katastrophenschutz")),
                     key=lambda n: (-n.aufrufe, -n.zuletzt_am.timestamp(), n.plan_id))[:8]
    return {"favorit_ids": favoriten, "favoriten": [p for p in sichtbar if p["id"] in favoriten],
            "haeufig": [nach_id[n.plan_id] for n in haeufig]}


BEREICHE = (
    ("sofort", "Sofortmaßnahmen"), ("sicherheit", "Gefahren und Sicherheitshinweise"),
    ("massnahmen", "Weitere Maßnahmen"), ("dokumente", "Dokumente"),
    ("kontakte", "Kontakte und Sondereinsatzmittel"), ("verwandt", "Verwandte Informationen"),
)


def einsatzbereiche(context: dict) -> list[dict]:
    """Ausschließlich die bereits rechtegeprüfte Freigabe gliedern; nichts duplizieren."""
    gruppen: dict[str, list] = {code: [] for code, _ in BEREICHE}

    def sammeln(knoten, sofortkapitel=False, sicherheitskapitel=False):
        for k in knoten:
            kapitel = (k.get("abschnitt") or {}).get("titel", "").casefold()
            sofort = sofortkapitel or any(w in kapitel for w in ("sofortmaß", "erstmaß", "sofortmass", "erstmass"))
            sicherheit = sicherheitskapitel or any(w in kapitel for w in ("gefahren", "sicherheit"))
            for e in sorted(k["elemente"], key=lambda e: (e.prioritaet, e.sort, e.id)):
                daten = context["element_daten"][e.id]
                if e.typ in ("gefahrenbereich", "kontrollstelle") or (e.typ == "info" and (
                    sicherheit or daten.get("warnhinweis") or daten.get("kategorie", "").casefold() == "sicherheit")):
                    code = "sicherheit"
                elif e.typ in ("massnahme", "ablauf"):
                    code = "sofort" if e.prioritaet == 1 or sofort else "massnahmen"
                elif e.typ in ("zustaendigkeit", "ressource"):
                    code = "kontakte"
                elif e.typ == "medien":
                    code = "dokumente"
                else:
                    code = "verwandt"
                gruppen[code].append(e)
            sammeln(k["kinder"], sofort, sicherheit)

    sammeln(context["version_baum"])
    return [{"id": code, "titel": titel, "elemente": sorted(gruppen[code],
             key=lambda e: (e.prioritaet, e.sort, e.id))} for code, titel in BEREICHE]
