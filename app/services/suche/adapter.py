"""Explizite Feld-Whitelists fuer die vier ruhenden Suchquellen."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.katplan import (
    KatplanDokument,
    KatplanDokumentSeite,
    KatplanPlan,
    KatplanVersion,
    KatplanVersionElement,
)
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_STATUS_UEBERARBEITUNG,
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektGefahr,
    ObjektKategorie,
    ObjektMerkmal,
    ObjektZusatzadresse,
)
from app.services.katplan_akteur_service import schluessel_lesen
from app.services.katplan_typen import ELEMENT_TYPEN, PLANARTEN
from app.services.suche.quellen import (
    IndexChunk,
    IndexDokument,
    html_zu_text,
    registriere,
    zerlege_text,
)
from app.services.suche.synonyme import entferne_telefonnummern, expandiere, normalisiere


def _text(*werte: str | int | float | None) -> str:
    return " ".join(str(w) for w in werte if w is not None and str(w).strip())


def _adresse(strasse: str | None, hausnummer: str | None, plz: str | None, ort: str | None) -> str:
    return ", ".join(w for w in (_text(strasse, hausnummer), _text(plz, ort)) if w)


def _keywords(titel: str, *werte: str | int | None) -> str:
    text = entferne_telefonnummern(_text(titel, *werte))
    return " ".join(expandiere(normalisiere(text).split()))


def _chunk(titel: str, text: str, seite: int | None = None, abschnitt: str | None = None) -> IndexChunk:
    return IndexChunk(entferne_telefonnummern(f"{titel}\n{text}"), seite, abschnitt)


def _objekte(org_id: int):
    return select(Objekt).where(
        Objekt.org_id == org_id, Objekt.entwurf_von_id.is_(None),
        Objekt.status.in_((OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_UEBERARBEITUNG)),
    )


class ObjektAdapter:
    quelle = "objekt"

    def alle_ids(self, db: Session, org_id: int) -> list[str]:
        return [str(o.id) for o in db.scalars(_objekte(org_id).order_by(Objekt.id))]

    def dokument(self, db: Session, org_id: int, quell_id: str) -> IndexDokument | None:
        o = db.scalar(_objekte(org_id).where(Objekt.id == quell_id))
        if o is None:
            return None
        titel = entferne_telefonnummern(_text(o.nummer, o.name))
        adresse = _adresse(o.strasse, o.hausnummer, o.plz, o.ort)
        kategorie = db.scalar(select(ObjektKategorie.name).where(
            ObjektKategorie.org_id == org_id, ObjektKategorie.id == o.kategorie_id,
        ))
        zusatz = db.scalars(select(ObjektZusatzadresse).where(
            ObjektZusatzadresse.org_id == org_id, ObjektZusatzadresse.objekt_id == o.id,
        ).order_by(ObjektZusatzadresse.sort, ObjektZusatzadresse.id))
        stamm = [o.name, o.vulgoname, kategorie, adresse]
        stamm.extend(_text(z.bezeichnung, _adresse(z.strasse, z.hausnummer, z.plz, z.ort)) for z in zusatz)
        stamm.extend([o.informationen, o.anfahrtsweg])
        chunks = [_chunk(titel, "\n".join(w for w in stamm if w), abschnitt="Stammdaten")]
        namen: list[str] = []
        details: list[str] = []
        merkmale = db.execute(select(ObjektMerkmal, MerkmalKatalog.name).join(
            MerkmalKatalog, ObjektMerkmal.merkmal_id == MerkmalKatalog.id,
        ).where(
            ObjektMerkmal.org_id == org_id, MerkmalKatalog.org_id == org_id,
            ObjektMerkmal.objekt_id == o.id,
        ).order_by(ObjektMerkmal.id))
        for merkmal, name in merkmale:
            namen.append(name)
            details.append(_text(name, merkmal.hinweis))
        gefahren = db.execute(select(ObjektGefahr, GefahrenKatalog.name).join(
            GefahrenKatalog, ObjektGefahr.gefahr_id == GefahrenKatalog.id,
        ).where(
            ObjektGefahr.org_id == org_id, GefahrenKatalog.org_id == org_id,
            ObjektGefahr.objekt_id == o.id,
        ).order_by(ObjektGefahr.sort, ObjektGefahr.id))
        for gefahr, name in gefahren:
            namen.append(name)
            details.append(_text(name, gefahr.stoffname, "UN", gefahr.un_nummer,
                                 "Gefahrklasse", gefahr.gefahrklasse,
                                 "Gefahrnummer", gefahr.gefahrnummer, gefahr.detail))
        if details:
            chunks.append(_chunk(titel, "\n".join(details), abschnitt="Merkmale und Gefahren"))
        bma = db.scalar(select(ObjektBMA).where(ObjektBMA.org_id == org_id, ObjektBMA.objekt_id == o.id))
        if bma is not None:
            chunks.append(_chunk(titel, _text(
                "BMA", bma.bma_nummer, "RFL", bma.rfl_nummer, "BMZ", bma.bmz_standort,
                "FBF", bma.fbf_standort, bma.laufkarten_ablageort,
                bma.uebertragungseinrichtung, bma.schluesselsafe_standort,
            ), abschnitt="BMA"))
        return IndexDokument(
            quell_id=str(o.id), titel=titel, untertitel=entferne_telefonnummern(adresse) or None,
            keywords=_keywords(titel, o.vulgoname, kategorie, o.strasse, o.ort,
                               *namen, "bma bmz" if bma is not None else None),
            chunks=chunks, lat=o.lat, lng=o.lng, meta={"objekt_id": o.id},
            geaendert_am=o.aktualisiert_am,
        )


def _objekt_seiten(org_id: int):
    return select(ObjektDokumentSeite, ObjektDokument, Objekt).join(
        ObjektDokument, ObjektDokumentSeite.dokument_id == ObjektDokument.id,
    ).join(Objekt, ObjektDokument.objekt_id == Objekt.id).where(
        ObjektDokumentSeite.org_id == org_id, ObjektDokument.org_id == org_id, Objekt.org_id == org_id,
        ObjektDokumentSeite.objekt_id == Objekt.id,
        Objekt.entwurf_von_id.is_(None),
        Objekt.status.in_((OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_UEBERARBEITUNG)),
        ObjektDokument.freigabe_status == "freigegeben", ObjektDokument.ist_aktuelle_version.is_(True),
    )


class ObjektDokumentAdapter:
    quelle = "objekt_dokument_seite"

    def alle_ids(self, db: Session, org_id: int) -> list[str]:
        return [str(s.id) for s, _, _ in db.execute(_objekt_seiten(org_id))
                if (s.volltext or "").strip() or (s.titel or "").strip()]

    def dokument(self, db: Session, org_id: int, quell_id: str) -> IndexDokument | None:
        zeile = db.execute(_objekt_seiten(org_id).where(ObjektDokumentSeite.id == quell_id)).first()
        if zeile is None:
            return None
        s, d, o = zeile
        if not (s.volltext or "").strip() and not (s.titel or "").strip():
            return None
        titel = entferne_telefonnummern(s.titel or f"{d.dateiname_original}, Seite {s.seiten_nr}")
        return IndexDokument(
            quell_id=str(s.id), titel=titel, untertitel=o.name,
            keywords=_keywords(titel, s.dokumentart, o.name),
            chunks=[_chunk(titel, t, s.seiten_nr, s.dokumentart)
                    for t in zerlege_text(entferne_telefonnummern(s.volltext or "")) or [""]],
            meta={"objekt_id": o.id, "dokument_id": d.id, "seiten_nr": s.seiten_nr},
        )


def _elemente(org_id: int):
    return select(KatplanVersionElement, KatplanVersion, KatplanPlan).join(
        KatplanVersion, KatplanVersionElement.version_id == KatplanVersion.id,
    ).join(KatplanPlan, KatplanPlan.aktuelle_version_id == KatplanVersion.id).where(
        KatplanVersionElement.org_id == org_id, KatplanVersion.org_id == org_id,
        KatplanPlan.org_id == org_id, KatplanPlan.status != "archiviert",
        KatplanVersion.plan_id == KatplanPlan.id,
    )


def _json_text(roh: str | None) -> str:
    try:
        werte = json.loads(roh or "{}")
    except ValueError:
        return ""
    if isinstance(werte, dict):
        werte = [v for k, v in werte.items()
                 if not any(w in k.casefold() for w in ("telefon", "tel", "mobil", "mail", "email"))]
    if not isinstance(werte, list):
        return ""
    return _text(*(v for v in werte if type(v) in (str, int, float)))


def _plandaten(version: KatplanVersion) -> dict[str, Any]:
    try:
        daten = json.loads(version.plan_json or "{}")
    except ValueError:
        return {}
    return daten if isinstance(daten, dict) else {}


def _plantitel(daten: dict[str, Any], plan: KatplanPlan) -> str:
    return daten["titel"] if isinstance(daten.get("titel"), str) else plan.titel


def _akteurnamen(roh: str | None, daten: dict[str, Any]) -> list[str]:
    katalog = daten.get("akteure", [])
    if not isinstance(katalog, list):
        katalog = []
    namen = {a["schluessel"]: a["bezeichnung"] for a in katalog
             if isinstance(a, dict) and isinstance(a.get("schluessel"), str)
             and isinstance(a.get("bezeichnung"), str) and a["bezeichnung"].strip()}
    return [namen.get(s, s) for s in schluessel_lesen(roh)]


def _linktexte(roh: str | None) -> str:
    try:
        links = json.loads(roh or "[]")
    except ValueError:
        return ""
    if not isinstance(links, list):
        return ""
    zeilen = []
    for link in links:
        if not isinstance(link, dict):
            continue
        snapshot = link.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        text = _text(*(w for w in (snapshot.get("titel"), snapshot.get("untertitel"), link.get("hinweis"))
                       if isinstance(w, str)))
        if text:
            zeilen.append(text)
    return entferne_telefonnummern("\n".join(zeilen))


class KatplanElementAdapter:
    quelle = "katplan_element"

    def alle_ids(self, db: Session, org_id: int) -> list[str]:
        return [f"{p.id}:{e.element_uuid}" for e, _, p in db.execute(_elemente(org_id))]

    def dokument(self, db: Session, org_id: int, quell_id: str) -> IndexDokument | None:
        plan_id, trenner, uuid = quell_id.partition(":")
        if not trenner:
            return None
        zeile = db.execute(_elemente(org_id).where(
            KatplanPlan.id == plan_id, KatplanVersionElement.element_uuid == uuid,
        )).first()
        if zeile is None:
            return None
        e, v, p = zeile
        daten = _plandaten(v)
        code = daten.get("planart") or p.planart
        planart = PLANARTEN.get(code) if isinstance(code, str) else None
        akteure = _akteurnamen(e.akteure_json, daten)
        titel = entferne_telefonnummern(_text(e.kennung, e.titel))
        typ = ELEMENT_TYPEN[e.typ].label if e.typ in ELEMENT_TYPEN else e.typ
        text = "\n".join(w for w in (
            typ, planart.label if planart else "", html_zu_text(e.beschreibung_html),
            _adresse(e.strasse, e.hausnummer, e.plz, e.ort),
            _json_text(e.daten_json), _json_text(e.tags_json),
            "Akteure: " + ", ".join(akteure) if akteure else "", _linktexte(e.links_json),
        ) if w)
        return IndexDokument(
            quell_id=f"{p.id}:{e.element_uuid}", titel=titel, untertitel=_plantitel(daten, p),
            keywords=_keywords(titel, typ, _json_text(e.tags_json),
                               planart.label if planart else None, planart.kuerzel if planart else None,
                               *akteure), chunks=[_chunk(titel, text)],
            sichtbarkeit="vertraulich" if e.vertraulich else "standard", lat=e.lat, lng=e.lng,
            meta={"plan_id": p.id, "version_id": v.id, "element_uuid": e.element_uuid},
            geaendert_am=v.freigegeben_am,
        )


def _katplan_seiten(org_id: int):
    return select(KatplanDokumentSeite, KatplanDokument, KatplanPlan).join(
        KatplanDokument, KatplanDokumentSeite.dokument_id == KatplanDokument.id,
    ).join(KatplanPlan, KatplanDokument.plan_id == KatplanPlan.id).where(
        KatplanDokumentSeite.org_id == org_id, KatplanDokument.org_id == org_id,
        KatplanPlan.org_id == org_id, KatplanPlan.status != "archiviert",
        KatplanPlan.aktuelle_version_id.is_not(None), KatplanDokument.archiviert.is_(False),
        KatplanDokumentSeite.plan_id == KatplanPlan.id,
    )


class KatplanSeiteAdapter:
    quelle = "katplan_seite"

    def alle_ids(self, db: Session, org_id: int) -> list[str]:
        return [str(s.id) for s, _, _ in db.execute(_katplan_seiten(org_id)) if (s.volltext or "").strip()]

    def dokument(self, db: Session, org_id: int, quell_id: str) -> IndexDokument | None:
        zeile = db.execute(_katplan_seiten(org_id).where(KatplanDokumentSeite.id == quell_id)).first()
        if zeile is None:
            return None
        s, d, p = zeile
        if not (s.volltext or "").strip():
            return None
        titel = entferne_telefonnummern(s.titel or f"{d.titel}, Seite {s.seiten_nr}")
        planart = PLANARTEN.get(p.planart)
        return IndexDokument(
            quell_id=str(s.id), titel=titel, untertitel=p.titel,
            keywords=_keywords(titel, p.titel, planart.label if planart else None,
                               planart.kuerzel if planart else None),
            chunks=[_chunk(titel, t, s.seiten_nr)
                    for t in zerlege_text(entferne_telefonnummern(s.volltext))],
            meta={"plan_id": p.id, "dokument_id": d.id, "seiten_nr": s.seiten_nr},
        )


registriere(ObjektAdapter())
registriere(ObjektDokumentAdapter())
registriere(KatplanElementAdapter())
registriere(KatplanSeiteAdapter())
