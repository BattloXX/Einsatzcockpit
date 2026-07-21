"""Client für die DIBOS-EventHub-Schnittstelle (Elvis-Desktop-Client, Landeswarnzentrale
Vorarlberg).

Reverse-engineered aus einem echten HTTP-Mitschnitt des Elvis-Clients (v2.3.0.1),
NICHT aus einer Dokumentation. Anders als LIS/IPR (siehe app/services/lis/, SOAP 1.1/
WCF-Schnittstelle mit XML-Antworten) ist DIBOS EventHub eine eigenständige, einfachere
Schnittstelle:

- Antworten sind **JSON** (kein SOAP-Body-Parsing nötig), nur der Request-Umschlag ist
  ein minimaler SOAP-1.1-Envelope.
- Auth hat ZWEI unabhängige Ebenen: ein HTTP-Basic-"Gateway"-Konto (vom Betreiber
  vergeben, z.B. Nutzer "elvis") UND ein WS-Security-`UsernameToken` im SOAP-Header
  (das Org-Servicekonto, z.B. "service.<orgslug>.all"). Im Mitschnitt liefert der
  allererste Request (ohne Session-Cookie) durchgehend `401`; der Server setzt dabei
  bereits ein Session-Cookie, mit dem Folge-Requests `200` liefern. Ein persistenter
  Client mit Cookie-Jar (nicht Client-pro-Request wie bei LisClient) ist hier also
  nötig, um den Handshake abzubilden.
- Kadenz im Mitschnitt: ~20s Polling auf Main/GetCurrentEvents etc.

Diese Datei implementiert bewusst nur LESE-Endpunkte (keine Alarmierung, kein
Status-Rückschreiben) — siehe dibos_capture.py für das Tracing, das diese Endpunkte
periodisch abruft.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger("einsatzleiter.dibos.client")

_NS_SOAPENV = "http://schemas.xmlsoap.org/soap/envelope/"
_NS_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
_NS_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
_NS_USER = "https://dibos.lwz-vorarlberg.at/LWZ_EventHub/"


class DibosClientError(Exception):
    """Transport-, Auth- oder Parsingfehler bei der Kommunikation mit DIBOS EventHub."""


class DibosAuthError(DibosClientError):
    """Anhaltender 401 trotz Session-Cookie (Gateway- oder Servicekonto falsch)."""


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


class DibosClient:
    """Ein DIBOS-Client pro Organisation/Session (Cookie-Jar ist zustandsbehaftet,
    nicht über mehrere gleichzeitige Polls hinweg teilen).
    """

    def __init__(
        self,
        base_url: str,
        gateway_user: str,
        gateway_password: str,
        service_user: str,
        service_password: str,
        host: str = "einsatzcockpit",
        ag: str = "FW",
        timeout: float = 20.0,
        on_exchange: Callable[[str, str, bytes, bytes], None] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.gateway_user = gateway_user
        self.gateway_password = gateway_password
        self.service_user = service_user
        self.service_password = service_password
        self.host = host
        self.ag = ag
        self.timeout = timeout
        # Diagnose-Hook: wird nach jedem HTTP-Austausch mit (url, operation,
        # request_bytes, response_bytes) aufgerufen — siehe dibos_capture.py.
        # Rein lesend/beobachtend, hat keinen Einfluss auf den normalen Ablauf.
        self._on_exchange = on_exchange
        # Persistenter Client (Cookie-Jar!) — der Handshake im Mitschnitt zeigt:
        # erster Request ohne Cookie -> 401, Server setzt dabei ein Session-Cookie,
        # Folge-Requests mit diesem Cookie -> 200. Anders als LisClient (Client pro
        # Request) muss dieser Client über die Laufzeit einer Polling-Session hinweg
        # wiederverwendet werden.
        self._client = httpx.AsyncClient(
            auth=(self.gateway_user, self.gateway_password), timeout=self.timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _ws_envelope(self) -> bytes:
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<soapenv:Envelope xmlns:soapenv="{_NS_SOAPENV}" xmlns:user="{_NS_USER}">'
            "<soapenv:Header>"
            f'<o:Security xmlns:u="{_NS_WSU}" xmlns:o="{_NS_WSSE}">'
            f'<o:UsernameToken xmlns:o="{_NS_WSSE}">'
            f"<o:Username>{_xml_escape(self.service_user)}</o:Username>"
            f"<o:Password>{_xml_escape(self.service_password)}</o:Password>"
            "</o:UsernameToken>"
            "</o:Security>"
            "</soapenv:Header>"
            "<soapenv:Body></soapenv:Body>"
            "</soapenv:Envelope>"
        )
        return body.encode("utf-8")

    async def _post(self, path: str, operation: str, query: str = "") -> Any:
        url = f"{self.base_url}/{path}{query}"
        envelope = self._ws_envelope()
        headers = {
            "Accept": "text/plain",
            "Content-Type": "text/xml; charset=utf-8",
        }
        try:
            resp = await self._client.post(url, content=envelope, headers=headers)
        except httpx.HTTPError as exc:
            raise DibosClientError(f"Transportfehler bei {url}: {exc}") from exc

        if self._on_exchange:
            try:
                self._on_exchange(url, operation, envelope, resp.content)
            except Exception:
                logger.exception("on_exchange-Hook fehlgeschlagen (wird ignoriert)")

        if resp.status_code == 401:
            # Erster Request ohne Session-Cookie liefert immer 401, setzt dabei aber
            # das Cookie (siehe Modul-Docstring) — ein Retry mit demselben (jetzt um
            # das Cookie ergänzten) Client klärt, ob es Auth-Handshake oder ein echter
            # Credential-Fehler war.
            try:
                resp2 = await self._client.post(url, content=envelope, headers=headers)
            except httpx.HTTPError as exc:
                raise DibosClientError(f"Transportfehler bei Retry {url}: {exc}") from exc
            if self._on_exchange:
                try:
                    self._on_exchange(url, operation, envelope, resp2.content)
                except Exception:
                    logger.exception("on_exchange-Hook fehlgeschlagen (wird ignoriert)")
            if resp2.status_code == 401:
                raise DibosAuthError(
                    f"DIBOS-Login fehlgeschlagen ({operation}): weiterhin 401 nach Cookie-Retry "
                    "- Gateway- oder Servicekonto pruefen"
                )
            resp = resp2
        elif resp.status_code >= 400:
            raise DibosClientError(f"DIBOS HTTP {resp.status_code} bei {operation}: {resp.text[:300]}")

        try:
            return json.loads(resp.content) if resp.content else None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DibosClientError(f"Ungueltige JSON-Antwort von {operation}: {exc}") from exc

    # ── Main.svc (Ereignisse/Einheiten/Funk) ────────────────────────────────
    async def get_current_events(self) -> list[dict]:
        """Eigene aktive Einsätze der Org (leer, solange kein Einsatz läuft)."""
        result = await self._post("Main/GetCurrentEvents", "GetCurrentEvents")
        return result if isinstance(result, list) else []

    async def get_public_events(self, qty: int = 15) -> list[dict]:
        """Regionale Einsätze anderer Wehren derselben Agentur (ag), z.B. für Alarmierungsradar."""
        result = await self._post(
            "Main/GetPublicEvents", "GetPublicEvents", f"?qty={qty}&ag={self.ag}",
        )
        return result if isinstance(result, list) else []

    async def get_current_units(self) -> list[dict]:
        result = await self._post("Main/GetCurrentUnits", "GetCurrentUnits")
        return result if isinstance(result, list) else []

    async def get_current_radios(self) -> list[dict]:
        result = await self._post("Main/GetCurrentRadios", "GetCurrentRadios")
        return result if isinstance(result, list) else []

    # ── Elvis.svc (Zusatzinfos) ──────────────────────────────────────────────
    async def get_elvis_notification(self) -> list[dict]:
        result = await self._post(
            "Elvis/GetElvisNotification",
            "GetElvisNotification",
            f"?serviceUser={self.service_user}&host={self.host}",
        )
        return result if isinstance(result, list) else []

    async def test_connection(self) -> tuple[bool, str]:
        """Für den 'Verbindung testen'-Button im Admin-UI: wirft nie durch."""
        try:
            events = await self.get_current_events()
        except DibosAuthError as exc:
            return False, f"Anmeldung fehlgeschlagen: {exc}"
        except DibosClientError as exc:
            return False, f"Verbindung fehlgeschlagen: {exc}"
        return True, f"Verbindung erfolgreich ({len(events)} eigene Einsätze aktuell)"


# ── Live-Parsing: rohe DIBOS-Objekte auf lesbare Felder reduzieren ──────────
# Feldnamen 1:1 aus dem echten Mitschnitt entnommen (GetPublicEvents/GetCurrentUnits/
# GetCurrentRadios-Antworten), nicht aus einer Dokumentation abgeleitet.

def parse_events(events: list[dict]) -> list[dict]:
    """Reduziert ein rohes DIBOS-Event auf lesbare Felder.

    Erweitert 2026-07-21 (Vorfall f26006436, Wolfurt Unterlinden 23 — LIS-Feed
    speiste bis dahin einen Großteil der im Mitschnitt vorhandenen Felder gar
    nicht ein, u.a. den vollständigen Einsatzort, Einsatzcode/Diagnose, BMA-Nr.
    und das Meldungsprotokoll): NUR additiv, bestehende Schlüssel (inkl.
    "targets" als reine String-Liste) bleiben unverändert, damit vorhandene
    Konsumenten (latest.json, admin/_dibos_live.html) unverändert weiterlaufen.
    Neue Felder ergänzen bei Bedarf um Details, statt Bestehendes zu ersetzen.
    """
    parsed = []
    for e in events:
        callers = e.get("callerList") or []
        targets = e.get("targetList") or []
        comments = e.get("comments") or []
        parsed.append({
            "eventNumber": e.get("eventNumber"),
            "ag": e.get("ag"),
            "tycodDescription": e.get("tycodDescription"),
            "eventComment": e.get("eventComment"),
            "created": e.get("created"),
            "dispatched": e.get("dispatched"),
            "closed": e.get("closed"),
            "callers": [
                {"name": c.get("callerName"), "number": c.get("callerNumber")}
                for c in callers if isinstance(c, dict)
            ],
            "targets": [t.get("target") for t in targets if isinstance(t, dict)],
            # ── Neu: Einsatzcode/Diagnose, BMA-Nr., Gesamtstatus ────────────
            "tycod": e.get("tycod"),
            "subTycod": e.get("subTycod"),
            "diagnose": e.get("diagnose"),
            "bmaNo": e.get("bmaNo"),
            "status": e.get("status"),
            "statusText": e.get("statusText"),
            "statusTime": e.get("statusTime"),
            # ── Neu: vollständiger Einsatzort (bisher komplett verworfen) ───
            "location": {
                "city": e.get("locationCity"),
                "district": e.get("locationDistrict"),
                "cityPart": e.get("locationCityPart"),
                "street": e.get("locationStreet"),
                "streetNo": e.get("locationStreetNo"),
                "zipCode": e.get("locationZipCode"),
                "object": e.get("locationObject"),
                "longitude": e.get("locationLongitude"),
                "latitude": e.get("locationLatitude"),
            },
            # ── Neu: Ziel-Details (Typ/Anzahl), "targets" oben bleibt unverändert ──
            "targetsDetailed": [
                {
                    "target": t.get("target"),
                    "targetType": t.get("targetType"),
                    "targetCount": t.get("targetCount"),
                    "description": t.get("description"),
                }
                for t in targets if isinstance(t, dict)
            ],
            # ── Neu: Meldungs-/Kommentarprotokoll (Einheitenvorschlag, Dispose-
            # Meldungen, LWZ_Respond-Link etc.) ─────────────────────────────
            "comments": [
                {
                    "id": c.get("id"),
                    "text": c.get("comment"),
                    "isInternal": c.get("isInternal"),
                    "messageType": c.get("messageType"),
                    "creationDate": c.get("creationDate"),
                    "creationPerson": c.get("creationPerson"),
                }
                for c in comments if isinstance(c, dict)
            ],
        })
    return parsed


def parse_units(units: list[dict]) -> list[dict]:
    """Reduziert ein rohes DIBOS-Unit-Objekt auf lesbare Felder.

    Erweitert 2026-07-21 um die Fahrzeug-Statuszeiten (al/s1..s8/ueb/eta) —
    bisher komplett verworfen, obwohl im Mitschnitt vorhanden (z.B. für eine
    Anfahrt-Timeline im Einsatz). Additiv, bestehende Schlüssel unverändert.
    """
    return [
        {
            "unid": u.get("unid"),
            "unidRfl": u.get("unidRfl"),
            "unitType": u.get("unitType"),
            "currentStatusText": u.get("currentStatusText"),
            "currentStatusTime": u.get("currentStatusTime"),
            "longitude": u.get("longitude"),
            "latitude": u.get("latitude"),
            "eventNumber": u.get("eventNumber"),
            "station": u.get("station"),
            "ag": u.get("ag"),
            "statusTimes": {
                "al": u.get("al"),
                "s1": u.get("s1"),
                "s2": u.get("s2"),
                "s3": u.get("s3"),
                "s4": u.get("s4"),
                "s5": u.get("s5"),
                "s6": u.get("s6"),
                "s7": u.get("s7"),
                "s8": u.get("s8"),
                "ueb": u.get("ueb"),
                "eta": u.get("eta"),
            },
        }
        for u in units
    ]


def parse_radios(radios: list[dict]) -> list[dict]:
    return [
        {
            "issi": r.get("issi"),
            "alias": r.get("alias"),
            "talkGroup": r.get("talkGroup"),
            "state": r.get("state"),
            "department": r.get("department"),
        }
        for r in radios
    ]
