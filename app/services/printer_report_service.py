"""ECPG – Verarbeitung von printer_report-Meldungen des Gateways.

Discovery-Funde werden als Vorschläge (aktiv=False) gespeichert bzw. der Status
bereits aktiver Drucker aktualisiert. Aktivierung/Benennung erfolgt im Web-UI –
kein Drucker wird ungefragt eingerichtet.

Identität-Priorität für den Abgleich: serial > uuid > mac > uri/ip.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.db import SessionLocal
from app.core.tenant import set_tenant_context
from app.models.gateway import Printer

logger = logging.getLogger("einsatzleiter.print")


def _identity_key(identity: dict) -> str | None:
    for k in ("serial", "uuid", "mac"):
        v = (identity or {}).get(k)
        if v:
            return f"{k}:{v}"
    return None


def _match_printer(printers: list[Printer], entry: dict) -> Printer | None:
    ident = entry.get("identity") or {}
    key = _identity_key(ident)
    uri = entry.get("uri")
    for p in printers:
        if key and _identity_key(p.identity or {}) == key:
            return p
        if uri and p.uri == uri:
            return p
    return None


def apply_printer_report(gateway_id: int, org_id: int, payload: dict) -> None:
    """Speichert Vorschläge/Status aus einem printer_report. Eigene DB-Session,
    da aus dem WS-Handler ohne Request-Kontext aufgerufen."""
    entries = payload.get("printers") or payload.get("entries") or []
    if not entries:
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        existing = (
            db.query(Printer)
            .filter(Printer.gateway_id == gateway_id)
            .all()
        )
        for entry in entries:
            uri = entry.get("uri")
            if not uri:
                continue
            match = _match_printer(existing, entry)
            if match is None:
                p = Printer(
                    org_id=org_id,
                    gateway_id=gateway_id,
                    name=entry.get("name") or entry.get("modell") or uri,
                    modell=entry.get("modell"),
                    uri=uri,
                    identity=entry.get("identity") or {},
                    capabilities=entry.get("capabilities") or {},
                    aktiv=False,
                    status=entry.get("status") or {},
                    discovered_at=now,
                )
                db.add(p)
                existing.append(p)
            else:
                # IP-Wechsel bei DHCP erkennen + Status/Fähigkeiten aktualisieren.
                if uri and match.uri != uri:
                    logger.info("Drucker %s URI-Wechsel %s → %s", match.id, match.uri, uri)
                    match.uri = uri
                if entry.get("capabilities"):
                    match.capabilities = entry["capabilities"]
                if entry.get("status"):
                    st = dict(entry["status"])
                    st.setdefault("checked_at", now.isoformat())
                    match.status = st
                if entry.get("modell") and not match.modell:
                    match.modell = entry["modell"]
        db.commit()
    finally:
        db.close()
