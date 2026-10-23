"""SEC-03: Rueckwirkende Analyse fehlgeschlagener Geraetezugriffe aus Access-Logs.

Reine Auswertung (keine Datenbankaenderung). Tokens stehen nicht im Log (nur der Query-String bei
``/geraet-login?token=`` taucht auf und wird hier sofort verworfen); eine Zuordnung zu einem Geraet ist deshalb nur
ueber fruehere erfolgreiche Anmeldungen derselben IP-Adresse moeglich und nur, wenn genau ein Geraet dazu passt.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

# gunicorn/nginx "combined": IP - - [10/Oct/2026:12:00:00 +0000] "GET /pfad HTTP/1.1" 401 123 "ref" "UA"
_ZEILE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<zeit>[^\]]+)\] "(?P<methode>[A-Z]+) (?P<pfad>\S+)[^"]*" (?P<status>\d{3}) \S+'
    r'(?: "[^"]*" "(?P<ua>[^"]*)")?'
)
_DEVICE_PFADE = ("/api/v1/device/",)
_LOGIN_UNGUELTIG = "/login?error=device_invalid"


@dataclass
class Fehlzugriff:
    ip: str
    zeit: datetime | None
    pfad: str
    status: int
    user_agent: str | None


@dataclass
class Gruppe:
    ip: str
    user_agent: str | None
    anzahl: int = 0
    erster: datetime | None = None
    letzter: datetime | None = None
    pfade: set[str] = field(default_factory=set)


def _zeit(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None


def _ohne_token(pfad: str) -> str:
    return re.sub(r"([?&]token=)[^&\s]+", r"\1***", pfad)


def parse_zeilen(zeilen) -> list[Fehlzugriff]:
    """Fehlgeschlagene Geraetezugriffe: 401 auf /api/v1/device/* oder Redirect auf /login?error=device_invalid."""
    treffer = []
    for zeile in zeilen:
        m = _ZEILE.match(zeile.strip())
        if not m:
            continue
        pfad = m["pfad"]
        status = int(m["status"])
        geraet_401 = status == 401 and pfad.startswith(_DEVICE_PFADE)
        if not (geraet_401 or _LOGIN_UNGUELTIG in pfad):
            continue
        treffer.append(Fehlzugriff(m["ip"], _zeit(m["zeit"]), _ohne_token(pfad), status, m["ua"]))
    return treffer


def gruppiere(zugriffe: list[Fehlzugriff]) -> list[Gruppe]:
    gruppen: dict[tuple[str, str | None], Gruppe] = {}
    for z in zugriffe:
        g = gruppen.setdefault((z.ip, z.user_agent), Gruppe(z.ip, z.user_agent))
        g.anzahl += 1
        g.pfade.add(z.pfad.split("?")[0])
        if z.zeit and (g.erster is None or z.zeit < g.erster):
            g.erster = z.zeit
        if z.zeit and (g.letzter is None or z.zeit > g.letzter):
            g.letzter = z.zeit
    return sorted(gruppen.values(), key=lambda g: -g.anzahl)


def zuordnen(gruppen: list[Gruppe], geraete_je_ip: dict[str, set[str]]) -> list[dict]:
    """Ordnet Gruppen einem Geraet zu, nur wenn die IP in frueheren Anmeldungen genau einem Geraet gehoerte."""
    ergebnis = []
    for g in gruppen:
        kandidaten = geraete_je_ip.get(g.ip, set())
        if len(kandidaten) == 1:
            zuordnung, grund = next(iter(kandidaten)), "IP stimmt mit genau einem bekannten Geraet ueberein"
        elif len(kandidaten) > 1:
            zuordnung, grund = None, f"IP gehoert zu {len(kandidaten)} Geraeten (nicht eindeutig)"
        else:
            zuordnung, grund = None, "keine fruehere Anmeldung von dieser IP bekannt"
        ergebnis.append({"gruppe": g, "geraet": zuordnung, "grund": grund})
    return ergebnis


def geraete_je_ip(db) -> dict[str, set[str]]:
    """IP -> Geraetebezeichnungen aus erfolgreichen Geraete-Logins (Audit ``auth.device_login``)."""
    import json

    from app.models.user import AuditLog

    ergebnis: dict[str, set[str]] = defaultdict(set)
    for a in db.query(AuditLog).filter(AuditLog.action == "auth.device_login", AuditLog.ip.isnot(None)):
        try:
            label = (json.loads(a.payload_json or "{}")).get("label")
        except ValueError:
            label = None
        if label:
            ergebnis[a.ip].add(label)
    return ergebnis
