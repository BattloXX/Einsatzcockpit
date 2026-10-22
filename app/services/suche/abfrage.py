"""Deterministischer Abfrageplaner mit RRF und Live-Verifikation."""

import json
import logging
from time import perf_counter
from typing import Literal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.config import settings
from app.models.master import OrgSettings
from app.models.suche import SearchChunk, SearchDocument, SearchOutbox
from app.models.user import User
from app.services.hydrant_service import _haversine_m
from app.services.katplan_suche_service import ausschnitt
from app.services.katplan_zugriff import sieht_vertraulich
from app.services.kontakt_access_service import KontaktZugriff
from app.services.suche import embedding, live, resolver
from app.services.suche.embedding import EmbeddingProvider
from app.services.suche.quellen import html_zu_text
from app.services.suche.synonyme import suchwoerter
from app.services.suche.textsuche import Kandidat, volltext_kandidaten
from app.services.suche.vektor import knn, vektor_faehig
from app.services.suche.vertrag import SuchAnfrage, SuchErgebnis, Treffer

logger = logging.getLogger(__name__)
INDEX_TYPEN = ("objekt", "objekt_dokument_seite", "katplan_element", "katplan_seite")


def fusioniere(listen: list[list[Treffer]]) -> list[Treffer]:
    """Reciprocal Rank Fusion; jede Quelle pro Rangliste nur einmal werten."""
    scores: dict[tuple[str, str], float] = {}
    treffer: dict[tuple[str, str], Treffer] = {}
    for liste in listen:
        gesehen = set()
        for rang, t in enumerate(liste, 1):
            key = (t.quelle, t.quell_id)
            if key in gesehen:
                continue
            gesehen.add(key)
            scores[key] = scores.get(key, 0) + 1 / (60 + rang)
            if key not in treffer:
                treffer[key] = t.model_copy(deep=True)
            else:
                bisher = treffer[key]
                bester_auszug = "volltext" in t.grund and "volltext" not in bisher.grund
                bisher.grund = list(dict.fromkeys([*bisher.grund, *t.grund]))
                if (bester_auszug or not bisher.auszug) and t.auszug:
                    bisher.auszug, bisher.fundstelle = t.auszug, t.fundstelle
    return sorted(treffer.values(), key=lambda t: (
        "exakt" not in t.grund, -scores[(t.quelle, t.quell_id)], t.quelle, t.quell_id,
    ))


def _passt_filter(quelle: str, meta: dict, anfrage: SuchAnfrage) -> bool:
    katplan = quelle in ("katplan_element", "katplan_seite")
    if anfrage.plan_id is not None and (not katplan or meta.get("plan_id") != anfrage.plan_id):
        return False
    quelle_art = anfrage.filter.get("quelle_art")
    if quelle_art is not None and quelle != quelle_art:
        return False
    for feld in ("typ", "prioritaet", "phase_id"):
        wert = anfrage.filter.get(feld)
        schluessel = "phase_ids" if feld == "phase_id" else feld
        # Altbestand ohne die neuen Metadaten bleibt bis zum Reindex auffindbar.
        if wert is None or schluessel not in meta:
            continue
        if feld == "phase_id":
            if str(wert) not in {str(p) for p in meta[schluessel]}:
                return False
        elif str(meta[schluessel]) != str(wert):
            return False
    return True


def _index_treffer(db: Session, user: User, kandidaten: list[Kandidat], woerter: list[str],
                   grund: Literal["exakt", "volltext", "semantisch"],
                   anfrage: SuchAnfrage | None = None) -> list[Treffer]:
    beste: dict[int, Kandidat] = {}
    for k in sorted(kandidaten, key=lambda k: k.rang):
        bisher = beste.get(k.document_id)
        if bisher is None or (bisher.chunk_id is None and k.chunk_id is not None):
            beste[k.document_id] = k
    treffer = []
    for k in beste.values():
        d = db.scalar(select(SearchDocument).where(
            SearchDocument.org_id == user.org_id, SearchDocument.id == k.document_id,
        ))
        if d is None or (d.sichtbarkeit == "vertraulich" and not sieht_vertraulich(user)):
            continue
        meta = json.loads(d.meta_json or "{}")
        if anfrage is not None and not _passt_filter(d.quelle, meta, anfrage):
            continue
        aktuell = resolver.pruefe(db, user, d.quelle, d.quell_id, meta)
        if aktuell is None:
            continue
        query = select(SearchChunk).where(SearchChunk.org_id == user.org_id, SearchChunk.document_id == d.id)
        if k.chunk_id is not None:
            query = query.where(SearchChunk.id == k.chunk_id)
        chunk = db.scalar(query.order_by(SearchChunk.chunk_nr).limit(1))
        text = chunk.text.partition("\n")[2] if chunk else ""
        fundstelle = None
        if chunk:
            teile = ([f"Seite {chunk.seite}"] if chunk.seite is not None else [])
            if chunk.abschnitt:
                teile.append(f"Abschnitt {chunk.abschnitt}")
            fundstelle = ", ".join(teile) or None
        klartext = html_zu_text(text)
        auszug = ausschnitt(klartext, woerter)
        if grund == "semantisch" and not auszug:
            auszug = klartext[:300]
        treffer.append(Treffer(
            quelle=d.quelle, quell_id=d.quell_id, titel=d.titel, untertitel=d.untertitel,
            auszug=auszug, fundstelle=fundstelle,
            status=aktuell.status, ist_aktuell=aktuell.ist_aktuell, grund=[grund],
            url=aktuell.url, lat=d.lat, lng=d.lng, stand=d.quelle_geaendert_am or d.indexiert_am,
        ))
    return treffer


