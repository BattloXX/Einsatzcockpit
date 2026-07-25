"""Brandschutzplan-Upload ohne Objekt-Vorauswahl (Objektverwaltung).

Normaler Weg: Dokumente werden im Dokumente-Tab eines bereits existierenden
Objekts hochgeladen (siehe ui_objekt_dokumente.py::dokumente_upload). Dieses
Modul ermoeglicht den umgekehrten Weg: ein Brandschutzplan wird hochgeladen,
OHNE vorher ein Objekt auszuwaehlen - die KI liest Name/Adresse/BMA-Nummer aus
dem PDF (Titelblock/Objektbeschreibungs-Seite), BEVOR das Dokument gespeichert
wird, und loest damit das Ziel-Objekt auf:
  - eindeutiger Treffer (BMA-Nummer oder normalisierte Adresse) -> bestehendes
    Objekt wird ergaenzt
  - kein Treffer -> neues Entwurf-Objekt wird angelegt

"Identify-first": die Identifikation laeuft VOR dem Speichern des Dokuments,
damit nie ein Platzhalter-Objekt angelegt und spaeter wieder geloescht/gemergt
werden muss (das waere ein riskanteres FK-Umhaengen ueber mehrere Tabellen in
einem Background-Task, plus die Gefahr einer toten Redirect-URL).

Die eigentliche Dokumenten-Pipeline (Speichern, Seiten-Rendering, OCR,
KI-Klassifikation, Gefahren-/BMA-/Merkmale-Vorschlaege) laeuft danach
vollstaendig unveraendert weiter (objekt_dokument_service.py, objekt_ki_service.py).
"""
from __future__ import annotations

import io
import json
import logging

from sqlalchemy.orm import Session, selectinload

from app.core.audit import write_audit
from app.models.objekt import OBJEKT_STATUS_ENTWURF, Objekt
from app.models.user import User

logger = logging.getLogger("einsatzleiter.objekt_plan_upload")

# Ab dieser Anzahl gelesener Seiten wird abgebrochen (Titelblock/Objektbeschreibung
# steht in der Praxis immer in den ersten Seiten, siehe Meusburger-Beispiel Seite 4).
_MAX_SEITEN_IDENTIFY = 5
# Ab dieser Textlaenge gilt der PDF-Textlayer als "brauchbar genug" fuer eine
# Text-Extraktion; darunter (z. B. gescannte Seiten ohne Textlayer) Vision-Fallback.
_MIN_TEXT_LAENGE = 200


def _system_prompt_identitaet() -> str:
    return (
        "Du liest die ersten Seiten eines Brandschutzplans oder einer "
        "Objektinformation der Feuerwehr. Extrahiere NUR den Namen und die "
        "Adresse des Objekts/Betriebs, fuer den dieser Plan gilt (steht meist im "
        "Titelblock jeder Seite und/oder auf einer Objektbeschreibungs-Seite). "
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt ohne Markdown: "
        "{\"name\": <string|null>, \"strasse\": <string|null>, "
        "\"hausnummer\": <string|null>, \"plz\": <string|null>, \"ort\": <string|null>, "
        "\"bma_nummer\": <string|null>}. Erfinde nichts - lass unklare Felder null."
    )


def _parse_identitaet(text: str) -> dict | None:
    """Parst die JSON-Antwort; None bei ungueltigem JSON (gleiches Muster wie
    objekt_ki_service._parse_antwort)."""
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

    def _str(key: str, max_len: int) -> str | None:
        val = daten.get(key)
        return str(val)[:max_len] if val else None

    return {
        "name": _str("name", 200),
        "strasse": _str("strasse", 200),
        "hausnummer": _str("hausnummer", 20),
        "plz": _str("plz", 10),
        "ort": _str("ort", 100),
        "bma_nummer": _str("bma_nummer", 50),
    }


