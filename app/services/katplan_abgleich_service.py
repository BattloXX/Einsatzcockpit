"""Lesender Abgleich des Arbeitsstands mit einer sichtbaren Planquelle."""

from collections import Counter
from difflib import SequenceMatcher
from itertools import combinations

from sqlalchemy.orm import Session

from app.models.katplan import KatplanDokumentSeite, KatplanElement, KatplanImportVorschlag, KatplanPlan
from app.models.user import User
from app.services.katplan_mcp_service import pruefe_fassung
from app.services.katplan_quelle_service import parse_seiten, sichtbare_quellen
from app.services.katplan_zugriff import sieht_vertraulich


def seiten_menge(seiten: str) -> set[int]:
    return set(parse_seiten(seiten))


def _normalisiert(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def titel_aehnlichkeit(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalisiert(a), _normalisiert(b)).ratio()


def moegliche_dubletten(elemente: list[KatplanElement], schwelle: float = 0.85) -> list[dict]:
    result = []
    for a, b in combinations(elemente, 2):
        if a.typ != b.typ:
            continue
        quote = titel_aehnlichkeit(a.titel, b.titel)
        adresse_a = (_normalisiert(a.strasse), _normalisiert(a.hausnummer))
        adresse_b = (_normalisiert(b.strasse), _normalisiert(b.hausnummer))
        gleiche_adresse = any(adresse_a) and adresse_a == adresse_b
        if quote >= schwelle or gleiche_adresse:
            result.append({
                "a": {k: getattr(a, k) for k in ("kennung", "uuid", "titel")},
                "b": {k: getattr(b, k) for k in ("kennung", "uuid", "titel")},
                "aehnlichkeit": quote,
                "grund": "Titel ähnlich" if quote >= schwelle else "Gleiche Adresse",
            })
            if len(result) == 50:
                break
    return result


def abgleich(db: Session, plan: KatplanPlan, user: User, dokument_id: int) -> dict:
    pruefe_fassung(user, "arbeitsstand")
    dokumente = sichtbare_quellen(db, plan, user)
    dokument = next((d for d in dokumente if d.id == dokument_id), None)
    if dokument is None:
        raise LookupError("Nicht gefunden")
    elemente = db.query(KatplanElement).filter_by(org_id=plan.org_id, plan_id=plan.id)
    if not sieht_vertraulich(user):
        elemente = elemente.filter_by(vertraulich=False)
    sichtbar = elemente.order_by(KatplanElement.sort, KatplanElement.id).all()
    belegt: set[int] = set()
    for e in sichtbar:
        if e.quelle_dokument_id == dokument.id:
            belegt.update(seiten_menge(e.quelle_seiten or ""))
    seiten = {s.seiten_nr: s for s in db.query(KatplanDokumentSeite).filter_by(
        org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument.id)}
    ohne_element = []
    for nr in range(1, dokument.seitenzahl + 1):
        if nr in belegt:
            continue
        seite = seiten.get(nr)
        ohne_element.append({
            "seiten_nr": nr, "titel": seite.titel if seite else None,
            "textauszug": (seite.volltext or "")[:200] if seite else "",
            "thumb_url": f"/katastrophenplaene/quelle/{dokument.uuid}/seite/{nr}/thumb",
        })
    gruppe = dokument.dokument_gruppe_id or dokument.id
    alt = {d.id: d for d in dokumente if (d.dokument_gruppe_id or d.id) == gruppe
           and d.versionsnummer < dokument.versionsnummer}
    veraltet = [{"kennung": e.kennung, "titel": e.titel, "dokument_id": e.quelle_dokument_id,
                 "versionsnummer": alt[e.quelle_dokument_id].versionsnummer}
                for e in sichtbar if e.quelle_dokument_id in alt]
    ohne_quelle = [{"kennung": e.kennung, "element_id": e.uuid, "titel": e.titel, "typ": e.typ}
                  for e in sichtbar if e.quelle_dokument_id is None]
    dubletten = moegliche_dubletten(sichtbar)
    offen = Counter(v.seiten_nr for v in db.query(KatplanImportVorschlag).filter_by(
        org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument.id, status="offen"))
    schritte = [f"Seite {s['seiten_nr']}: katplan_quellseite_verknuepfen oder katplan_element_entwurf_anlegen"
               for s in ohne_element]
    schritte += [f"{e['kennung'] or e['element_id']}: katplan_quellseite_verknuepfen" for e in ohne_quelle]
    schritte += [f"{e['kennung']}: veralteten Quellbezug in der Oberfläche prüfen" for e in veraltet]
    schritte += [f"{d['a']['kennung'] or d['a']['uuid']} und {d['b']['kennung'] or d['b']['uuid']} prüfen: "
                 "mögliche Dublette (Zusammenführen in der Oberfläche)" for d in dubletten]
    schritte += [f"Seite {nr}: {anzahl} offene Vorschläge in der Oberfläche prüfen"
                 for nr, anzahl in sorted(offen.items())]
    return {
        "quelle": {k: getattr(dokument, k) for k in ("id", "titel", "versionsnummer", "seitenzahl")},
        "seiten_ohne_element": ohne_element, "elemente_ohne_quelle": ohne_quelle,
        "veraltete_quellbezuege": veraltet, "dubletten": dubletten,
        "offene_vorschlaege": [{"seiten_nr": nr, "anzahl": anzahl} for nr, anzahl in sorted(offen.items())],
        "naechste_schritte": schritte[:30],
    }
