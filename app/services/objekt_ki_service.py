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
    KI_VORSCHLAG_OFFEN,
    ObjektDokumentSeite,
    ObjektSeiteKiVorschlag,
)

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


async def analysiere_seite(seite: ObjektDokumentSeite, db: Session) -> ObjektSeiteKiVorschlag | None:
    """Erzeugt einen KI-Vorschlag fuer eine Seite (nutzt das Thumbnail-/Hi-Res-Bild).

    Gibt None zurueck bei fehlendem Rendering, KI-Fehler oder ungueltiger
    Antwort. Vorhandene offene Vorschlaege der Seite werden ersetzt.
    Caller committet.
    """
    from app.services.ai_service import AIServiceError, complete_vision
    from app.services.objekt_dokument_service import absolute_pfad

    bild_pfad = seite.bild_pfad or seite.thumb_pfad
    if not bild_pfad:
        return None
    pfad = absolute_pfad(bild_pfad)
    if not pfad.exists():
        return None

    bild = pfad.read_bytes()
    # Tokenkosten begrenzen: auf max. ~1024 px verkleinern
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
    return vorschlag


async def analysiere_unklassifizierte_seiten(objekt_id: int, *, limit: int = 20) -> int:
    """Background-Task: erzeugt Vorschlaege fuer unklassifizierte Seiten eines Objekts.

    Gibt die Anzahl erzeugter Vorschlaege zurueck. Eigene Session, Opt-in-Gate.
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    erzeugt = 0
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
        if not seiten:
            return 0
        if not ki_klassifikation_enabled(seiten[0].org_id, db):
            return 0
        # Seiten mit bereits offenem Vorschlag ueberspringen
        offene = {
            v.seite_id
            for v in db.query(ObjektSeiteKiVorschlag)
            .filter(
                ObjektSeiteKiVorschlag.seite_id.in_([s.id for s in seiten]),
                ObjektSeiteKiVorschlag.status == KI_VORSCHLAG_OFFEN,
            )
            .all()
        }
        for seite in seiten:
            if seite.id in offene:
                continue
            vorschlag = await analysiere_seite(seite, db)
            if vorschlag is not None:
                erzeugt += 1
        if erzeugt:
            db.commit()
        return erzeugt
    except Exception:
        logger.exception("KI-Analyse fuer Objekt %d fehlgeschlagen", objekt_id)
        try:
            db.rollback()
        except Exception:
            pass
        return erzeugt
    finally:
        db.close()
