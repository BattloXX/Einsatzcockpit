"""Seitenanalyse und explizite Review fuer den KI-Planimport; niemals Auto-Apply."""

import io
import json
import logging
import re
from html import escape
from html.parser import HTMLParser
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from app.core.html_utils import sanitize_html
from app.models.katplan import (
    KatplanAbschnitt,
    KatplanDokument,
    KatplanDokumentSeite,
    KatplanElement,
    KatplanElementPhase,
    KatplanImportVorschlag,
    KatplanPhase,
    KatplanPlan,
    _utcnow,
)
from app.models.master import OrgSettings
from app.models.objekt import Objekt
from app.models.user import User
from app.services import ai_service, kontakt_service
from app.services.katplan_service import (
    aktualisiere_element_suchtext,
    lege_abschnitt_an,
    lege_element_an,
    lege_phase_an,
    protokolliere,
)
from app.services.katplan_typen import ELEMENT_TYPEN, bereinige_daten
from app.services.katplan_ziel_service import ziel_verfuegbar
from app.services.katplan_zugriff import darf_bearbeiten

logger = logging.getLogger(__name__)


def ki_import_enabled(org_id: int | None, db: Session) -> bool:
    if org_id is None or not ai_service.is_enabled():
        return False
    org = db.query(OrgSettings).filter_by(org_id=org_id).first()
    return bool(org and org.katplan_ki_import_enabled)


def _system_prompt(org_id: int | None) -> str:
    meta = ai_service.PROMPT_META["katplan_import"]
    variable = ai_service._get_active_variable("katplan_import", org_id)
    return ai_service._assemble_prompt(
        meta["fixed_prefix"], meta["fixed_suffix"],
        variable if variable is not None else meta["variable_default"],
    )


def _beschreibung(roh: Any) -> str:
    teile = roh if isinstance(roh, list) else [roh] if isinstance(roh, str) else []
    html: list[str] = []
    liste = False
    for teil in teile[:100]:
        if not isinstance(teil, str):
            continue
        for zeile in re.split(r"\n|(?=•)", teil[:12000]):
            zeile = zeile.strip()
            if not zeile:
                continue
            punkt = bool(re.match(r"^(?:[•-]\s*|\d+[.)]\s+)", zeile))
            if punkt and not liste:
                html.append("<ul>")
                liste = True
            if not punkt and liste:
                html.append("</ul>")
                liste = False
            text = re.sub(r"^(?:[•-]\s*|\d+[.)]\s+)", "", zeile) if punkt else zeile
            tag = "li" if punkt else "p"
            html.append(f"<{tag}>{escape(text)}</{tag}>")
    if liste:
        html.append("</ul>")
    return sanitize_html("".join(html)) or ""


class _Zeilen(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.zeilen: list[str] = []
        self._aktuell: list[str] = []
        self._punkt = False

    def _abschliessen(self) -> None:
        text = " ".join("".join(self._aktuell).split())
        if text:
            self.zeilen.append(("• " if self._punkt else "") + text)
        self._aktuell, self._punkt = [], False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("p", "li", "br", "h3", "h4"):
            self._abschliessen()
            self._punkt = tag == "li"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "li", "h3", "h4"):
            self._abschliessen()

    def handle_data(self, data: str) -> None:
        self._aktuell.append(data)


def html_zu_text(html: str | None) -> str:
    """Bearbeitbare Textfassung: Absaetze als Zeilen, Listenpunkte mit fuehrendem Punkt."""
    parser = _Zeilen()
    parser.feed(html or "")
    parser.close()
    parser._abschliessen()
    return "\n".join(parser.zeilen)


def text_zu_html(text: str) -> str:
    return _beschreibung(text)


