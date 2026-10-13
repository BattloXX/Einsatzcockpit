"""Sicheres Laden von ChatGPT-Dateianhaengen ueber kurzlebige HTTPS-URLs."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx


def _pruefe_url(url: str) -> None:
    teile = urlsplit(url)
    if teile.scheme != "https" or not teile.hostname:
        raise ValueError("Datei-URL muss HTTPS verwenden.")
    try:
        ipaddress.ip_address(teile.hostname)
    except ValueError:
        pass
    else:
        # IP-Literale muessen ebenfalls global routbar sein.
        if not _ist_global(teile.hostname):
            raise ValueError("Datei-URL verweist auf eine unzulaessige Adresse.")

    try:
        antworten = socket.getaddrinfo(teile.hostname, teile.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Datei-Host konnte nicht aufgeloest werden.") from exc
    if not antworten or any(not _ist_global(str(antwort[4][0])) for antwort in antworten):
        raise ValueError("Datei-URL verweist auf eine unzulaessige Adresse.")
    # DNS-Rebinding zwischen dieser Pruefung und dem Verbindungsaufbau bleibt ein Restrisiko.


def _ist_global(adresse: str) -> bool:
    ip = ipaddress.ip_address(adresse)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


def lade_datei_von_url(
    url: str,
    max_bytes: int,
    *,
    transport: httpx.BaseTransport | None = None,
    client: httpx.Client | None = None,
) -> bytes:
    """Laedt eine PDF mit URL-, Redirect- und Groessenbegrenzung."""
    if max_bytes < 0:
        raise ValueError("max_bytes muss nicht-negativ sein.")
    eigener_client = client is None
    http_client = client or httpx.Client(
        transport=transport,
        follow_redirects=False,
        timeout=httpx.Timeout(30.0),
    )
    aktuelle_url = url
    try:
        for hop in range(4):
            _pruefe_url(aktuelle_url)
            with http_client.stream("GET", aktuelle_url, follow_redirects=False) as antwort:
                if 300 <= antwort.status_code < 400:
                    if hop == 3 or not (ziel := antwort.headers.get("location")):
                        raise ValueError("Datei-Weiterleitung ist ungueltig.")
                    aktuelle_url = urljoin(aktuelle_url, ziel)
                    continue
                if not 200 <= antwort.status_code < 300:
                    raise ValueError(f"Datei-Download fehlgeschlagen (HTTP {antwort.status_code}).")
                groesse = antwort.headers.get("content-length", "")
                if groesse.isdigit() and int(groesse) > max_bytes:
                    raise ValueError(f"Datei zu gross (max. {max_bytes} Bytes).")
                daten = bytearray()
                for block in antwort.iter_bytes():
                    daten.extend(block)
                    if len(daten) > max_bytes:
                        raise ValueError(f"Datei zu gross (max. {max_bytes} Bytes).")
                ergebnis = bytes(daten)
                if not ergebnis.startswith(b"%PDF-"):
                    raise ValueError("Datei ist kein PDF.")
                return ergebnis
        raise ValueError("Datei-Weiterleitung ist ungueltig.")
    finally:
        if eigener_client:
            http_client.close()
