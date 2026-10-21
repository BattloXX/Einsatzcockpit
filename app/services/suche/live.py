"""Strukturierte Live-Anbieter; ausschliesslich fachliche Textfelder."""

from calendar import monthrange
from datetime import UTC, datetime
from math import cos, radians
from typing import cast

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.permissions import STRASSENSPERREN_LESE_ROLLEN, can_access_incident, has_role
from app.core.queries import visible_incidents_q
from app.core.telefon import telefon_normalisiert
from app.models.incident import Incident, Message, Task
from app.models.kontakt import Kontakt, KontaktKategorie, KontaktKategorieZuordnung, KontaktTelefon
from app.models.major_incident import (
    CommLogEntry,
    IncidentSite,
    LageEinheit,
    LageJournalEntry,
    MajorIncident,
    SiteLogEntry,
    SitePhase,
)
from app.models.road_closure import CLOSURE_STATUS
from app.models.user import User
from app.models.wasserstelle import Wasserstelle
from app.services.hydrant_service import _haversine_m
from app.services.katplan_suche_service import ausschnitt
from app.services.kontakt_access_service import KontaktZugriff, einsatz_kontakte
from app.services.major_incident_service import PHASE_LABELS
from app.services.mi_feature_service import get_mi_features
from app.services.road_closure_flags import strassensperren_effective_enabled
from app.services.road_closure_service import active_closures_in_bbox, compute_status, visible_closures_q
from app.services.suche.quellen import html_zu_text
from app.services.suche.synonyme import entferne_telefonnummern, expandiere, normalisiere
from app.services.suche.vertrag import SuchAnfrage, Treffer


def _text(*werte: str | None) -> str:
    return " ".join(w for w in werte if w)


def _auszug(text: str, woerter: list[str]) -> str:
    # Alarm- und Meldungstexte koennen Anrufer-Nummern enthalten; wie im Index entfernen.
    return ausschnitt(entferne_telefonnummern(text), woerter)


# Wie die Leserollen des GSL-Boards (ui_major_incident.lage_board).
_GSL_LESE_ROLLEN = ("incident_leader", "admin", "org_admin", "recorder", "readonly")


def _passt(text: str, woerter: list[str]) -> bool:
    text = normalisiere(text)
    return bool(woerter) and all(any(w in text for w in expandiere([wort])) for wort in woerter)


def _sortiere(treffer: list[Treffer], anfrage: SuchAnfrage) -> list[Treffer]:
    # Titeluebereinstimmungen vor Inhaltsfunden, bei gleichem Rang neueste zuerst.
    return sorted(treffer, key=lambda t: (
        not _passt(t.titel, [normalisiere(anfrage.q)]),
        -(t.stand.replace(tzinfo=UTC).timestamp() if t.stand else 0), t.quell_id,
    ))[:anfrage.limit * 3]


def pruefe_kontext(db: Session, user: User, anfrage: SuchAnfrage) -> None:
    if anfrage.kontext == "gsl":
        if not has_role(user, *_GSL_LESE_ROLLEN):
            raise PermissionError("Kein Zugriff auf diese Lage")
        lage = db.query(MajorIncident).filter(
            MajorIncident.org_id == user.org_id, MajorIncident.id == anfrage.kontext_id,
        ).first()
        if lage is None:
            raise PermissionError("Kein Zugriff auf diese Lage")
    elif anfrage.kontext == "einsatz":
        einsatz = visible_incidents_q(db, user).filter(Incident.id == anfrage.kontext_id).first()
        if einsatz is None or not can_access_incident(user, einsatz):
            raise PermissionError("Kein Zugriff auf diesen Einsatz")


def _stellen(db: Session, user: User, anfrage: SuchAnfrage) -> list[IncidentSite]:
    pruefe_kontext(db, user, anfrage)
    return db.query(IncidentSite).filter(
        IncidentSite.org_id == user.org_id, IncidentSite.major_incident_id == anfrage.kontext_id,
    ).all()


