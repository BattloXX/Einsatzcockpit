"""KI-Dokumentklassifizierung (Objektverwaltung PR8).

Vision-Analyse gerenderter PDF-Seiten → Vorschlaege (Dokumentart, Titel,
Melderlinien, Stand) in eine Review-Queue. Vorschlaege werden NIE automatisch
uebernommen — der Sachbearbeiter bestaetigt/korrigiert/verwirft (EUS-Lehre).

Opt-in: OrgSettings.objekt_ki_klassifikation_enabled UND ai_service.is_enabled().
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.objekt import (
    GEFAHR_PIKTOGRAMME,
    KI_VORSCHLAG_OFFEN,
    Objekt,
    ObjektDokumentSeite,
    ObjektSeiteKiVorschlag,
    ObjektStammdatenVorschlag,
)
from app.services.loop_thread_io import im_db_thread

logger = logging.getLogger("einsatzleiter.objekt_ki")


def _system_prompt(erlaubte_codes: list[str]) -> str:
    """Baut den System-Prompt mit den org-spezifischen Dokumentart-Codes."""
    return (
        "Du bist ein Dokumenten-Klassifizierer der Feuerwehr. Du siehst eine Seite "
        "aus den Einsatzunterlagen eines Objekts (Betrieb/Wohnanlage). "
        "Klassifiziere die Seite und antworte AUSSCHLIESSLICH mit einem JSON-Objekt "
        "ohne Markdown: {\"dokumentart\": <code>, \"titel\": <string|null>, "
        "\"melderlinien\": <string|null>, \"stand\": <YYYY-MM-DD|null>, "
        "\"begruendung\": <string>}. "
        "Erlaubte dokumentart-Codes: " + ", ".join(erlaubte_codes) + ". "
        "melderlinien: nur wenn eindeutig BMA-Melderlinien-Nummern erkennbar sind "
        "(kommagetrennt, z. B. '12, 13'). titel: kurzer sprechender Titel "
        "(z. B. 'Melderplan EG Nord'). stand: Datum auf der Seite, falls vorhanden. "
        "Wenn unsicher: dokumentart null lassen und das in der begruendung sagen."
    )


def ki_klassifikation_enabled(org_id: int | None, db: Session) -> bool:
    """Opt-in-Gate: Org-Flag UND KI-Dienst aktiv."""
    if org_id is None:
        return False
    from app.models.master import OrgSettings
    from app.services.ai_service import is_enabled
    if not is_enabled():
        return False
    org_s = (
        db.query(OrgSettings)
        .filter(OrgSettings.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    return bool(org_s and org_s.objekt_ki_klassifikation_enabled)


def _parse_antwort(text: str, erlaubte_codes: set[str]) -> dict | None:
    """Parst die JSON-Antwort; None bei ungueltigem JSON. Unbekannte Codes → None."""
    try:
        roh = text.strip()
        if roh.startswith("```"):
            roh = roh.strip("`")
            if roh.startswith("json"):
                roh = roh[4:]
        daten = json.loads(roh)
    except (ValueError, TypeError):
        return None
    if not isinstance(daten, dict):
        return None
    dokumentart = daten.get("dokumentart")
    if dokumentart is not None and dokumentart not in erlaubte_codes:
        dokumentart = None
    stand = None
    if daten.get("stand"):
        try:
            stand = datetime.strptime(str(daten["stand"]), "%Y-%m-%d").date()
        except ValueError:
            stand = None
    return {
        "dokumentart": dokumentart,
        "titel": (str(daten.get("titel"))[:200] if daten.get("titel") else None),
        "melderlinien": (str(daten.get("melderlinien"))[:100] if daten.get("melderlinien") else None),
        "stand": stand,
        "begruendung": (str(daten.get("begruendung"))[:300] if daten.get("begruendung") else None),
    }


def _lade_bild_fuer_ki(seite: ObjektDokumentSeite) -> bytes | None:
    """Laedt das Seiten-Rendering und verkleinert es auf max. ~1024px
    (Tokenkosten). None, wenn (noch) kein Rendering existiert."""
    from app.services.objekt_dokument_service import absolute_pfad

    # bewusst Original (keine Einzeichnung)
    bild_pfad = seite.bild_pfad or seite.thumb_pfad
    if not bild_pfad:
        return None
    pfad = absolute_pfad(bild_pfad)
    if not pfad.exists():
        return None

    bild = pfad.read_bytes()
    try:
        import io as _io

        from PIL import Image
        img = Image.open(_io.BytesIO(bild))
        if max(img.size) > 1024:
            img.thumbnail((1024, 1024))
            buf = _io.BytesIO()
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")  # type: ignore[assignment]
            img.save(buf, format="PNG")
            bild = buf.getvalue()
    except Exception:
        pass
    return bild


async def analysiere_seite(seite: ObjektDokumentSeite, db: Session) -> ObjektSeiteKiVorschlag | None:
    """Erzeugt einen KI-Vorschlag fuer eine Seite (nutzt das Thumbnail-/Hi-Res-Bild).

    Gibt None zurueck bei fehlendem Rendering, KI-Fehler oder ungueltiger
    Antwort. Vorhandene offene Vorschlaege der Seite werden ersetzt.
    Caller committet.
    """
    from app.services.ai_service import AIServiceError, complete_vision

    bild = _lade_bild_fuer_ki(seite)
    if bild is None:
        return None

    from app.services.objekt_service import lade_auswahl
    dokumentarten = lade_auswahl(db, seite.org_id, "dokumentart")

    # Neben dem Bild auch den je Seite extrahierten Volltext (PDF-Textlayer/OCR)
    # mitgeben — verbessert die Klassifikation deutlich (Titel, Melderlinien, Stand).
    user_prompt = "Klassifiziere diese Dokumentseite."
    seiten_text = (seite.volltext or "").strip()
    if seiten_text:
        user_prompt += (
            "\n\nExtrahierter Text dieser Seite (PDF-Textlayer/OCR, kann Fehler enthalten):\n"
            + seiten_text[:4000]
        )

    try:
        antwort = await complete_vision(
            _system_prompt(list(dokumentarten)),
            user_prompt,
            [bild],
            feature="objekt_dokumentklassifizierung",
            org_id=seite.org_id,
        )
    except AIServiceError as exc:
        logger.warning("KI-Klassifizierung fehlgeschlagen (Seite %d): %s", seite.id, exc)
        return None

    geparst = _parse_antwort(antwort, set(dokumentarten))
    if geparst is None:
        logger.warning("KI-Antwort unparsebar (Seite %d): %.200s", seite.id, antwort)
        return None

    # Offene Alt-Vorschlaege der Seite ersetzen
    db.query(ObjektSeiteKiVorschlag).filter(
        ObjektSeiteKiVorschlag.seite_id == seite.id,
        ObjektSeiteKiVorschlag.status == KI_VORSCHLAG_OFFEN,
    ).delete()

    vorschlag = ObjektSeiteKiVorschlag(
        org_id=seite.org_id,
        seite_id=seite.id,
        status=KI_VORSCHLAG_OFFEN,
        **geparst,
    )
    db.add(vorschlag)

    # Zusaetzlicher Extraktionsdurchlauf: Seiten vom Typ "objektinformation"
    # (z. B. die Objektbeschreibungs-Tabelle eines Brandschutzplans) enthalten
    # oft strukturierte Stammdaten (BMZ/FBF-Standort, Gefahren, Merkmale) - dafuer
    # separat einen Vorschlag erzeugen, statt das in die Klassifikation zu mischen.
    if geparst.get("dokumentart") == "objektinformation":
        await analysiere_objektbeschreibung(seite, db, bild=bild, seiten_text=seiten_text)

    return vorschlag


def _system_prompt_stammdaten(merkmal_codes: list[str]) -> str:
    piktogramm_typen = ", ".join(GEFAHR_PIKTOGRAMME.keys())
    return (
        "Du bist ein Sachbearbeiter der Feuerwehr-Objektverwaltung. Du siehst die "
        "Objektbeschreibungs-Seite eines Brandschutzplans oder einer Objektinformation "
        "(meist eine Tabelle mit Ankreuzfeldern). Extrahiere NUR Angaben, die auf der "
        "Seite eindeutig erkennbar sind - erfinde nichts, lass unklare Felder null/leer. "
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt ohne Markdown:\n"
        "{\"informationen_text\": <string|null>, \"bma_nummer\": <string|null>, "
        "\"bmz_standort\": <string|null>, \"fbf_standort\": <string|null>, "
        "\"laufkarten_ablageort\": <string|null>, \"schluesselsafe_standort\": <string|null>, "
        "\"gefahren\": [{\"piktogramm_typ\": <code>, \"name\": <string|null>, \"detail\": <string>}], "
        "\"merkmale\": [{\"code\": <code|null>, \"name\": <string|null>}], \"begruendung\": <string>}.\n"
        "informationen_text: kompakte Stichpunkte zu Gebäudeklasse, Anzahl UG/OG, "
        "höchstem Fluchtniveau, Anzahl Stiegen/Sicherheitsstiegen, Sprinkler- und "
        "Gaslöschanlagen-Standort - alles, was sonst nirgends erfasst wird. "
        "bmz_standort/fbf_standort/laufkarten_ablageort/schluesselsafe_standort: "
        "jeweils der auf der Seite genannte Standort/Ablageort, sonst null. "
        "gefahren: nur bei eindeutigem Beleg (z. B. angekreuzte 'Besondere Gefahren' "
        "oder konkret genannte Gefahrstoffe/Mengen); piktogramm_typ IMMER NUR aus: "
        f"{piktogramm_typen} (waehle den am besten passenden Typ, auch wenn die Org "
        "dafuer noch keinen Katalogeintrag hat - das ist ausdruecklich erwuenscht). "
        "name: nur ausfuellen, wenn du eine spezifischere Katalog-Bezeichnung als den "
        "Standardnamen vorschlagen willst (sonst null). "
        "merkmale: code NUR aus dieser Liste, wenn eindeutig belegt: " + ", ".join(merkmal_codes)
        + ". Ist ein eindeutig erkennbares Merkmal keinem dieser Codes zuordenbar "
        "(z. B. eine objektspezifische Besonderheit), code=null setzen und stattdessen "
        "einen kurzen, sprechenden name vergeben (neues, bisher unbekanntes Merkmal - "
        "ausdruecklich erwuenscht, nicht auslassen)."
    )


def _parse_stammdaten_antwort(text: str) -> dict | None:
    try:
        roh = text.strip()
        if roh.startswith("```"):
            roh = roh.strip("`")
            if roh.startswith("json"):
                roh = roh[4:]
        daten = json.loads(roh)
    except (ValueError, TypeError):
        return None
    if not isinstance(daten, dict):
        return None

    gefahren = [
        {
            "piktogramm_typ": g["piktogramm_typ"],
            "name": (str(g["name"])[:100] if g.get("name") else None),
            "detail": str(g.get("detail") or "")[:300],
        }
        for g in (daten.get("gefahren") or [])
        if isinstance(g, dict) and g.get("piktogramm_typ") in GEFAHR_PIKTOGRAMME
    ]
    merkmale = [
        {"code": (m.get("code") or None), "name": (str(m["name"])[:120] if m.get("name") else None)}
        for m in (daten.get("merkmale") or [])
        if isinstance(m, dict) and (m.get("code") or m.get("name"))
    ]

    def _str(key: str, max_len: int) -> str | None:
        val = daten.get(key)
        return str(val)[:max_len] if val else None

    return {
        "informationen_text": _str("informationen_text", 2000),
        "bma_nummer": _str("bma_nummer", 50),
        "bmz_standort": _str("bmz_standort", 300),
        "fbf_standort": _str("fbf_standort", 300),
        "laufkarten_ablageort": _str("laufkarten_ablageort", 300),
        "schluesselsafe_standort": _str("schluesselsafe_standort", 300),
        "gefahren_json": json.dumps(gefahren) if gefahren else None,
        "merkmale_json": json.dumps(merkmale) if merkmale else None,
        "begruendung": _str("begruendung", 300),
    }


def _bereits_vorhanden(objekt) -> dict:
    """Aktueller Objekt-Stand für den Dubletten-Filter (nur bereits befuellte
    Felder/Kategorien - eine KI-Extraktion soll nichts vorschlagen, was schon
    erfasst ist)."""
    bma = objekt.bma
    return {
        "bma_werte": {
            feld: getattr(bma, feld)
            for feld in ("bma_nummer", "bmz_standort", "fbf_standort",
                         "laufkarten_ablageort", "schluesselsafe_standort")
        } if bma else {},
        "gefahr_typen": {g.gefahr.piktogramm_typ for g in objekt.gefahren if g.gefahr},
        "merkmal_codes": {m.merkmal.code for m in objekt.merkmale if m.merkmal and m.merkmal.code},
        "informationen": objekt.informationen or "",
    }


def _ohne_bereits_vorhandenes(geparst: dict, bereits: dict) -> dict:
    """Entfernt aus dem Extraktionsergebnis alles, was am Objekt schon erfasst
    ist - verhindert Dubletten UND unnoetigen Review-Lärm (dieselbe Info wird
    nicht bei jeder neu analysierten Seite erneut vorgeschlagen)."""
    ergebnis = dict(geparst)
    for feld, bestehender_wert in bereits["bma_werte"].items():
        if bestehender_wert and ergebnis.get(feld):
            ergebnis[feld] = None
    if ergebnis.get("informationen_text") and ergebnis["informationen_text"] in bereits["informationen"]:
        ergebnis["informationen_text"] = None

    gefahren = json.loads(ergebnis["gefahren_json"]) if ergebnis.get("gefahren_json") else []
    gefahren = [g for g in gefahren if g["piktogramm_typ"] not in bereits["gefahr_typen"]]
    ergebnis["gefahren_json"] = json.dumps(gefahren) if gefahren else None

    merkmale = json.loads(ergebnis["merkmale_json"]) if ergebnis.get("merkmale_json") else []
    merkmale = [m for m in merkmale if not (m.get("code") and m["code"] in bereits["merkmal_codes"])]
    ergebnis["merkmale_json"] = json.dumps(merkmale) if merkmale else None
    return ergebnis


async def analysiere_objektbeschreibung(
    seite: ObjektDokumentSeite, db: Session, *, bild: bytes, seiten_text: str,
) -> ObjektStammdatenVorschlag | None:
    """Erzeugt einen strukturierten Stammdaten-Vorschlag aus einer Objektbeschreibungs-
    Seite (Review-Queue, nie Auto-Apply). Nutzt Bild/Volltext, die analysiere_seite()
    bereits vorbereitet hat. Gibt None zurück bei KI-Fehler, ungültiger Antwort oder
    wenn alles Extrahierte am Objekt schon erfasst ist. Caller (analysiere_seite)
    committet."""
    from app.services.ai_service import AIServiceError, complete_vision
    from app.services.objekt_service import STANDARD_MERKMALE

    objekt = db.get(Objekt, seite.objekt_id)
    if objekt is None:
        return None

    merkmal_codes = [code for code, _name, _icon in STANDARD_MERKMALE]
    user_prompt = "Extrahiere die Stammdaten dieser Objektbeschreibungs-Seite."
    if seiten_text:
        user_prompt += (
            "\n\nExtrahierter Text dieser Seite (PDF-Textlayer/OCR, kann Fehler enthalten):\n"
            + seiten_text[:4000]
        )

    try:
        antwort = await complete_vision(
            _system_prompt_stammdaten(merkmal_codes), user_prompt, [bild],
            feature="objekt_stammdaten_extraktion", org_id=seite.org_id,
        )
    except AIServiceError as exc:
        logger.warning("KI-Stammdaten-Extraktion fehlgeschlagen (Seite %d): %s", seite.id, exc)
        return None

    geparst = _parse_stammdaten_antwort(antwort)
    if geparst is None:
        logger.warning("KI-Stammdaten-Antwort unparsebar (Seite %d): %.200s", seite.id, antwort)
        return None

    geparst = _ohne_bereits_vorhandenes(geparst, _bereits_vorhanden(objekt))
    # Nichts Verwertbares (oder nur bereits Bekanntes) erkannt -> keinen Vorschlag anlegen
    if not any(v for k, v in geparst.items() if k != "begruendung"):
        return None

    db.query(ObjektStammdatenVorschlag).filter(
        ObjektStammdatenVorschlag.seite_id == seite.id,
        ObjektStammdatenVorschlag.status == KI_VORSCHLAG_OFFEN,
    ).delete()

    vorschlag = ObjektStammdatenVorschlag(
        org_id=seite.org_id,
        objekt_id=seite.objekt_id,
        seite_id=seite.id,
        status=KI_VORSCHLAG_OFFEN,
        **geparst,
    )
    db.add(vorschlag)
    return vorschlag


async def analysiere_objektbeschreibung_fuer_seite(
    seite: ObjektDokumentSeite, db: Session,
) -> ObjektStammdatenVorschlag | None:
    """Wie analysiere_objektbeschreibung(), laedt Bild/Volltext aber selbst
    (Aufruf unabhaengig von analysiere_seite()). Fuer bereits als
    "objektinformation" klassifizierte Seiten, die noch keinen
    Stammdaten-Vorschlag haben - z. B. weil sie schon vor Einfuehrung dieser
    Erweiterung klassifiziert wurden (die Dokumentart-Klassifikation selbst
    laeuft nicht erneut, dafuer bleibt die bestehende Seiten-Klassifikation
    unangetastet)."""
    bild = _lade_bild_fuer_ki(seite)
    if bild is None:
        return None
    seiten_text = (seite.volltext or "").strip()
    return await analysiere_objektbeschreibung(seite, db, bild=bild, seiten_text=seiten_text)


async def analysiere_unklassifizierte_seiten(objekt_id: int, *, limit: int = 20) -> int:
    """Analysiert Klassifikation und nachzuholende Stammdaten ohne Session am API-Aufruf."""
    erzeugt = 0
    ergebnisse = []
    try:
        seiten = await im_db_thread(_objekt_analyse_laden_sync, objekt_id, limit)
        for seite_id, nachholen in seiten:
            daten = await im_db_thread(_objekt_seite_laden_sync, seite_id, nachholen)
            if daten is None:
                continue
            klassifikation = stammdaten = None
            if not daten["nachholen"]:
                klassifikation = await _klassifikation_aus_nutzdaten(daten)
            if daten["nachholen"] or (klassifikation and klassifikation.get("dokumentart") == "objektinformation"):
                stammdaten = await _stammdaten_aus_nutzdaten(daten)
            if klassifikation is not None or stammdaten is not None:
                ids = {key: daten[key] for key in ("seite_id", "org_id", "objekt_id")}
                ergebnisse.append((ids, klassifikation, stammdaten))
                erzeugt += 1
        if erzeugt:
            await im_db_thread(_objekt_analyse_speichern_sync, ergebnisse)
        return erzeugt
    except Exception:
        logger.exception("KI-Analyse fuer Objekt %d fehlgeschlagen", objekt_id)
        return erzeugt


def _objekt_analyse_laden_sync(objekt_id: int, limit: int) -> list[tuple[int, bool]]:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        seiten = (
            db.query(ObjektDokumentSeite)
            .filter(
                ObjektDokumentSeite.objekt_id == objekt_id,
                ObjektDokumentSeite.dokumentart.is_(None),
            )
            .order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)
            .limit(limit)
            .all()
        )
        nachzuholen = (
            db.query(ObjektDokumentSeite)
            .filter(
                ObjektDokumentSeite.objekt_id == objekt_id,
                ObjektDokumentSeite.dokumentart == "objektinformation",
                ~ObjektDokumentSeite.id.in_(
                    db.query(ObjektStammdatenVorschlag.seite_id)
                    .filter(ObjektStammdatenVorschlag.objekt_id == objekt_id)
                ),
            )
            .order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)
            .limit(limit)
            .all()
        )
        if not seiten and not nachzuholen:
            return []
        org_id = (seiten[0] if seiten else nachzuholen[0]).org_id
        if not ki_klassifikation_enabled(org_id, db):
            return []
        # Seiten mit bereits offenem Klassifikations-Vorschlag ueberspringen
        offene = {
            v.seite_id
            for v in db.query(ObjektSeiteKiVorschlag)
            .filter(
                ObjektSeiteKiVorschlag.seite_id.in_([s.id for s in seiten]),
                ObjektSeiteKiVorschlag.status == KI_VORSCHLAG_OFFEN,
            )
            .all()
        }
        return ([(seite.id, False) for seite in seiten if seite.id not in offene]
                + [(seite.id, True) for seite in nachzuholen])
    finally:
        db.close()


def _objekt_seite_laden_sync(seite_id: int, nachholen: bool) -> dict | None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services.objekt_service import lade_auswahl

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        seite = db.get(ObjektDokumentSeite, seite_id)
        if seite is None:
            return None
        bild = _lade_bild_fuer_ki(seite)
        if bild is None:
            return None
        objekt = db.get(Objekt, seite.objekt_id)
        return {
            "seite_id": seite.id, "org_id": seite.org_id, "objekt_id": seite.objekt_id,
            "bild": bild, "text": (seite.volltext or "").strip(), "nachholen": nachholen,
            "dokumentarten": list(lade_auswahl(db, seite.org_id, "dokumentart")) if not nachholen else [],
            "bereits": _bereits_vorhanden(objekt) if objekt is not None else None,
        }
    finally:
        db.close()


async def _klassifikation_aus_nutzdaten(daten: dict) -> dict | None:
    from app.services.ai_service import AIServiceError, complete_vision

    prompt = "Klassifiziere diese Dokumentseite."
    if daten["text"]:
        prompt += ("\n\nExtrahierter Text dieser Seite (PDF-Textlayer/OCR, kann Fehler enthalten):\n"
                   + daten["text"][:4000])
    try:
        antwort = await complete_vision(
            _system_prompt(daten["dokumentarten"]), prompt, [daten["bild"]],
            feature="objekt_dokumentklassifizierung", org_id=daten["org_id"],
        )
    except AIServiceError as exc:
        logger.warning("KI-Klassifizierung fehlgeschlagen (Seite %d): %s", daten["seite_id"], exc)
        return None
    geparst = _parse_antwort(antwort, set(daten["dokumentarten"]))
    if geparst is None:
        logger.warning("KI-Antwort unparsebar (Seite %d): %.200s", daten["seite_id"], antwort)
    return geparst


async def _stammdaten_aus_nutzdaten(daten: dict) -> dict | None:
    from app.services.ai_service import AIServiceError, complete_vision
    from app.services.objekt_service import STANDARD_MERKMALE

    if daten["bereits"] is None:
        return None
    prompt = "Extrahiere die Stammdaten dieser Objektbeschreibungs-Seite."
    if daten["text"]:
        prompt += ("\n\nExtrahierter Text dieser Seite (PDF-Textlayer/OCR, kann Fehler enthalten):\n"
                   + daten["text"][:4000])
    try:
        antwort = await complete_vision(
            _system_prompt_stammdaten([code for code, _name, _icon in STANDARD_MERKMALE]),
            prompt, [daten["bild"]], feature="objekt_stammdaten_extraktion", org_id=daten["org_id"],
        )
    except AIServiceError as exc:
        logger.warning("KI-Stammdaten-Extraktion fehlgeschlagen (Seite %d): %s", daten["seite_id"], exc)
        return None
    geparst = _parse_stammdaten_antwort(antwort)
    if geparst is None:
        logger.warning("KI-Stammdaten-Antwort unparsebar (Seite %d): %.200s", daten["seite_id"], antwort)
        return None
    geparst = _ohne_bereits_vorhandenes(geparst, daten["bereits"])
    return geparst if any(v for k, v in geparst.items() if k != "begruendung") else None


def _objekt_analyse_speichern_sync(ergebnisse: list) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        for daten, klassifikation, stammdaten in ergebnisse:
            if klassifikation is not None:
                alte = db.query(ObjektSeiteKiVorschlag).filter(
                    ObjektSeiteKiVorschlag.seite_id == daten["seite_id"],
                    ObjektSeiteKiVorschlag.status == KI_VORSCHLAG_OFFEN,
                ).all()
                for alt in alte:
                    db.delete(alt)
                db.add(ObjektSeiteKiVorschlag(
                    org_id=daten["org_id"], seite_id=daten["seite_id"], status=KI_VORSCHLAG_OFFEN,
                    **klassifikation,
                ))
            if stammdaten is not None:
                alte_stammdaten = db.query(ObjektStammdatenVorschlag).filter(
                    ObjektStammdatenVorschlag.seite_id == daten["seite_id"],
                    ObjektStammdatenVorschlag.status == KI_VORSCHLAG_OFFEN,
                ).all()
                for alt_stammdaten in alte_stammdaten:
                    db.delete(alt_stammdaten)
                db.add(ObjektStammdatenVorschlag(
                    org_id=daten["org_id"], objekt_id=daten["objekt_id"],
                    seite_id=daten["seite_id"], status=KI_VORSCHLAG_OFFEN, **stammdaten,
                ))
        db.commit()
    finally:
        db.close()
