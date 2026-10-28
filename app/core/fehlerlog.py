"""Einzeilige Fehlerdiagnose ohne Ausnahmetext oder frei durchgereichten Kontext.

Hintergrundtasks setzen beim Einstieg mit korrelation_setzen() eine eigene ID,
statt die ID eines erzeugenden Requests weiterzuverwenden.
"""

from __future__ import annotations

import logging
import math
import re
from contextvars import ContextVar, Token
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from app.services import gateway_presence

korrelations_id: ContextVar[str | None] = ContextVar("korrelations_id", default=None)


def neue_korrelations_id() -> str:
    """Erzeugt eine unabhaengige ID ohne Nutzdaten."""
    return uuid4().hex


def korrelation_setzen(id: str | None = None) -> Token[str | None]:
    """Setzt eine validierte Request-ID; das Token dient zum Zuruecksetzen."""
    gueltig = id is not None and re.fullmatch(r"[A-Za-z0-9-]{8,64}", id) is not None
    return korrelations_id.set(id if gueltig else neue_korrelations_id())


def _kontextwert(schluessel: str, wert: object) -> str | int | None:
    """Nur bekannte Kanaele und kurze Zahlen zulassen, keine freien Texte/Objekte."""
    if schluessel in {"channel", "kanal"}:
        return wert if type(wert) is str and wert in {"sms", "teams", "push", "mail", "redis", "websocket"} else None
    if schluessel in {"incident_id", "org_id", "job_id", "versuch", "code"}:
        if type(wert) is int and 0 <= wert <= 999_999_999:
            return wert
        if type(wert) is str and re.fullmatch(r"[0-9]{1,9}", wert):
            return int(wert)
    return None


def log_fehler(
    logger: logging.Logger, ereignis: str, exc: BaseException, *,
    dauer_s: float | None = None, level: int = logging.WARNING, **kontext: object,
) -> None:
    """Loggt genau eine Zeile; Ereignis ist ein fester Text der Aufrufstelle.

    IDs/Codes sind auf neun Ziffern begrenzt, Kanaele auf feste Bezeichnungen.
    Unbekannte Schluessel und ungeeignete Werte werden vollstaendig verworfen.
    Bei OperationalError wird ausschliesslich der numerische DBAPI-Code ergaenzt.
    """
    dauer_ms = round(max(0, dauer_s) * 1000) if dauer_s is not None and math.isfinite(dauer_s) else None
    felder = [
        " ".join(ereignis.split()), f"fehlerklasse={type(exc).__name__}",
        f"dauer_ms={dauer_ms}", f"worker_id={gateway_presence.worker_id()}",
        f"korrelation_id={korrelations_id.get() or neue_korrelations_id()}",
    ]
    for schluessel, wert in kontext.items():
        sicher = _kontextwert(schluessel, wert)
        if sicher is not None:
            felder.append(f"{schluessel}={sicher}")
    if isinstance(exc, OperationalError) and exc.orig is not None and exc.orig.args:
        code = _kontextwert("code", exc.orig.args[0])
        if code is not None:
            felder.append(f"dbapi_code={code}")
    logger.log(level, " ".join(felder), exc_info=False, stack_info=False)