def suche_gsl_stellen(db: Session, user: User, anfrage: SuchAnfrage, woerter: list[str]) -> list[Treffer]:
    if anfrage.kontext != "gsl":
        return []
    treffer = []
    for s in _stellen(db, user, anfrage):
        aktuell = s.phase not in (SitePhase.erledigt, SitePhase.abgebrochen)
        text = _text(s.bezeichnung, s.einsatzgrund, s.strasse, s.hausnr, s.ort)
        if (aktuell or anfrage.inkl_erledigt) and _passt(text, woerter):
            treffer.append(Treffer(
                quelle="gsl_stelle", quell_id=str(s.id), titel=s.bezeichnung,
                auszug=_auszug(text, woerter), status=PHASE_LABELS[s.phase],
                ist_aktuell=aktuell, grund=["live"], lat=s.lat, lng=s.lng,
                url=f"/lage/{anfrage.kontext_id}?site={s.id}",
            ))
    return _sortiere(treffer, anfrage)


def suche_gsl_meldungen(db: Session, user: User, anfrage: SuchAnfrage, woerter: list[str]) -> list[Treffer]:
    if anfrage.kontext != "gsl":
        return []
    stellen = {s.id: s for s in _stellen(db, user, anfrage)}
    treffer: list[Treffer] = []

    def hinzufuegen(kennung: str, text: str, ts: datetime, site_id: int | None, handled: bool = False) -> None:
        if handled and not anfrage.inkl_erledigt:
            return
        if not _passt(text, woerter):
            return
        stelle = stellen.get(site_id) if site_id is not None else None
        treffer.append(Treffer(
            quelle="gsl_meldung", quell_id=kennung, titel=entferne_telefonnummern(text)[:120],
            untertitel=stelle.bezeichnung if stelle else None, auszug=_auszug(text, woerter),
            status="Erledigt" if handled else "Offen", ist_aktuell=not handled, stand=ts,
            grund=["live"], url=f"/lage/{anfrage.kontext_id}" + (f"?site={stelle.id}" if stelle else ""),
        ))

    for log in db.query(SiteLogEntry).filter(SiteLogEntry.incident_site_id.in_(stellen)).all():
        hinzufuegen(f"stelle:{log.id}", log.text, log.ts, log.incident_site_id)
    # Das Lagejournal hat wie in gsl_mcp_service.lagebild keinen Feature-Schalter.
    for journal in db.query(LageJournalEntry).join(
        MajorIncident, LageJournalEntry.major_incident_id == MajorIncident.id,
    ).filter(
        MajorIncident.org_id == user.org_id, MajorIncident.id == anfrage.kontext_id,
        LageJournalEntry.storniert_at.is_(None),
    ).all():
        hinzufuegen(f"journal:{journal.id}", _text(journal.text, html_zu_text(journal.body_html)),
                    journal.ts, journal.site_id)
    if get_mi_features(db, user.org_id)["funkjournal"]:
        for funk in db.query(CommLogEntry).join(
            MajorIncident, CommLogEntry.major_incident_id == MajorIncident.id,
        ).filter(MajorIncident.org_id == user.org_id, MajorIncident.id == anfrage.kontext_id).all():
            hinzufuegen(f"funk:{funk.id}", funk.message, funk.ts, funk.related_site_id, funk.handled)
    return _sortiere(treffer, anfrage)


def suche_gsl_einheiten(db: Session, user: User, anfrage: SuchAnfrage, woerter: list[str]) -> list[Treffer]:
    if anfrage.kontext != "gsl":
        return []
    stellen = {s.id: s for s in _stellen(db, user, anfrage)}
    treffer = []
    for e in db.query(LageEinheit).join(MajorIncident, LageEinheit.lage_id == MajorIncident.id).filter(
        MajorIncident.org_id == user.org_id, MajorIncident.id == anfrage.kontext_id,
    ).all():
        stelle = stellen.get(e.incident_site_id) if e.incident_site_id is not None else None
        text = _text(e.funkrufname, e.label, e.org_name, e.status, stelle.bezeichnung if stelle else None)
        if (e.released_at is None or anfrage.inkl_erledigt) and _passt(text, woerter):
            treffer.append(Treffer(
                quelle="gsl_einheit", quell_id=str(e.id), titel=e.funkrufname or e.label,
                untertitel=stelle.bezeichnung if stelle else None, auszug=_auszug(text, woerter),
                status="Freigegeben" if e.released_at else e.status, ist_aktuell=e.released_at is None,
                grund=["live"], stand=e.status_at, url=f"/lage/{anfrage.kontext_id}",
            ))
    return _sortiere(treffer, anfrage)


