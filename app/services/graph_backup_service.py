"""Backup-Upload nach Microsoft Graph (SharePoint-Dokumentbibliothek / OneDrive).

Ein gemeinsames Protokoll fuer beide Ablageorte: adressiert wird ueber eine
**Drive-ID** (die Dokumentbibliothek einer SharePoint-Site ODER ein OneDrive-Drive)
plus einen Zielordner. App-only (Client-Credentials); die Azure-App braucht je nach
Ziel `Sites.ReadWrite.All` (SharePoint) bzw. `Files.ReadWrite.All` (OneDrive).

Synchron (httpx.Client), weil remote_backup_service/upload synchron ist und aus einem
Thread (asyncio.to_thread) laeuft. Upload via Upload-Session (beliebige Dateigroesse).
Fuer Tests ist ein httpx.Client injizierbar (httpx.MockTransport).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger("einsatzleiter.backup.graph")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_CHUNK = 10 * 1024 * 1024  # 10 MiB (Vielfaches von 320 KiB)
_TIMEOUT = 120.0


@dataclass(frozen=True)
class GraphZiel:
    tenant: str
    client_id: str
    secret: str
    drive_id: str
    folder: str = ""


def _login_base() -> str:
    from app.config import settings
    return (getattr(settings, "MS_LOGIN_BASE_URL", "") or "https://login.microsoftonline.com").rstrip("/")


def _item_pfad(ziel: GraphZiel, name: str) -> str:
    return f"{ziel.folder.strip('/')}/{name}".strip("/")


def hole_token(ziel: GraphZiel, *, client: httpx.Client) -> str:
    url = f"{_login_base()}/{ziel.tenant}/oauth2/v2.0/token"
    r = client.post(url, data={
        "grant_type": "client_credentials",
        "client_id": ziel.client_id,
        "client_secret": ziel.secret,
        "scope": GRAPH_SCOPE,
    })
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError("Graph-Token-Antwort ohne access_token")
    return token


def _mit_client(client: httpx.Client | None):
    if client is not None:
        return client, False
    return httpx.Client(timeout=_TIMEOUT), True


def upload(ziel: GraphZiel, local_path: Path, *,
           client: httpx.Client | None = None, token: str | None = None) -> None:
    """Laedt local_path per Upload-Session in drive/folder (ersetzt bei Namensgleichheit)."""
    client, close = _mit_client(client)
    try:
        if token is None:
            token = hole_token(ziel, client=client)
        headers = {"Authorization": f"Bearer {token}"}
        pfad = _item_pfad(ziel, local_path.name)
        sess = client.post(
            f"{GRAPH_BASE}/drives/{ziel.drive_id}/root:/{pfad}:/createUploadSession",
            headers=headers,
            json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        )
        sess.raise_for_status()
        upload_url = sess.json()["uploadUrl"]

        groesse = local_path.stat().st_size
        with open(local_path, "rb") as fh:
            start = 0
            while start < groesse:
                block = fh.read(_CHUNK)
                if not block:
                    break
                ende = start + len(block) - 1
                r = client.put(upload_url, content=block, headers={
                    "Content-Range": f"bytes {start}-{ende}/{groesse}",
                })
                if r.status_code not in (200, 201, 202):
                    r.raise_for_status()
                start += len(block)
    finally:
        if close:
            client.close()


def liste(ziel: GraphZiel, praefix: str, *,
          client: httpx.Client | None = None, token: str | None = None) -> list[str]:
    """Listet Dateinamen im Zielordner mit gegebenem Praefix."""
    client, close = _mit_client(client)
    try:
        if token is None:
            token = hole_token(ziel, client=client)
        headers = {"Authorization": f"Bearer {token}"}
        ordner = ziel.folder.strip("/")
        url = (f"{GRAPH_BASE}/drives/{ziel.drive_id}/root:/{ordner}:/children?$select=name&$top=200"
               if ordner else
               f"{GRAPH_BASE}/drives/{ziel.drive_id}/root/children?$select=name&$top=200")
        namen: list[str] = []
        while url:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            j = r.json()
            namen += [it["name"] for it in j.get("value", []) if it.get("name")]
            url = j.get("@odata.nextLink")
        return [n for n in namen if n.startswith(praefix)]
    finally:
        if close:
            client.close()


def loesche(ziel: GraphZiel, name: str, *,
            client: httpx.Client | None = None, token: str | None = None) -> None:
    client, close = _mit_client(client)
    try:
        if token is None:
            token = hole_token(ziel, client=client)
        headers = {"Authorization": f"Bearer {token}"}
        r = client.delete(
            f"{GRAPH_BASE}/drives/{ziel.drive_id}/root:/{_item_pfad(ziel, name)}",
            headers=headers)
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()
    finally:
        if close:
            client.close()