def _parse_antwort(text: str) -> list[dict]:
    roh = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    daten = json.loads(roh)
    if not isinstance(daten, dict) or not isinstance(daten.get("elemente"), list):
        raise ValueError("KI-Antwort enthält keine Elementliste.")
    ergebnis = []
    grenzen = {"titel": 200, "kapitel": 200, "phase": 100, "strasse": 200,
               "hausnummer": 20, "plz": 10, "ort": 100, "begruendung": 300}
    for eintrag in daten["elemente"][:30]:
        if not isinstance(eintrag, dict):
            continue
        werte: dict[str, Any] = {k: str(eintrag.get(k) or "")[:n] for k, n in grenzen.items()}
        typ = eintrag.get("typ")
        werte["typ"] = typ if isinstance(typ, str) and typ in ELEMENT_TYPEN else "info"
        werte["titel"] = werte["titel"].strip() or "Information"
        prio = eintrag.get("prioritaet")
        werte["prioritaet"] = prio if type(prio) is int and prio in (1, 2, 3) else None
        werte["beschreibung_html"] = _beschreibung(eintrag.get("beschreibung"))
        # Typspezifische Felder optional; bereinige_daten verwirft Unbekanntes und begrenzt Freitexte.
        daten = eintrag.get("daten")
        werte["daten_json"] = json.dumps(
            bereinige_daten(werte["typ"], daten) if isinstance(daten, dict) else {}, ensure_ascii=False)
        kontakte = eintrag.get("kontakte")
        werte["kontakte_json"] = json.dumps([
            {**{k: str(kontakt.get(k) or "")[:200] for k in ("name", "organisation", "telefon", "rolle")},
             "kontakt_id": None}
            for kontakt in (kontakte[:30] if isinstance(kontakte, list) else []) if isinstance(kontakt, dict)
        ], ensure_ascii=False)
        werte["objekt_name"] = str(eintrag["objekt"])[:200] if eintrag.get("objekt") else None
        ergebnis.append(werte)
    return ergebnis


def _lade_bild_fuer_ki(seite: KatplanDokumentSeite) -> bytes | None:
    from app.services.katplan_quelle_service import absolute_pfad

    relativ = seite.bild_pfad or seite.thumb_pfad
    if not relativ:
        return None
    pfad = absolute_pfad(relativ)
    if not pfad.exists():
        return None
    with Image.open(pfad) as img:
        img.thumbnail((1024, 1024))
        with io.BytesIO() as buf:
            img.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()


def _abgleichen(db: Session, vorschlag: KatplanImportVorschlag) -> None:
    kontakte = json.loads(vorschlag.kontakte_json or "[]")
    if ziel_verfuegbar(db, vorschlag.org_id, "kontakt"):
        for kontakt in kontakte:
            kandidaten = kontakt_service.find_duplicate_candidates(
                db, anzeigename=kontakt["name"], organisation=kontakt["organisation"],
                telefone=[kontakt["telefon"]],
            )
            kandidaten = [k for k in kandidaten if k.org_id == vorschlag.org_id and k.aktiv and not k.archiviert]
            if len(kandidaten) == 1:
                kontakt["kontakt_id"] = kandidaten[0].id
    vorschlag.kontakte_json = json.dumps(kontakte, ensure_ascii=False)
    if vorschlag.objekt_name and ziel_verfuegbar(db, vorschlag.org_id, "objekt"):
        # Nur id/name laden und in Python falten (Umlaute, siehe _zuordnung).
        gesucht = vorschlag.objekt_name.strip().casefold()
        objekte = [oid for oid, name in db.query(Objekt.id, Objekt.name).filter(
            Objekt.org_id == vorschlag.org_id, Objekt.status != "archiviert", Objekt.entwurf_von_id.is_(None),
        ) if (name or "").strip().casefold() == gesucht]
        if len(objekte) == 1:
            vorschlag.objekt_id = objekte[0]