def _exakt(db: Session, user: User, anfrage: SuchAnfrage, woerter: list[str], quellen: list[str]) -> list[Treffer]:
    bedingungen: list[ColumnElement[bool]] = []
    if "objekt" in quellen and anfrage.q.isdecimal():
        bedingungen.append((SearchDocument.quelle == "objekt")
                           & SearchDocument.titel.startswith(anfrage.q + " ", autoescape=True))
    if "katplan_element" in quellen:
        bedingungen.append((SearchDocument.quelle == "katplan_element")
                           & SearchDocument.titel.istartswith(anfrage.q, autoescape=True))
    if woerter:
        titel = func.lower(SearchDocument.titel)
        for umlaut, ersatz in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            titel = func.replace(titel, umlaut, ersatz)
        bedingungen.append(and_(
            SearchDocument.quelle.in_([q for q in quellen if q.startswith("katplan_")]),
            *(titel.contains(w, autoescape=True) for w in woerter),
        ))
    treffer = []
    if bedingungen:
        dokumente = db.scalars(select(SearchDocument).where(
            SearchDocument.org_id == user.org_id, or_(*bedingungen),
        ).order_by(case((SearchDocument.titel.istartswith(anfrage.q, autoescape=True), 0), else_=1),
                   SearchDocument.id).limit(anfrage.limit * 6)).all()
        kandidaten = [Kandidat(d.id, None, r) for r, d in enumerate(dokumente, 1)]
        treffer = _index_treffer(db, user, kandidaten, woerter, "exakt", anfrage)
    if (anfrage.plan_id is None and _passt_filter("einsatz", {}, anfrage)
            and (not anfrage.typen or "einsatz" in anfrage.typen)
            and anfrage.kontext != "gsl"):
        treffer.extend(live.suche_einsaetze(db, user, anfrage, woerter, nur_exakt=True))
    return treffer


def _geo(treffer: list[Treffer], anfrage: SuchAnfrage) -> list[Treffer]:
    if anfrage.lat is None or anfrage.lng is None or anfrage.radius_m is None:
        return treffer
    ergebnis = []
    for t in treffer:
        if t.lat is not None and t.lng is not None:
            distanz = _haversine_m(anfrage.lat, anfrage.lng, t.lat, t.lng)
            if distanz > anfrage.radius_m:
                continue
            t.distanz_m = round(distanz)
        ergebnis.append(t)
    return ergebnis