def suche_einsaetze(db: Session, user: User, anfrage: SuchAnfrage, woerter: list[str],
                   *, nur_exakt: bool = False) -> list[Treffer]:
    if anfrage.kontext == "gsl":
        return []
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    grenze = jetzt.replace(year=jetzt.year - 2, day=min(
        jetzt.day, monthrange(jetzt.year - 2, jetzt.month)[1],
    ))
    query = visible_incidents_q(db, user)
    # Alte Einsaetze ausschliesslich bei exakter Leitstellennummer laden.
    query = query.filter((Incident.started_at >= grenze)
                         | (func.lower(Incident.lis_operation_number) == anfrage.q.lower()))
    treffer = []
    for e in query.all():
        exakt = bool(e.lis_operation_number and normalisiere(e.lis_operation_number) == normalisiere(anfrage.q))
        text = _text(e.alarm_type_code, e.reason, e.address_street, e.address_no, e.address_city,
                     e.lis_operation_number, e.report_text)
        if (nur_exakt and not exakt) or (not exakt and not _passt(text, woerter)):
            continue
        treffer.append(Treffer(
            quelle="einsatz", quell_id=str(e.id), titel=_text(e.alarm_type_code, e.reason) or "Einsatz",
            untertitel=_text(e.address_street, e.address_no, e.address_city), auszug=_auszug(text, woerter),
            status=e.status, ist_aktuell=e.closed_at is None and e.status not in ("closed", "abgeschlossen"),
            grund=["exakt", "live"] if exakt else ["live"], url=f"/einsatz/{e.id}",
            lat=e.lat, lng=e.lng, stand=e.started_at,
        ))
    return _sortiere(treffer, anfrage)


def suche_einsatz_meldungen(db: Session, user: User, anfrage: SuchAnfrage, woerter: list[str]) -> list[Treffer]:
    if anfrage.kontext != "einsatz":
        return []
    pruefe_kontext(db, user, anfrage)
    treffer = []
    for modell, kennung in ((Task, "auftrag"), (Message, "meldung")):
        for m in cast(list[Task | Message], db.query(modell).filter(
            modell.incident_id == anfrage.kontext_id,
        ).all()):
            aktuell = not (m.is_done or m.is_cancelled or m.status in ("done", "cancelled", "erledigt", "storniert"))
            text = _text(m.title, m.detail, m.status)
            if (aktuell or anfrage.inkl_erledigt) and _passt(text, woerter):
                anker = f"task-card-{m.id}" if kennung == "auftrag" else f"msg-card-{m.id}"
                treffer.append(Treffer(
                    quelle="einsatz_meldung", quell_id=f"{kennung}:{m.id}", titel=m.title,
                    auszug=_auszug(text, woerter), status=m.status, ist_aktuell=aktuell,
                    grund=["live"], stand=m.created_at, url=f"/einsatz/{anfrage.kontext_id}#{anker}",
                ))
    return _sortiere(treffer, anfrage)


