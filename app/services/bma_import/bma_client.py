"""Client fuer die BMA-Webplattform (Landeswarnzentrale Vorarlberg).

Aus einem echten Mitschnitt der Plattform entnommen (angemeldet als Org-Nutzer,
2026-07-26), NICHT aus einer Dokumentation. Anders als DIBOS EventHub (siehe
app/services/dibos/dibos_client.py, WS-Security-UsernameToken) hat der
Portal-Login der BMA-Webplattform eine Anti-Roboter-Verifizierung (Friendly
Captcha) und teils 2FA - ein automatischer Login ist damit ausgeschlossen. Die
Plattform selbst authentifiziert danach aber schlicht ueber ein Session-Cookie
(ASP.NET), das im Admin-UI hinterlegt wird (siehe app/models/bma_import.py -
kompletter Cookie-String statt einzelnem Namen, robust gegen Umbenennungen).

Zwei Endpunkte:
- POST AlarmSystem/GetAllAlarmSystems liefert die Anlagenliste als Kendo-Grid-
  JSON ({"Data": [...], "Total": N}) - kein HTML-Parsing noetig.
- GET AlarmSystem/Details/{Id} liefert eine server-gerenderte HTML-Seite mit
  den Kontaktpersonen-Karten (siehe bma_parser.py::parse_kontakte).

Bewusst nur LESE-Endpunkte - dieser Client schreibt nichts in die BMA-Webplattform.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("einsatzleiter.bma_import.client")

_DEFAULT_TIMEOUT_S = 20.0
_USER_AGENT = "Einsatzcockpit/3.x (+https://einsatzcockpit.com; BMA-Import)"


def parse_cookie_paste(raw: str) -> str:
    """Baut aus dem im Admin-UI eingefügten Text einen fertigen `Cookie:`-Header-String.

    Akzeptiert zwei Formate, auch gemischt in mehreren Zeilen:
    1) Chrome-DevTools-Cookie-Tabelle (Anwendung/Application -> Cookies für
       dibos.lwz-vorarlberg.at): Zeilen markieren und kopieren liefert Tab-
       getrennte Spalten Name / Value / Domain / Path / Expires .../ Size /
       HttpOnly / Secure / SameSite / ... - hier werden nur die ersten beiden
       Spalten (Name, Value) je Zeile verwendet. Eine eventuelle Kopfzeile
       ("Name<TAB>Value...") wird übersprungen.
    2) Klassischer Cookie-Header von Hand: "name1=wert1; name2=wert2; ...".

    `str.partition("=")` (nicht `split`) trennt nur am ERSTEN Gleichheitszeichen,
    damit Werte, die selbst "=" enthalten (z.B. Base64-Padding wie bei
    PORTALTOKEN), nicht abgeschnitten werden.
    """
    paare: list[tuple[str, str]] = []
    for zeile in raw.splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        if "\t" in zeile:
            spalten = zeile.split("\t")
            name = spalten[0].strip()
            wert = spalten[1].strip() if len(spalten) > 1 else ""
            if name.lower() == "name" and wert.lower() == "value":
                continue  # Kopfzeile der DevTools-Tabelle
            if name:
                paare.append((name, wert))
        else:
            for teil in zeile.split(";"):
                teil = teil.strip()
                if not teil or "=" not in teil:
                    continue
                name, _, wert = teil.partition("=")
                name = name.strip()
                if name:
                    paare.append((name, wert.strip()))
    return "; ".join(f"{name}={wert}" for name, wert in paare)


class BmaClientError(Exception):
    """Transport- oder Parsingfehler bei der Kommunikation mit der BMA-Webplattform."""


class BmaSessionAbgelaufenError(BmaClientError):
    """Das hinterlegte Session-Cookie ist ungueltig/abgelaufen.

    Da der Login eine Anti-Roboter-Verifizierung hat, kann dieser Client sich
    NICHT selbst neu anmelden - der Org-Admin muss ein frisches Cookie hinterlegen
    (siehe /admin/bma-import).
    """


class BmaClient:
    """Ein Client pro Organisation/Aufruf (kein geteiltes Cookie-Jar noetig, das
    Cookie wird als fixer Header mitgeschickt - anders als dibos_client.py, wo
    der Server das Cookie erst beim ersten Request per Handshake setzt).
    """

    def __init__(self, base_url: str, cookie_header: str, timeout: float = _DEFAULT_TIMEOUT_S):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={
                "Cookie": cookie_header,
                "User-Agent": _USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=timeout,
            follow_redirects=False,  # ein Redirect auf /dibos-web/gui/ = Session abgelaufen
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _pruefe_sitzung(self, resp: httpx.Response, operation: str) -> None:
        if resp.status_code in (301, 302, 303, 307, 308):
            raise BmaSessionAbgelaufenError(
                f"BMA-Webplattform ({operation}): Redirect (Session abgelaufen?) auf "
                f"{resp.headers.get('location', '?')}"
            )
        if resp.status_code >= 400:
            raise BmaClientError(f"BMA-Webplattform HTTP {resp.status_code} bei {operation}")

    async def _hole_seite(self, skip: int, take: int) -> dict:
        """Ein einzelner GetAllAlarmSystems-Request; liefert die rohe Kendo-
        DataSourceResult ({"Data": [...], "Total": N}).

        Wirft BmaSessionAbgelaufenError bei abgelaufener Session, BmaClientError
        bei sonstigen Transport-/Formatfehlern.
        """
        url = f"{self.base_url}/AlarmSystem/GetAllAlarmSystems"
        body = f"take={take}&skip={skip}&page={skip // take + 1 if take else 1}&pageSize={take}"
        try:
            resp = await self._client.post(
                url, content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            )
        except httpx.HTTPError as exc:
            raise BmaClientError(f"Transportfehler bei GetAllAlarmSystems: {exc}") from exc
        self._pruefe_sitzung(resp, "GetAllAlarmSystems")

        try:
            daten = resp.json()
        except ValueError as exc:
            # Eine abgelaufene Session liefert oft HTML (Login-/Fehlerseite) statt JSON.
            raise BmaSessionAbgelaufenError(
                "BMA-Webplattform (GetAllAlarmSystems): Antwort ist kein JSON "
                "(Session vermutlich abgelaufen)"
            ) from exc
        if not isinstance(daten, dict) or "Data" not in daten or "Total" not in daten:
            # Body-Anfang direkt in die Fehlermeldung (landet in BmaImportLauf.meldung bzw.
            # der "Verbindung testen"-Antwort) - ohne das laesst sich diese Klasse Fehler
            # nicht diagnostizieren, da es (anders als bei DIBOS, siehe dibos_capture.py)
            # keine Rohdaten-Aufzeichnung fuer BMA gibt. Vollstaendiger Body zusaetzlich
            # geloggt (Server-Log, nicht in der DB gespeichert).
            logger.warning(
                "BMA-Webplattform (GetAllAlarmSystems): unerwartetes Antwortformat (HTTP %s) - Body: %s",
                resp.status_code, resp.text[:2000],
            )
            raise BmaClientError(
                "BMA-Webplattform (GetAllAlarmSystems): unerwartetes Antwortformat "
                f"(HTTP {resp.status_code}, Anfang: {resp.text[:200]!r})"
            )
        return daten

    async def hole_anlagen(self, seiten_groesse: int = 200) -> list[dict]:
        """Holt ALLE Anlagen (paginiert ueber Kendo-Grid skip/take, falls noetig).

        Wirft NIE bei leerer, aber gueltiger Antwort (das prueft die
        Plausibilitaetsuntergrenze in bma_sync.py, nicht der Client).
        """
        alle: list[dict] = []
        skip = 0
        gesamt: int | None = None
        while gesamt is None or skip < gesamt:
            daten = await self._hole_seite(skip, seiten_groesse)
            seite = daten.get("Data") or []
            alle.extend(x for x in seite if isinstance(x, dict))
            gesamt = int(daten.get("Total") or 0)
            if not seite:
                break  # Sicherheitsnetz gegen Endlosschleife bei kaputter Paginierung
            skip += len(seite)
        return alle

    async def hole_detail_html(self, extern_id: str) -> str:
        """Holt die Detailseite (Kontaktpersonen) einer einzelnen Anlage."""
        url = f"{self.base_url}/AlarmSystem/Details/{extern_id}"
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise BmaClientError(f"Transportfehler bei Details/{extern_id}: {exc}") from exc
        self._pruefe_sitzung(resp, f"Details/{extern_id}")
        text = resp.text
        # Eine abgelaufene Session liefert hier idR eine Login-Seite statt der
        # BMA-Detailseite - "BMANR" steht auf jeder echten Detailseite.
        if "BMANR" not in text:
            raise BmaSessionAbgelaufenError(
                f"BMA-Webplattform (Details/{extern_id}): Antwort sieht nicht wie eine "
                "Detailseite aus (Session vermutlich abgelaufen)"
            )
        return text

    async def test_connection(self) -> tuple[bool, str]:
        """Fuer den 'Verbindung testen'-Button im Admin-UI: wirft nie durch.

        Fragt nur die Gesamtzahl ab (eine einzelne Seite, take=1) statt aller
        Anlagen - der Button soll die Session pruefen, nicht die volle Liste laden.
        """
        try:
            daten = await self._hole_seite(0, 1)
        except BmaSessionAbgelaufenError as exc:
            return False, f"Session abgelaufen: {exc}"
        except BmaClientError as exc:
            return False, f"Verbindung fehlgeschlagen: {exc}"
        return True, f"Verbindung erfolgreich ({int(daten.get('Total') or 0)} Anlagen sichtbar)"

    async def keepalive(self) -> bool:
        """Leichter Ping, um die hinterlegte Session zwischen Laeufen aktiv zu
        halten (siehe bma_loop.py). Best-effort - ein Fehler hier wird nur
        geloggt, der naechste echte Lauf deckt eine abgelaufene Session ohnehin auf."""
        try:
            resp = await self._client.get(f"{self.base_url}/Home/Start")
            self._pruefe_sitzung(resp, "Keepalive")
            return True
        except BmaClientError:
            logger.info("BMA-Import Keepalive: Session vermutlich abgelaufen")
            return False
        except httpx.HTTPError:
            logger.warning("BMA-Import Keepalive: Transportfehler", exc_info=True)
            return False
