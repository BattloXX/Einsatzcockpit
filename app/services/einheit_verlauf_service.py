"""Lesende Projektion aus Aufträgen, Einsatzstellenchronik, Fotos und Aktionsprotokoll.

Keine neue Nachrichtenablage. Unverbundene Alteinträge bleiben ausdrücklich
Einsatzstellenmeldungen; eine gerade geöffnete Disposition ist kein Herkunftsnachweis.
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.major_incident import EinheitAktion, EinheitSiteDispatch, IncidentSite, SiteLogEntry, SiteMedia
from app.services.einheit_service import EinheitKontext
from app.services.site_log_service import meldung_hervorhebung


def _cursor_lesen(cursor: str | None) -> tuple[datetime, int, int] | None:
    if not cursor:
        return None
    try:
        if len(cursor) > 200:
            raise ValueError
        t, rang, ident = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        zeit = datetime.fromisoformat(t)
        if zeit.tzinfo or type(rang) is not int or rang not in range(1, 6) or type(ident) is not int or ident < 1:
            raise ValueError
        return zeit, rang, ident
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise ValueError("cursor_ungueltig") from exc


def _seite(query, zeit, ident, rang, cursor, limit):
    if cursor:
        t, r, i = cursor
        gleich = ident < i if rang == r else rang < r
        query = query.filter(or_(zeit < t, and_(zeit == t, gleich)))
    return query.order_by(zeit.desc(), ident.desc()).limit(limit + 1).all()


def meldungsverlauf(
    db: Session, ctx: EinheitKontext, *, dispatch: EinheitSiteDispatch | None = None,
    cursor: str | None = None, limit: int = 30, mit_status: bool = False,
) -> dict:
    grenze = _cursor_lesen(cursor)
    auftraege = db.query(EinheitSiteDispatch).join(IncidentSite).options(joinedload(EinheitSiteDispatch.site)).filter(
        EinheitSiteDispatch.einheit_id == ctx.einheit.id,
        IncidentSite.major_incident_id == ctx.lage.id, IncidentSite.org_id == ctx.org_id,
        *([EinheitSiteDispatch.site_id == dispatch.site_id] if dispatch else []),
    ).all()
    nach_id = {d.id: d for d in auftraege}
    stellen = {d.site_id: d.site for d in auftraege}
    freigegebene_stellen = [d.site_id for d in auftraege if d.withdrawn_at is None]
    if dispatch and dispatch.withdrawn_at is not None:
        freigegebene_stellen = []
    allgemein = ctx.quelle != "zugang"
    kandidaten: list[tuple[tuple[datetime, int, int], dict, object | None]] = []

    def hinzufuegen(zeit, rang, ident, site_id, daten, original=None):
        site = stellen[site_id]
        kandidaten.append(((zeit, rang, ident), {
            "id": f"{rang}:{ident}", "ts": zeit.replace(tzinfo=UTC).isoformat(timespec="microseconds"),
            "site_id": site_id,
            "stelle": site.bezeichnung, "dispatch_id": None, "auftragsart": None,
            "bezug": "Meldung zur Einsatzstelle", "client_uuid": None,
            "foto": None, "kompakt": False, "hervorhebung": "", **daten,
        }, original))

    # Nur eigene Meldungen und bereits berechtigte Einsatzstellenmitteilungen der Führung.
    eigene = SiteLogEntry.einheit_id == ctx.einheit.id
    leitung = and_(SiteLogEntry.einheit_id.is_(None), SiteLogEntry.incident_site_id.in_(freigegebene_stellen))
    log_zeit = func.coalesce(SiteLogEntry.erfasst_at, SiteLogEntry.ts)
    arten = ["note", "lagemeldung", "massnahmen"] + (["einheit"] if mit_status else [])
    logs = _seite(db.query(SiteLogEntry).filter(
        SiteLogEntry.incident_site_id.in_(stellen), SiteLogEntry.kind.in_(arten),
        or_(eigene, leitung) if allgemein else eigene,
        or_(SiteLogEntry.kind != "einheit", SiteLogEntry.text.startswith(ctx.einheit.label + ": ", autoescape=True)),
    ), log_zeit, SiteLogEntry.id, 1, grenze, limit)
    for row in logs:
        ist_eigene = row.einheit_id == ctx.einheit.id
        text = row.text.removeprefix(ctx.einheit.label + ": ") if row.kind == "einheit" else row.text
        hervorhebung = meldung_hervorhebung(text)
        if not ist_eigene and ("?" in text or text.casefold().startswith("rückfrage")):
            hervorhebung = "rueckfrage"
        hinzufuegen(row.erfasst_at or row.ts, 1, row.id, row.incident_site_id, {
            "text": text, "absender": "Eigene Einheit" if ist_eigene else "Einsatzleitung",
            "eigene": ist_eigene, "status": "uebermittelt" if ist_eigene else None,
            "art": "status" if row.kind == "einheit" else "meldung", "kompakt": row.kind == "einheit",
            "hervorhebung": hervorhebung,
        }, row)

    fotos = _seite(db.query(SiteMedia).filter(
        SiteMedia.incident_site_id.in_(stellen), SiteMedia.media_type == "image",
        or_(SiteMedia.org_id == ctx.org_id, SiteMedia.org_id.is_(None)),
        SiteMedia.incident_site_id.in_(freigegebene_stellen),
        or_(SiteMedia.einheit_id == ctx.einheit.id, and_(
            SiteMedia.einheit_id.is_(None), SiteMedia.incident_site_id.in_(freigegebene_stellen),
        )) if allgemein else SiteMedia.einheit_id == ctx.einheit.id,
    ), func.coalesce(SiteMedia.erfasst_at, SiteMedia.uploaded_at), SiteMedia.id, 2, grenze, limit)
    for foto in fotos:
        ist_eigene = foto.einheit_id == ctx.einheit.id
        hinzufuegen(foto.erfasst_at or foto.uploaded_at, 2, foto.id, foto.incident_site_id, {
            "art": "foto", "text": foto.kommentar or "Foto zur Einsatzstelle",
            "absender": "Eigene Einheit" if ist_eigene else "Einsatzleitung",
            "eigene": ist_eigene, "status": "uebermittelt" if ist_eigene else None,
            "foto": {"url": f"/einheit/medien/{foto.id}", "thumb_url": f"/einheit/medien/thumb/{foto.id}"},
        }, foto)

    # Zuweisung und letzter Änderungsstand stammen aus dem vorhandenen Auftrag.
    for rang, feld, titel, art in (
        (3, EinheitSiteDispatch.dispatched_at, "Neuer Auftrag", "auftrag"),
        (4, EinheitSiteDispatch.geaendert_at, "Auftrag geändert", "aenderung"),
        (5, EinheitSiteDispatch.withdrawn_at, "Auftrag zurückgezogen", "rueckzug"),
    ):
        query = db.query(EinheitSiteDispatch).filter(EinheitSiteDispatch.id.in_(nach_id), feld.isnot(None))
        for d in _seite(query, feld, EinheitSiteDispatch.id, rang, grenze, limit):
            text = titel
            if art == "aenderung" and d.auftrag:
                text += "\n" + d.auftrag
            if art == "rueckzug" and d.withdrawn_grund:
                text += "\n" + d.withdrawn_grund
            hinzufuegen(getattr(d, feld.key), rang, d.id, d.site_id, {
                "text": text, "art": art, "absender": "Einsatzleitung", "eigene": False, "status": None,
                "hervorhebung": art, "dispatch_id": d.id, "auftragsart": d.auftragsart,
                "bezug": "Auftrag: " + (d.auftrag or "Auftrag ohne zusätzlichen Text"),
            })

    ausgewaehlt = sorted(kandidaten, key=lambda item: item[0], reverse=True)[:limit + 1]
    mehr = len(ausgewaehlt) > limit
    ausgewaehlt = ausgewaehlt[:limit]
    log_ids = [row.id for _, _, row in ausgewaehlt if isinstance(row, SiteLogEntry)]
    foto_ids = [row.id for _, _, row in ausgewaehlt if isinstance(row, SiteMedia)]
    aktionen = db.query(EinheitAktion).filter(
        EinheitAktion.org_id == ctx.org_id, EinheitAktion.einheit_id == ctx.einheit.id,
        EinheitAktion.dispatch_id.in_(nach_id), EinheitAktion.ergebnis == "ok",
        or_(and_(EinheitAktion.entity_type == "site_log", EinheitAktion.entity_id.in_(log_ids)),
            and_(EinheitAktion.entity_type == "site_media", or_(EinheitAktion.entity_id.in_(foto_ids), and_(
                EinheitAktion.entity_id.is_(None),
                func.json_extract(EinheitAktion.antwort_json, "$.entity_id").in_(foto_ids),
            )))),
    ).all() if log_ids or foto_ids else []
    verknuepfungen = {(a.entity_type, a.entity_id): a for a in aktionen}
    for a in aktionen:
        if a.entity_type == "site_media" and a.entity_id is None:
            # Ältere Fotoaktionen hatten die ID nur in der erfolgreich gespeicherten Antwort.
            ident = json.loads(a.antwort_json or "{}").get("entity_id")
            if type(ident) is int:
                verknuepfungen[("site_media", ident)] = a
    status_zeiten = [row.erfasst_at for _, _, row in ausgewaehlt
                    if isinstance(row, SiteLogEntry) and row.kind == "einheit" and row.erfasst_at]
    status_aktionen = db.query(EinheitAktion).filter(
        EinheitAktion.org_id == ctx.org_id, EinheitAktion.einheit_id == ctx.einheit.id,
        EinheitAktion.dispatch_id.in_(nach_id), EinheitAktion.ergebnis == "ok", EinheitAktion.aktion == "status",
        EinheitAktion.erfasst_at.in_(status_zeiten),
    ).all() if status_zeiten else []
    for _, daten, row in ausgewaehlt:
        typ = "site_log" if isinstance(row, SiteLogEntry) else "site_media"
        aktion = verknuepfungen.get((typ, getattr(row, "id", None)))
        if isinstance(row, SiteLogEntry) and row.kind == "einheit":
            passend = [a for a in status_aktionen if a.erfasst_at == row.erfasst_at
                       and nach_id[a.dispatch_id or -1].site_id == row.incident_site_id]
            # Keine Zuordnung raten, wenn mehrere Statusaktionen denselben Zeitbezug haben.
            aktion = passend[0] if len(passend) == 1 else None
            if " (per " in row.text and not aktion:
                daten.update(absender="Einsatzleitung", eigene=False, status=None)
        if aktion and (d := nach_id.get(aktion.dispatch_id or -1)) and d.site_id == daten["site_id"]:
            daten.update(dispatch_id=d.id, auftragsart=d.auftragsart, client_uuid=aktion.client_uuid,
                         bezug="Auftrag: " + (d.auftrag or "Auftrag ohne zusätzlichen Text"))
    next_cursor = None
    if mehr:
        t, r, ident = ausgewaehlt[-1][0]
        next_cursor = base64.urlsafe_b64encode(json.dumps([t.isoformat(), r, ident]).encode()).decode().rstrip("=")
    return {"eintraege": [daten for _, daten, _ in ausgewaehlt], "cursor": next_cursor,
            "einheit_id": ctx.einheit.id, "lage_id": ctx.lage.id}
