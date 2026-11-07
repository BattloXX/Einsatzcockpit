"""ELVis-Events des Windows-Agents, organisationsgebunden und wiederholbar."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.gateway import AlarmIngest, Gateway
from app.models.incident import Incident
from app.models.master import FireDept


def elvis_modus(gateway: Gateway) -> str:
    """Cloud-Konfiguration bleibt unabhaengig vom ueberschriebenen Agent-Inventar."""
    config = (gateway.wut_config or {}).get("elvis")
    mode = config.get("mode") if isinstance(config, dict) else None
    return mode if mode in ("aus", "protokollieren", "alarmquelle") else "protokollieren"


async def lese_event_body(request: Request) -> dict:
    """Auch ohne Content-Length den HTTP-Body vor JSON-Decodierung begrenzen."""
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 32768:
            raise HTTPException(413, "ELVis-Event ist groesser als 32 KiB")
        body.extend(chunk)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "Ungueltiges JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON-Objekt erwartet")
    return data


_ALIASE = {
    "nr": "Nr", "zeit": "Zeit", "status": "Status", "einsatz": "Einsatz", "esw": "ESW", "uesw": "UESW",
    "adresse": "Adresse", "objekt": "Objekt", "kommentar": "Kommentar", "t_art": "T-Art",
    "anrufer": "Anrufer", "anrufer_nr": "AnruferNr", "id": "ID", "organisation": "Organisation",
    "lev3": "LEV3", "abteilung": "Abteilung", "x_coord": "xCoord", "y_coord": "yCoord",
    "zielort": "Zielort", "dest_x_coord": "destinationXCoord", "dest_y_coord": "destinationYCoord",
    "strasse": "Straße", "hausnummer": "Hausnummer", "ort": "Ort",
}


# Wortanfang, damit "Attest" o. ae. keinen echten Einsatz als Test markiert.
_TESTMARKER = re.compile(r"\b(?:test|dummy)", re.IGNORECASE)


def _sanitizing(args: dict) -> tuple[dict, bool]:
    if not isinstance(args, dict):
        raise ValueError("args muss ein Objekt sein")
    try:
        size = len(json.dumps(args, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Ungueltige args") from exc
    if size > 32768:
        raise ValueError("args ist groesser als 32 KiB")
    keys = (
        "Nr", "Zeit", "Status", "Einsatz", "ESW", "UESW", "Adresse", "Objekt", "Kommentar",
        "T-Art", "Anrufer", "AnruferNr", "ID", "Organisation", "LEV3", "Abteilung",
        "xCoord", "yCoord", "Zielort", "destinationXCoord", "destinationYCoord",
        "Straße", "Hausnummer", "Ort",
    )
    # Der Windows-Agent sendet snake_case-Schluessel; ELVis-Namen gelten weiter.
    args = {**{_ALIASE.get(k, k): v for k, v in args.items()}, **{k: v for k, v in args.items() if k in keys}}
    clean: dict = {}
    for key in keys:
        value = args.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            clean[key] = "".join(c for c in str(value) if not unicodedata.category(c).startswith("C"))[
                :2000 if key == "Kommentar" else 500
            ].strip()
    # Vor dem Abschneiden pruefen: Auch ein Testmarker am Ende darf nie alarmieren.
    test = (args.get("test") is True or str(args.get("test", "")).lower() in ("true", "1")
            or clean.get("Nr", "").strip("0 ") == ""
            or clean.get("ESW", "").casefold() == "f30"
            or any(_TESTMARKER.search(value)
                   for key in ("Nr", "ESW", "Einsatz", "Kommentar")
                   for value in (str(args.get(key, "")), clean.get(key, ""))))
    clean["test"] = test
    return clean, test


def _alarmzeit(value: str, org: FireDept | None) -> datetime | None:
    from app.core.timezones import org_tz

    try:
        zeit = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            zeit = datetime.strptime(value, "%d.%m.%Y %H:%M:%S").replace(tzinfo=org_tz(org))
        except ValueError:
            return None
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=org_tz(org))
    return zeit.astimezone(UTC).replace(tzinfo=None)


def parsed_elvis(args: dict, raw_text: str, org: FireDept | None = None) -> dict:
    """Strukturierte Felder auf Parser-/Apply-Schema abbilden, keine Koordinaten raten."""
    from app.services.alarm_parser import AlarmParseResult
    from app.services.lis.lis_mapping import is_exercise_label, map_stichwort

    nr = args.get("Nr", "")
    nummer = nr.lower() if re.fullmatch(r"[a-zA-Z]\d{8}", nr) else None
    result = AlarmParseResult(
        original_text=raw_text, leitstellennummer=nummer,
        stichwort=args.get("ESW"), alarm_type_code=map_stichwort(args.get("ESW")),
        strasse=args.get("Straße") or args.get("Adresse"), hausnummer=args.get("Hausnummer"),
        gemeinde=args.get("Ort"), meldebild=args.get("Einsatz"),
        zusatzinfo=[args[key] for key in ("UESW", "Objekt", "Kommentar") if args.get(key)],
        alarmzeit=_alarmzeit(args.get("Zeit", ""), org),
        ist_uebung=is_exercise_label(args.get("Einsatz")),
        status="ok", parser_version="elvis-1",
    ).to_dict()
    return {
        **result, "street": result["strasse"], "house_no": result["hausnummer"],
        "city": result["gemeinde"], "started_at": result["alarmzeit"],
        "is_exercise": result["ist_uebung"], "reason": " > ".join(filter(None, [
            result["meldebild"], *result["zusatzinfo"],
        ])) or None,
    }


def verarbeite_elvis_event(
    db: Session, gateway: Gateway, event_id, args: dict, received_at,
    background_tasks: BackgroundTasks | None = None,
) -> dict:
    """Persistiert vor Verarbeitung; Tenant ausschliesslich aus dem Gateway.

    Annahme LWZ: Nr ist die menschenlesbare Leitstellennummer wie bei DIBOS/LIS
    (Parserformat Buchstabe + acht Ziffern). ID ist eine interne ELVis-ID und wird
    weder als Ersatznummer noch als external_key benutzt. Reale LWZ-Beispiele
    muessen diese Annahme vor Aktivierung als Alarmquelle bestaetigen.

    Die negative, aus event_id abgeleitete 63-Bit-Sequenz nutzt den bestehenden
    UNIQUE(gateway_id, sequence), auch bei parallelen Retries und nach Tagen.
    Der volle Event-Schluessel wird zusaetzlich auf Hashkollisionen geprueft.
    Inhaltsdedup gilt org-weit fuer ELVis innerhalb von zehn Minuten.
    """
    from app.services.alarm_apply_service import melde_ingest_geaendert, nachlauf
    from app.services.serial_alarm_service import process_ingest, raw_hash, store_raw

    if not gateway.is_windows_agent or gateway.org_id is None:
        raise ValueError("Windows-Agent mit Organisation erforderlich")
    if (not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 128
            or any(unicodedata.category(c).startswith("C") for c in event_id)):
        raise ValueError("event_id muss 1 bis 128 Zeichen enthalten")
    clean, test = _sanitizing(args)
    mode = elvis_modus(gateway)
    result = {"ingest_id": None, "duplicate": False, "einsatz_id": None, "modus": mode,
              "test": test, "parse_status": "parsed", "process_status": "discarded"}
    if mode == "aus":
        return result
    # Zeit ist Transport-/Aufrufrauschen. Der eigentliche Inhalt bleibt stabil.
    raw = "ELVis\n" + "\n".join(f"{key}: {clean[key]}" for key in sorted(clean) if key != "Zeit")
    sequence = -(int.from_bytes(hashlib.sha256(event_id.encode()).digest()[:8], "big") % (2**63 - 1) + 1)
    metadata = {"elvis_event_id": event_id, "elvis_args": clean, "elvis_mode": mode, "elvis_test": test}
    # Ein nicht alarmierendes Event muss schon im ersten Commit 'logged' sein:
    # Recovery darf auch bei einem Absturz zwischen Speicherung und Apply nichts anlegen.
    ingest, created = store_raw(
        db, org_id=gateway.org_id, gateway_id=gateway.id, raw_text=raw,
        parsed=metadata, parse_status="parsed", sequence=sequence, received_at_gateway=received_at,
        source="elvis", process_status="logged" if test or mode != "alarmquelle" else "stored",
    )
    if (ingest.parsed or {}).get("elvis_event_id") != event_id:
        raise ValueError("ELVis event_id kollidiert mit bestehender Sequenz")
    duplicate = not created
    if created:
        original = db.query(AlarmIngest).filter(
            AlarmIngest.org_id == gateway.org_id, AlarmIngest.source == "elvis",
            AlarmIngest.content_hash == raw_hash(raw), AlarmIngest.id != ingest.id,
            AlarmIngest.received_at >= datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10),
            AlarmIngest.process_status.in_(("logged", "applied")),
        ).order_by(AlarmIngest.id).first()
        if original is not None:
            duplicate = True
            ingest.process_status = "logged"
            ingest.einsatz_id = original.einsatz_id
            ingest.dedup_action = "duplicate"
            db.commit()
    if created and not duplicate and mode == "alarmquelle" and not test:
        applied: list[AlarmIngest] = []
        ingest = process_ingest(db, ingest.id, org_id=gateway.org_id, on_applied=applied.append)
        if applied and ingest.einsatz_id and ingest.dedup_action != "duplicate":
            tasks = background_tasks if background_tasks is not None else BackgroundTasks()
            nachlauf(db, ingest, org_id=gateway.org_id, background_tasks=tasks,
                     base_url=settings.effective_public_base_url)
            if background_tasks is None:
                from app.core.async_bridge import run_async
                run_async(tasks)
    if created:
        melde_ingest_geaendert(ingest)
    saved = ingest.parsed or {}
    return {**result, "ingest_id": ingest.id,
            "duplicate": duplicate or ingest.dedup_action == "duplicate", "einsatz_id": ingest.einsatz_id,
            "modus": saved.get("elvis_mode", mode), "test": saved.get("elvis_test", test),
            "parse_status": ingest.parse_status, "process_status": ingest.process_status}


def vergleiche_elvis_events(db: Session, org_id: int, gateway_id: int, limit: int = 20) -> dict:
    """Letzte Events und rein lesender Nummernabgleich in drei Batch-SELECTs.

    Delta = Cloud-Empfang minus Einsatzanlage (naive UTC). Testevents bleiben in
    der Liste, nehmen aber weder am Abgleich noch an der Statistik teil.
    include_all_tenants ist nur zusammen mit expliziten Org-Filtern erlaubt.
    Quellen sind Verknuepfungsnachweise, keine gesicherte Anlagehistorie:
    LIS-GUID, sonst anderer Ingest, sonst DIBOS-Annahme anhand des Nummernfelds.
    """
    with db.no_autoflush:
        events = (db.query(AlarmIngest).filter(
            AlarmIngest.org_id == org_id, AlarmIngest.gateway_id == gateway_id,
            AlarmIngest.source == "elvis",
        ).execution_options(include_all_tenants=True)
            .order_by(AlarmIngest.received_at.desc(), AlarmIngest.id.desc()).limit(max(0, limit)).all())
        nummern = {}
        for event in events:
            metadata = event.parsed or {}
            args = metadata.get("elvis_args", {})
            # Auch aeltere Datensaetze mit Testmarker in den Argumenten ausschliessen.
            if metadata.get("elvis_test") or _sanitizing(args)[1]:
                continue
            nummern[event.id] = parsed_elvis({"Nr": args.get("Nr", "")}, "")["leitstellennummer"]
        plausible = {nr for nr in nummern.values() if nr}
        treffer: dict[str, tuple[Incident, str, bool]] = {}
        if plausible:
            direkt = (db.query(Incident).filter(
                Incident.primary_org_id == org_id,
                func.lower(Incident.lis_operation_number).in_(plausible),
            ).execution_options(include_all_tenants=True).order_by(Incident.created_at, Incident.id).all())
            # Beide Seiten des Joins explizit scopen; fremde Ingest-Verknuepfungen
            # duerfen selbst mit fehlerhaften Daten keinen Einsatz offenlegen.
            andere = (db.query(AlarmIngest, Incident).join(Incident, Incident.id == AlarmIngest.einsatz_id).filter(
                AlarmIngest.org_id == org_id, Incident.primary_org_id == org_id,
                AlarmIngest.source != "elvis",
                or_(func.lower(AlarmIngest.leitstellennummer).in_(plausible),
                    Incident.id.in_([inc.id for inc in direkt])),
            ).execution_options(include_all_tenants=True)
                .order_by(Incident.created_at, Incident.id, AlarmIngest.id).all())
            quellen: dict[int, str] = {}
            for ingest, inc in andere:
                quelle = ingest.source if ingest.source in ("dibos", "lis") else "seriell"
                quellen.setdefault(inc.id, quelle)
                nr = (ingest.leitstellennummer or "").lower()
                if nr in plausible:
                    treffer.setdefault(nr, (inc, quelle, False))
            for inc in direkt:
                quelle = "lis" if inc.lis_operation_id else quellen.get(inc.id, "dibos")
                treffer[(inc.lis_operation_number or "").lower()] = (
                    inc, quelle, not inc.lis_operation_id and inc.id not in quellen,
                )
        ergebnisse: dict[int, dict[str, Any]] = {}
        for event in events:
            if event.id not in nummern:
                continue
            nr = nummern[event.id]
            match = treffer.get(nr)
            einsatz = match[0] if match else None
            quelle_name = match[1] if match else None
            angenommen = match[2] if match else False
            ergebnisse[event.id] = {
                "status": "nummer_unplausibel" if not nr else "zugeordnet" if einsatz else "nur_elvis",
                "delta_s": (event.received_at - einsatz.created_at).total_seconds() if einsatz else None,
                "einsatz_id": einsatz.id if einsatz else None,
                "einsatz_link": f"/einsatz/{einsatz.id}" if einsatz else None,
                "quelle": quelle_name, "quelle_angenommen": angenommen,
            }
        deltas = [r["delta_s"] for r in ergebnisse.values() if r["delta_s"] is not None]
        return {
            "elvis_events": events, "elvis_abgleich": ergebnisse,
            "elvis_auswertung": {
                "gesamt": len(ergebnisse),
                **{status: sum(r["status"] == status for r in ergebnisse.values())
                   for status in ("zugeordnet", "nur_elvis", "nummer_unplausibel")},
                "median_s": median(deltas) if deltas else None,
            },
        }