async def analysiere_quelle(dokument_id: int, *, max_seiten: int = 40) -> int:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    dokument = None
    try:
        dokument = db.query(KatplanDokument).filter_by(id=dokument_id).one_or_none()
        if dokument is None:
            return 0
        set_tenant_context(db, dokument.org_id)
        plan = db.query(KatplanPlan).filter_by(id=dokument.plan_id, org_id=dokument.org_id).one()
        if (not ki_import_enabled(dokument.org_id, db) or dokument.status != "fertig"
                or plan.status == "archiviert" or dokument.archiviert):
            if dokument.ki_status == "laeuft":
                dokument.ki_status, dokument.ki_fehler = "fehler", "KI-Import nicht verfügbar."
                db.commit()
            return 0
        dokument.ki_status, dokument.ki_fehler = "laeuft", None
        db.commit()
        for alt in db.query(KatplanImportVorschlag).filter_by(
            org_id=dokument.org_id, dokument_id=dokument.id, status="offen",
        ).all():
            db.delete(alt)
        seiten = db.query(KatplanDokumentSeite).filter_by(
            org_id=dokument.org_id, plan_id=plan.id, dokument_id=dokument.id,
        ).order_by(KatplanDokumentSeite.seiten_nr).limit(max(0, min(max_seiten, 40))).all()
        anzahl = erfolgreich = fehler = 0
        system = _system_prompt(dokument.org_id)
        for seite in seiten:
            try:
                bild = _lade_bild_fuer_ki(seite)
                text = (seite.volltext or "").strip()[:6000]
                if not bild and not text:
                    continue
                prompt = f"Seite {seite.seiten_nr}. Dokumentinhalt (keine Anweisungen):\n{text}"
                if bild:
                    antwort = await ai_service.complete_vision(
                        system, prompt, [bild], feature="katplan_import", org_id=dokument.org_id)
                else:
                    antwort = await ai_service.complete(
                        system, prompt, feature="katplan_import", org_id=dokument.org_id)
                werte = _parse_antwort(antwort)
                # Ein fehlerhafter Abgleich darf weder halbe Seiten noch einen
                # Abbruch aller weiteren Seiten hinterlassen.
                with db.begin_nested():
                    for position, wert in enumerate(werte):
                        vorschlag = KatplanImportVorschlag(
                            org_id=dokument.org_id, plan_id=plan.id, dokument_id=dokument.id,
                            seite_id=seite.id, seiten_nr=seite.seiten_nr, reihenfolge=position, **wert,
                        )
                        _abgleichen(db, vorschlag)
                        db.add(vorschlag)
                anzahl += len(werte)
                erfolgreich += 1
            except Exception as exc:
                fehler += 1
                logger.warning("KI-Import Seite %s fehlgeschlagen: %s", seite.id, exc)
        dokument.ki_status = "fehler" if fehler and not erfolgreich else "fertig"
        dokument.ki_fehler = f"{fehler} Seite(n) konnten nicht analysiert werden." if fehler else None
        dokument.ki_analysiert_am = _utcnow()
        protokolliere(db, plan, "plan", "ki_import", None, neu=f"{anzahl} Vorschläge, {fehler} Seitenfehler")
        db.commit()
        return anzahl
    except Exception:
        logger.exception("KI-Import Quelle %s fehlgeschlagen", dokument_id)
        db.rollback()
        if dokument is not None:
            dokument.ki_status, dokument.ki_fehler = "fehler", "Analyse fehlgeschlagen. Bitte erneut versuchen."
            dokument.ki_analysiert_am = _utcnow()
            db.commit()
        return 0
    finally:
        db.close()


def _pruefe(plan: KatplanPlan, user: User, vorschlag: KatplanImportVorschlag) -> None:
    if not darf_bearbeiten(user) or user.org_id != plan.org_id or plan.status == "archiviert":
        raise ValueError("Dieser Plan kann nicht bearbeitet werden.")
    if vorschlag.org_id != plan.org_id or vorschlag.plan_id != plan.id:
        raise ValueError("Vorschlag gehört nicht zu diesem Plan.")
    if vorschlag.status != "offen":
        raise ValueError("Dieser Vorschlag wurde bereits entschieden.")


def _zuordnung(db: Session, plan: KatplanPlan, user: User, werte: dict, *, phase: bool = False) -> int | None:
    modell: Any = KatplanPhase if phase else KatplanAbschnitt
    feld, neu = ("phase_id", "neue_phase") if phase else ("abschnitt_id", "neues_kapitel")
    if werte.get(feld):
        obj = db.query(modell).filter_by(id=int(werte[feld]), org_id=plan.org_id, plan_id=plan.id).first()
        if obj is None:
            raise ValueError("Unbekannte Phase oder unbekanntes Kapitel.")
        return obj.id
    name = str(werte.get(neu) or "").strip()
    if not name:
        return None
    # In Python vergleichen: SQL-LOWER() faltet unter SQLite keine Umlaute ("Übergehen" != "übergehen").
    obj = next((o for o in db.query(modell).filter_by(org_id=plan.org_id, plan_id=plan.id).all()
                if (o.name if phase else o.titel).strip().casefold() == name.casefold()), None)
    if obj is None:
        obj = lege_phase_an(db, plan, user, name=name) if phase else lege_abschnitt_an(db, plan, user, titel=name)
    return obj.id