def suche_sperren(db: Session, user: User, anfrage: SuchAnfrage, woerter: list[str]) -> list[Treffer]:
    if not strassensperren_effective_enabled(user.org_id, db) or not has_role(user, *STRASSENSPERREN_LESE_ROLLEN):
        return []
    assert user.org_id is not None
    lat, lng = anfrage.lat, anfrage.lng
    if anfrage.kontext == "einsatz":
        pruefe_kontext(db, user, anfrage)
        if lat is None:
            einsatz = visible_incidents_q(db, user).filter(Incident.id == anfrage.kontext_id).one()
            lat, lng = einsatz.lat, einsatz.lng
    nah: set[int] = set()
    if (anfrage.kontext != "global" and lat is not None and lng is not None
            and any(w in ("sperre", "gesperrt", "umleitung", "anfahrt") for w in woerter)):
        radius = anfrage.radius_m or 5000
        dy = radius / 111320
        dx = dy / max(abs(cos(radians(lat))), 0.01)
        nah = {s.id for s in active_closures_in_bbox(db, user.org_id, lat - dy, lng - dx, lat + dy, lng + dx)}
    treffer = []
    for s in visible_closures_q(db, user.org_id).all():
        status = compute_status(s)
        aktuell = status == "active"
        text = _text(s.title, s.street, s.from_text, s.to_text, s.reason, s.city, s.description)
        if (aktuell or anfrage.inkl_erledigt) and (_passt(text, woerter) or s.id in nah):
            treffer.append(Treffer(
                quelle="strassensperre", quell_id=str(s.id), titel=s.title, auszug=_auszug(text, woerter),
                status=CLOSURE_STATUS[status], ist_aktuell=aktuell, grund=["live"],
                stand=s.updated_at, url=f"/strassensperren/{s.id}",
            ))
    return _sortiere(treffer, anfrage)


def suche_wasserstellen(db: Session, user: User, anfrage: SuchAnfrage, woerter: list[str]) -> list[Treffer]:
    if not has_role(user, *STRASSENSPERREN_LESE_ROLLEN):
        return []
    treffer = []
    for w in db.query(Wasserstelle).filter(Wasserstelle.org_id == user.org_id).all():
        text = _text(w.bezeichnung, w.typ, w.typ_label, w.hinweis)
        if (not w.aktiv and not anfrage.inkl_erledigt) or not _passt(text, woerter):
            continue
        distanz = None
        if anfrage.lat is not None and anfrage.lng is not None and anfrage.radius_m is not None:
            if w.lat is None or w.lng is None:
                continue
            distanz = round(_haversine_m(anfrage.lat, anfrage.lng, w.lat, w.lng))
            if distanz > anfrage.radius_m:
                continue
        treffer.append(Treffer(
            quelle="wasserstelle", quell_id=str(w.id), titel=w.bezeichnung, untertitel=w.typ_label,
            auszug=_auszug(text, woerter), status=w.status_label, ist_aktuell=w.aktiv,
            grund=["live"], lat=w.lat, lng=w.lng, distanz_m=distanz, stand=w.aktualisiert_am,
            url="/admin/wasserstellen" if has_role(user, "org_admin") else "",
        ))
    if anfrage.lat is not None:
        return sorted(treffer, key=lambda t: (
            t.distanz_m if t.distanz_m is not None else float("inf"), t.quell_id,
        ))[:anfrage.limit * 3]
    return _sortiere(treffer, anfrage)


ANBIETER = {
    "gsl_stelle": suche_gsl_stellen,
    "gsl_meldung": suche_gsl_meldungen,
    "gsl_einheit": suche_gsl_einheiten,
    "einsatz": suche_einsaetze,
    "einsatz_meldung": suche_einsatz_meldungen,
    "strassensperre": suche_sperren,
    "wasserstelle": suche_wasserstellen,
}