def suche(db: Session, user: User, anfrage: SuchAnfrage, *,
          kontakt_zugriff: KontaktZugriff = "keiner") -> SuchErgebnis:
    start = perf_counter()
    ergebnis = SuchErgebnis()
    org_id = user.org_id
    if org_id is None:
        ergebnis.hinweise.append("Keine Organisation ausgewählt")
        return ergebnis
    live.pruefe_kontext(db, user, anfrage)
    woerter = suchwoerter(anfrage.q)
    listen: list[list[Treffer]] = []

    def fehler(stufe: str) -> None:
        ergebnis.teilweise = True
        hinweis = f"{stufe} nicht verfügbar"
        if hinweis not in ergebnis.hinweise:
            ergebnis.hinweise.append(hinweis)
        # Keine Exception/SQL-Parameter loggen: sie koennen den Suchtext enthalten.
        logger.warning("suche stufe=%s nicht verfügbar", stufe)

    quellen = [q for q in INDEX_TYPEN if (not anfrage.typen or q in anfrage.typen)
               and (anfrage.kontext == "global" or anfrage.ganze_org or q in anfrage.typen)]
    try:
        with db.begin_nested():
            org_settings = db.scalar(select(OrgSettings).where(OrgSettings.org_id == org_id))
            index_aktiv = bool(org_settings and org_settings.suche_aktiv)
        if not index_aktiv:
            ergebnis.hinweise.append("Suchindex für diese Organisation nicht aktiv")
            quellen = []
    except Exception:
        fehler("Suchindex")
        quellen = []
    try:
        ergebnis.modi.append("exakt")
        with db.begin_nested():
            listen.append(_exakt(db, user, anfrage, woerter, quellen))
    except Exception:
        fehler("Exaktsuche")
    if quellen:
        try:
            ergebnis.modi.append("volltext")
            with db.begin_nested():
                kandidaten = volltext_kandidaten(db, org_id, woerter, quellen=quellen, limit=anfrage.limit * 3)
                treffer = _index_treffer(db, user, kandidaten, woerter, "volltext", anfrage)
                if len(_geo(treffer, anfrage)) < anfrage.limit:
                    kandidaten = volltext_kandidaten(db, org_id, woerter, quellen=quellen, limit=anfrage.limit * 6)
                    treffer = _index_treffer(db, user, kandidaten, woerter, "volltext", anfrage)
                listen.append(treffer)
        except Exception:
            fehler("Volltextsuche")
        try:
            if (settings.SEARCH_SEMANTIC_ENABLED and len(anfrage.q) >= 3
                    and not embedding.breaker_offen() and vektor_faehig(db)):
                ergebnis.modi.append("semantisch")
                with db.begin_nested():
                    vektor = EmbeddingProvider().embed_anfrage(anfrage.q)
                    nachbarn = knn(db, org_id, vektor, quellen, anfrage.limit * 5)
                    kandidaten = [
                        Kandidat(document_id, chunk_id, rang)
                        for rang, (document_id, chunk_id, distanz) in enumerate(
                            sorted(nachbarn, key=lambda n: n[2]), 1,
                        ) if distanz <= settings.SEARCH_SEMANTIC_MAX_DISTANZ
                    ]
                    listen.append(_index_treffer(db, user, kandidaten, woerter, "semantisch", anfrage))
        except Exception:
            fehler("Semantik")
    kontext_typen = {"strassensperre", "wasserstelle"}
    if anfrage.kontext == "gsl":
        kontext_typen.update(("gsl_stelle", "gsl_meldung", "gsl_einheit"))
    else:
        kontext_typen.add("einsatz")
        if anfrage.kontext == "einsatz":
            kontext_typen.add("einsatz_meldung")
    for quelle, anbieter in live.ANBIETER.items():
        if (anfrage.plan_id is not None or quelle not in kontext_typen
                or (anfrage.typen and quelle not in anfrage.typen)
                or not _passt_filter(quelle, {}, anfrage)):
            continue
        if "live" not in ergebnis.modi:
            ergebnis.modi.append("live")
        try:
            with db.begin_nested():
                listen.append(anbieter(db, user, anfrage, woerter))
        except Exception:
            fehler(quelle)
    if (anfrage.plan_id is None and _passt_filter("kontakt", {}, anfrage)
            and anfrage.kontext != "gsl" and (not anfrage.typen or "kontakt" in anfrage.typen)
            and kontakt_zugriff != "keiner"):
        if "live" not in ergebnis.modi:
            ergebnis.modi.append("live")
        try:
            with db.begin_nested():
                listen.append(live.suche_kontakte(db, user, anfrage, woerter, kontakt_zugriff))
        except Exception:
            fehler("Kontakte")
    ergebnis.treffer = _geo(fusioniere(listen), anfrage)[:anfrage.limit]
    try:
        with db.begin_nested():
            ergebnis.index_stand = db.scalar(select(func.min(SearchOutbox.erstellt_am)).where(
                SearchOutbox.org_id == org_id, SearchOutbox.status.in_(("pending", "retry")),
            ))
            if ergebnis.index_stand is None:
                ergebnis.index_stand = db.scalar(select(func.max(SearchDocument.indexiert_am)).where(
                    SearchDocument.org_id == org_id,
                ))
    except Exception:
        fehler("Indexstand")
    logger.info("suche dauer_ms=%d treffer=%d modi=%s teilweise=%s",
                (perf_counter() - start) * 1000, len(ergebnis.treffer), ergebnis.modi, ergebnis.teilweise)
    return ergebnis
