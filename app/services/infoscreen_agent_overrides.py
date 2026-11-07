"""S5: feste Token-Routen und fail-closed Host-/DNS-Pruefung.

DNS wird bei jedem Speichern erneut geprueft, nicht beim lesenden Resolver.
Der Browser/Agent muss DNS-Rebinding und Weiterleitungen ebenfalls begrenzen.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.security import hash_api_key
from app.models.infoscreen_agent import InfoscreenUrlAllowlist
from app.models.master import OrgSettings
from app.models.stats import StatistikDashboardToken
from app.models.weather import WeatherDashboardToken

KINDS = {"weather": "Wetter", "statistik": "Statistik", "custom_allowlisted": "Freigegebene URL"}


def hostname(value: str, *, dns: bool = True) -> str:
    if not value or len(value) > 253 or value != value.strip():
        raise ValueError("Host erforderlich, maximal 253 Zeichen")
    try:
        host = value.encode("idna").decode("ascii").lower()
        host.encode("ascii").decode("idna")
    except UnicodeError as exc:
        raise ValueError("Ungueltiger Domainname") from exc
    if len(host) > 253 or "." not in host or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Nur oeffentliche Domainnamen sind erlaubt")
    labels = host.split(".")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("Ungueltiger Domainname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP-Literale sind nicht erlaubt")
    if labels[-1].isdigit():
        raise ValueError("Ungueltige Domain-Endung")
    if dns:
        try:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(answer[4][0]) for answer in answers]
        except (OSError, ValueError) as exc:
            raise ValueError("DNS-Aufloesung fehlgeschlagen; Host wurde nicht gespeichert") from exc
        if not addresses or any(
            not address.is_global or address.is_multicast or address.is_reserved for address in addresses
        ):
            raise ValueError("DNS-Ziel muss ausschliesslich oeffentlich erreichbar sein")
    return host


def custom_url(db: Session, org_id: int, value: str, *, dns: bool = True) -> str:
    if len(value) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("Ungueltige URL, maximal 2048 Zeichen")
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError("Nur HTTPS ohne Zugangsdaten ist erlaubt")
        if parts.port not in (None, 443):
            raise ValueError("Nur Port 443 ist erlaubt")
        host = hostname(parts.hostname, dns=dns)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if (
        db.query(InfoscreenUrlAllowlist.id)
        .filter(InfoscreenUrlAllowlist.org_id == org_id, InfoscreenUrlAllowlist.host == host)
        .first()
        is None
    ):
        raise ValueError("Host ist nicht in der Allowlist dieser Organisation")
    # IDNA auch in der ausgelieferten URL normalisieren.
    return parts._replace(netloc=host, scheme="https").geturl()


def page_url(db: Session, org_id: int, kind: str, reference: str, *, stored: bool = False) -> str:
    if kind == "custom_allowlisted":
        return custom_url(db, org_id, reference, dns=not stored)
    if kind not in ("weather", "statistik"):
        raise ValueError("Diese Seite hat keine tokenbasierte Infoscreen-Route")
    prefix = "/wetter/infoscreen/" if kind == "weather" else "/infoscreen/statistik/"
    config = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if not reference and kind == "weather" and config:
        configured = urlsplit(config.alarm_infoscreen_wetter_url or "")
        # Ausschliesslich den Token wiederverwenden, niemals den konfigurierten Host.
        reference = configured.path.removeprefix(prefix) if configured.path.startswith(prefix) else ""
    if stored:
        if not reference.startswith(prefix):
            raise ValueError("Ungueltige gespeicherte Referenz")
        reference = reference.removeprefix(prefix)
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", reference):
        raise ValueError("Gueltigen Infoscreen-Token als Referenz eingeben")
    model = WeatherDashboardToken if kind == "weather" else StatistikDashboardToken
    if db.query(model.id).filter(model.org_id == org_id, model.token_hash == hash_api_key(reference)).first() is None:
        raise ValueError("Token nicht vorhanden oder aus einer anderen Organisation")
    if kind == "statistik" and (not config or not config.statistik_infoscreen_enabled):
        raise ValueError("Statistik-Infoscreen ist deaktiviert")
    return prefix + reference