def suche_kontakte(db: Session, user: User, anfrage: SuchAnfrage, woerter: list[str],
                   zugriff: KontaktZugriff) -> list[Treffer]:
    if zugriff == "keiner" or anfrage.kontext == "gsl" or (zugriff == "einsatz" and anfrage.kontext != "einsatz"):
        return []
    nummer = telefon_normalisiert(anfrage.q)
    ist_nummer = nummer.lstrip("+").isdigit() and sum(c.isdigit() for c in nummer) >= 6
    exakte_ids: set[int] = set()
    if zugriff == "einsatz":
        pruefe_kontext(db, user, anfrage)
        einsatz = visible_incidents_q(db, user).filter(Incident.id == anfrage.kontext_id).first()
        if einsatz is None or ist_nummer:
            return []
        kontakte = [k for k, _ in einsatz_kontakte(db, einsatz) if k.org_id == user.org_id]
    else:
        basis = db.query(Kontakt).filter(Kontakt.org_id == user.org_id, Kontakt.archiviert.is_(False))
        if not anfrage.inkl_erledigt:
            basis = basis.filter(Kontakt.aktiv.is_(True))
        kategorien = db.query(KontaktKategorieZuordnung.kontakt_id).join(
            KontaktKategorie, KontaktKategorie.id == KontaktKategorieZuordnung.kategorie_id,
        ).filter(
            KontaktKategorieZuordnung.org_id == user.org_id, KontaktKategorie.org_id == user.org_id,
            func.lower(KontaktKategorie.name).contains(woerter[0] if woerter else "", autoescape=True),
        )
        kontakte = basis.filter(or_(
            *(func.lower(feld).contains(woerter[0] if woerter else "", autoescape=True) for feld in (
                Kontakt.anzeigename, Kontakt.vorname, Kontakt.nachname, Kontakt.funktion,
                Kontakt.organisation, Kontakt.erreichbarkeit,
            )), Kontakt.id.in_(kategorien),
        )).order_by(Kontakt.id).limit(anfrage.limit * 6).all()
        if ist_nummer:
            # Nationale und internationale oesterreichische Schreibweise abgleichen.
            suchnummer = nummer[3:] if nummer.startswith("+43") else nummer
            if suchnummer.startswith("0"):
                suchnummer = suchnummer[1:]
            nummern = db.query(KontaktTelefon.kontakt_id).filter(
                KontaktTelefon.org_id == user.org_id,
                or_(KontaktTelefon.nummer_normalisiert == nummer,
                    KontaktTelefon.nummer_normalisiert.endswith(suchnummer, autoescape=True)),
            )
            exakt = basis.filter(Kontakt.id.in_(nummern)).order_by(Kontakt.id).limit(anfrage.limit * 6).all()
            exakte_ids = {k.id for k in exakt}
            kontakte = list({k.id: k for k in [*kontakte, *exakt]}.values())
    treffer = []
    relevante_ids = {str(k.id) for k in kontakte if k.einsatzrelevant}
    for k in kontakte:
        text = _text(k.anzeigename, k.vorname, k.nachname, k.funktion, k.organisation,
                     *(z.kategorie.name for z in k.kategorien if z.kategorie.org_id == user.org_id), k.erreichbarkeit)
        if k.id not in exakte_ids and not _passt(text, woerter):
            continue
        telefone = [tel for tel in k.telefone if tel.aktiv and tel.org_id == user.org_id
                    and (zugriff == "voll" or tel.verwendung == "dienst")]
        if zugriff == "voll":
            telefone.sort(key=lambda tel: not tel.bevorzugt)
        treffer.append(Treffer(
            quelle="kontakt", quell_id=str(k.id), titel=k.anzeigename,
            untertitel=" · ".join(w for w in (k.funktion, k.organisation) if w) or None,
            auszug=_auszug(text, woerter), status="Aktiv" if k.aktiv else "Inaktiv",
            ist_aktuell=k.aktiv, stand=k.aktualisiert_am,
            grund=["exakt", "live"] if k.id in exakte_ids else ["live"],
            url=f"/kontakte/{k.id}" if zugriff == "voll" else f"/einsatz/{anfrage.kontext_id}#einsatz-kontakte",
            telefone=[{"nummer": tel.nummer, "label": tel.label or ""} for tel in telefone[:2]],
        ))
    # Titelrang bleibt vorrangig; bei gleichem Rang einsatzrelevante Kontakte zuerst.
    return sorted(treffer, key=lambda t: (
        not _passt(t.titel, [normalisiere(anfrage.q)]), t.quell_id not in relevante_ids,
        -(t.stand.replace(tzinfo=UTC).timestamp() if t.stand else 0), t.quell_id,
    ))[:anfrage.limit * 3]