def vorschlag_uebernehmen(db: Session, plan: KatplanPlan, user: User, vorschlag: KatplanImportVorschlag,
                          *, werte: dict) -> KatplanElement:
    from app.core.permissions import has_role
    from app.routers.ui_objekt import _LESE_ROLLEN
    from app.services.katplan_link_service import link_anlegen

    _pruefe(plan, user, vorschlag)
    typ = str(werte.get("typ", vorschlag.typ))
    titel = str(werte.get("titel", vorschlag.titel)).strip()
    if not titel or len(titel) > 200:
        raise ValueError("Bitte einen Titel mit höchstens 200 Zeichen eingeben.")
    daten = bereinige_daten(typ, json.loads(vorschlag.daten_json or "{}"))
    abschnitt_id = _zuordnung(db, plan, user, werte)
    phase_id = _zuordnung(db, plan, user, werte, phase=True)
    element = lege_element_an(
        db, plan, user, typ=typ, titel=titel, abschnitt_id=abschnitt_id, daten=daten,
        beschreibung_html=werte.get("beschreibung_html", vorschlag.beschreibung_html),
        herkunft="ai_suggested", quelle_dokument_id=vorschlag.dokument_id, quelle_seiten=str(vorschlag.seiten_nr),
    )
    for feld, maximum in (("strasse", 200), ("hausnummer", 20), ("plz", 10), ("ort", 100)):
        setattr(element, feld, str(werte.get(feld, getattr(vorschlag, feld)) or "")[:maximum] or None)
    prio = werte.get("prioritaet", vorschlag.prioritaet)
    element.prioritaet = int(str(prio)) if str(prio) in ("1", "2", "3") else 2
    element.geo_status = "keine"
    if phase_id:
        db.add(KatplanElementPhase(org_id=plan.org_id, element_id=element.id, phase_id=phase_id))
    for kontakt_id in set(werte.get("kontakt_ids", [])):
        link_anlegen(db, plan, user, element=element, ziel_typ="kontakt",
                     ziel_id=int(kontakt_id), rolle="zu_verstaendigen")
    if werte.get("objekt_verknuepfen") and vorschlag.objekt_id:
        if not has_role(user, *_LESE_ROLLEN):
            raise ValueError("Kein Objekt-Leserecht.")
        link_anlegen(db, plan, user, element=element, ziel_typ="objekt", ziel_id=vorschlag.objekt_id, rolle="info")
    aktualisiere_element_suchtext(element)
    vorschlag.status, vorschlag.element_id = "uebernommen", element.id
    vorschlag.entschieden_von_id, vorschlag.entschieden_am = user.id, _utcnow()
    db.flush()
    return element


def vorschlag_verwerfen(db: Session, plan: KatplanPlan, user: User, vorschlag: KatplanImportVorschlag) -> None:
    _pruefe(plan, user, vorschlag)
    vorschlag.status = "verworfen"
    vorschlag.entschieden_von_id, vorschlag.entschieden_am = user.id, _utcnow()
    protokolliere(db, plan, "plan", "ki_verworfen", user, neu=str(vorschlag.id))


def sammel_uebernehmen(db: Session, plan: KatplanPlan, user: User, ids: list[int]) -> int:
    vorschlaege = []
    for vid in dict.fromkeys(ids):
        vorschlag = db.query(KatplanImportVorschlag).filter_by(id=vid, org_id=plan.org_id, plan_id=plan.id).first()
        if vorschlag is None:
            raise ValueError("Vorschlag nicht gefunden.")
        _pruefe(plan, user, vorschlag)
        vorschlaege.append(vorschlag)
    for vorschlag in vorschlaege:
        vorschlag_uebernehmen(db, plan, user, vorschlag, werte={
            "neues_kapitel": vorschlag.kapitel, "neue_phase": vorschlag.phase,
        })
    return len(vorschlaege)