def _text_erster_seiten(data: bytes, max_seiten: int = _MAX_SEITEN_IDENTIFY) -> str:
    """PDF-Textlayer der ersten Seiten (kein Poppler noetig). Leerstring bei Fehler."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        teile = []
        for seite in reader.pages[:max_seiten]:
            try:
                teile.append(seite.extract_text() or "")
            except Exception:
                continue
        return "\n".join(teile).strip()
    except Exception:
        return ""


def _render_erste_seite(data: bytes) -> bytes | None:
    """Rendert Seite 1 direkt aus den hochgeladenen Bytes (kein Temp-File, kein
    Objekt/Speicherpfad noetig). None wenn Poppler/pdf2image nicht verfuegbar."""
    try:
        from pdf2image import convert_from_bytes
        bilder = convert_from_bytes(data, dpi=150, first_page=1, last_page=1)
        if not bilder:
            return None
        buf = io.BytesIO()
        bilder[0].save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _name_aus_dateiname(dateiname: str) -> str:
    """Letzter Fallback, falls die KI nichts liefert - der Upload darf nie hart
    fehlschlagen, nur weil die Identifikation nichts ergeben hat."""
    stamm = dateiname.rsplit(".", 1)[0] if "." in dateiname else dateiname
    lesbar = stamm.replace("_", " ").replace("-", " ").strip()
    return lesbar[:200] or "Neues Objekt"


async def identifiziere_objekt(data: bytes, dateiname: str, org_id: int) -> dict:
    """Ermittelt Name/Adresse/BMA-Nummer des Objekts, fuer das der Plan gilt.

    Text-zuerst (ai_service.complete, kein Poppler noetig, guenstiger/schneller),
    Vision-Fallback nur wenn der Textlayer zu duenn ist UND Poppler verfuegbar
    ist, sonst Dateiname-Fallback. Wirft NIE - der Upload muss in jedem Fall
    durchlaufen, auch ganz ohne KI-Ergebnis. Rueckgabe enthaelt zusaetzlich
    "quelle" (ki_text/ki_vision/dateiname) fuer Audit/Debugging.
    """
    from app.services.ai_service import AIServiceError, complete, complete_vision
    from app.services.objekt_dokument_service import _detect_mime

    fallback = {
        "name": _name_aus_dateiname(dateiname), "strasse": None, "hausnummer": None,
        "plz": None, "ort": None, "bma_nummer": None, "quelle": "dateiname",
    }

    # Kein KI-Aufruf fuer offensichtlich ungueltige Dateien verschwenden - die
    # eigentliche, autoritative Validierung (Groesse/Seitenzahl/Quota) macht
    # store_dokument_upload ohnehin gleich danach.
    if _detect_mime(data) != "application/pdf":
        return fallback

    try:
        text = _text_erster_seiten(data)
        if len(text) >= _MIN_TEXT_LAENGE:
            antwort = await complete(
                _system_prompt_identitaet(),
                f"Text der ersten Seiten dieses Dokuments:\n\n{text[:6000]}",
                fast=True, org_id=org_id,
            )
            geparst = _parse_identitaet(antwort)
            if geparst and geparst.get("name"):
                geparst["quelle"] = "ki_text"
                return geparst

        bild = _render_erste_seite(data)
        if bild:
            antwort = await complete_vision(
                _system_prompt_identitaet(),
                "Identifiziere das Objekt/den Betrieb dieses Dokuments.",
                [bild], org_id=org_id,
            )
            geparst = _parse_identitaet(antwort)
            if geparst and geparst.get("name"):
                geparst["quelle"] = "ki_vision"
                return geparst
    except AIServiceError as exc:
        logger.warning("Objekt-Identifikation aus Dokument fehlgeschlagen: %s", exc)
    except Exception:
        logger.exception("Objekt-Identifikation aus Dokument unerwartet fehlgeschlagen")

    return fallback


def finde_passendes_objekt(db: Session, org_id: int, identitaet: dict) -> Objekt | None:
    """Sucht ein bestehendes Objekt der Org, das eindeutig zur Identitaet passt.

    Bewusst konservativ (nur exakte Treffer, kein Fuzzy-/Geo-Match wie beim
    Einsatz-Matching) - ein falscher Treffer wuerde Gefahren-/BMA-Daten eines
    fremden Betriebs an ein falsches Objekt haengen. Anders als
    objekt_matching_service.match_incident (nur freigegeben/in_ueberarbeitung)
    werden hier AUCH Entwurf-Objekte einbezogen: die Alternative zu einem
    verpassten Match ist hier kein harmloser Nicht-Vorschlag, sondern ein
    echtes Duplikat-Objekt.
    """
    from app.services.objekt_matching_service import _norm_bma, _normalisierte_adresse

    objekte = (
        db.query(Objekt)
        .options(selectinload(Objekt.bma), selectinload(Objekt.zusatzadressen))
        .filter(Objekt.org_id == org_id)
        .all()
    )
    if not objekte:
        return None

    bma_norm = _norm_bma(identitaet.get("bma_nummer"))
    if bma_norm:
        for o in objekte:
            if o.bma is None:
                continue
            for wert in (o.bma.bma_nummer, o.bma.rfl_nummer):
                if _norm_bma(wert) == bma_norm:
                    return o

    ziel_adresse = _normalisierte_adresse(
        identitaet.get("strasse"), identitaet.get("hausnummer"), identitaet.get("ort")
    )
    if ziel_adresse:
        treffer = []
        for o in objekte:
            kandidaten = [_normalisierte_adresse(o.strasse, o.hausnummer, o.ort)]
            kandidaten += [
                _normalisierte_adresse(z.strasse, z.hausnummer, z.ort)
                for z in o.zusatzadressen
            ]
            if any(k and k == ziel_adresse for k in kandidaten):
                treffer.append(o)
        if len(treffer) == 1:
            return treffer[0]
        # Mehrdeutig (>= 2 Objekte gleiche normalisierte Adresse) -> nicht raten,
        # lieber ein neues Objekt anlegen als versehentlich falsch zu mergen.

    return None


def erstelle_objekt_aus_identitaet(db: Session, user: User, identitaet: dict) -> Objekt:
    """Legt ein neues Entwurf-Objekt aus der ermittelten Identitaet an (repliziert
    den Kern von ui_objekt.py::objekt_neu). Nur db.flush() - Caller committet
    (zusammen mit dem nachfolgenden store_dokument_upload in EINER Transaktion,
    damit bei einem Speicherfehler kein leeres Karteileichen-Objekt zurueckbleibt)."""
    from app.services.objekt_service import naechste_nummer, write_objekt_change

    objekt = Objekt(
        org_id=user.org_id,
        nummer=naechste_nummer(db, user.org_id),  # type: ignore[arg-type]
        name=(identitaet.get("name") or "Neues Objekt").strip() or "Neues Objekt",
        strasse=identitaet.get("strasse"),
        hausnummer=identitaet.get("hausnummer"),
        plz=identitaet.get("plz"),
        ort=identitaet.get("ort"),
        status=OBJEKT_STATUS_ENTWURF,
        erstellt_von_id=user.id,
        aktualisiert_von_id=user.id,
    )
    db.add(objekt)
    db.flush()
    write_objekt_change(
        db, objekt.id, user.org_id, "stammdaten", "angelegt",
        before=None, after=f"{objekt.name} (automatisch aus Brandschutzplan-Upload)",
        user_id=user.id,
    )
    write_audit(
        db, "objekt.created_from_plan_upload", org_id=user.org_id, user_id=user.id,
        entity_type="objekt", entity_id=objekt.id,
        payload={"name": objekt.name, "nummer": objekt.nummer, "quelle": identitaet.get("quelle")},
    )
    return objekt
