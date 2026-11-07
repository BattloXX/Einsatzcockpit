"""Oeffentliche GitHub-Assets sind Transport; Vertrauen geben nur gepinnte Keys."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
from datetime import UTC, datetime
from typing import BinaryIO, Literal
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.infoscreen_agent import InfoscreenRelease
from app.models.master import SystemSettings
from app.services import infoscreen_agent_releases as releases
from app.services.loop_thread_io import im_db_thread
from app.services.loop_utils import iteration_watch

logger = logging.getLogger(__name__)
HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MAX_JSON = 1024 * 1024
MAX_KEY = 16 * 1024
CACHE_CHUNK = 48 * 1024
SYNC_S = 24 * 60 * 60


class ImportFailure(ValueError):
    """Nur feste Fehlercodes, nie HTTP-Inhalte oder Token an UI/Log geben."""


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    arch: Literal["amd64", "arm64"]
    package: str
    sha256: str
    size: StrictInt = Field(gt=0)
    min_version: str
    signature: str


class Installer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    file: Literal["install.sh"]
    signature: str = Field(min_length=1, max_length=100)


class ReleaseDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: StrictInt = Field(alias="schema")
    version: str
    channel: Literal["stable", "beta"]
    key_id: Literal["release"]
    artifacts: list[Artifact] = Field(min_length=1, max_length=2)
    install_sh: Installer
    key_file: Literal["release-key.pem"]


def repository() -> str:
    # Auch zur Laufzeit pruefen (Tests/programmatische Konfiguration).
    return settings.validate_agent_repo(settings.INFOSCREEN_AGENT_GITHUB_REPO)


def repo_url() -> str:
    return "https://github.com/" + repository()


def _url(url: str) -> None:
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == "https" and parts.hostname in HOSTS and parts.port in (None, 443)
                 and not parts.username and not parts.password and not parts.fragment
                 and "\\" not in url and not any(ord(c) < 33 or ord(c) == 127 for c in url))
    except ValueError:
        valid = False
    if not valid:
        raise ImportFailure("host_not_allowed")


def download(client: httpx.Client, url: str, output: BinaryIO, limit: int,
             etag: str | None = None) -> tuple[int, httpx.Headers]:
    """Begrenztes Streaming; jeden Redirect pruefen, Auth nur an GitHub-API."""
    for hop in range(4):
        _url(url)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "Einsatzcockpit-Infoscreen",
                   "Accept-Encoding": "identity"}
        if etag:
            headers["If-None-Match"] = etag
        if urlsplit(url).hostname == "api.github.com" and settings.INFOSCREEN_AGENT_GITHUB_TOKEN:
            headers["Authorization"] = "Bearer " + settings.INFOSCREEN_AGENT_GITHUB_TOKEN
        try:
            with client.stream("GET", url, headers=headers, follow_redirects=False,
                               timeout=httpx.Timeout(30, connect=10)) as response:
                if response.status_code in (403, 429):
                    raise ImportFailure("rate_limited")
                if response.status_code in (301, 302, 303, 307, 308):
                    if hop == 3 or not response.headers.get("location"):
                        raise ImportFailure("redirect_limit")
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code == 304:
                    return 304, response.headers
                if response.status_code != 200:
                    raise ImportFailure("download_failed")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ImportFailure("download_failed")
                length = response.headers.get("content-length")
                if length and (not length.isdigit() or int(length) > limit):
                    raise ImportFailure("size_limit")
                size = 0
                for chunk in response.iter_bytes(64 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ImportFailure("size_limit")
                    output.write(chunk)
                output.seek(0)
                return response.status_code, response.headers
        except httpx.HTTPError as exc:
            raise ImportFailure("download_failed") from exc
    raise ImportFailure("redirect_limit")


def _bytes(client: httpx.Client, url: str, limit: int) -> bytes:
    with tempfile.TemporaryFile() as stream:
        download(client, url, stream, limit)
        return stream.read()


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _json(data: bytes):
    try:
        return json.loads(data, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise ImportFailure("release_json_invalid") from exc


def _cache_key() -> str:
    return "infoscreen_github_" + hashlib.sha256(repository().encode()).hexdigest()[:32]


def sync_status(db: Session) -> dict:
    row = db.get(SystemSettings, _cache_key())
    if not row or not row.value:
        return {}
    root = json.loads(row.value)
    if "chunk_count" not in root:
        return root
    parts = []
    for number in range(root["chunk_count"]):
        part = db.get(SystemSettings, f"{_cache_key()}_{number}")
        if part is None or part.value is None:
            return {}  # Unvollstaendiger Cache: naechster Abgleich ohne ETag.
        parts.append(part.value)
    return json.loads("".join(parts))


def _save_status(db: Session, data: dict, now: datetime) -> None:
    # MariaDB TEXT ist auf 64 KiB begrenzt. ASCII-JSON in begrenzte Zeilen teilen;
    # Wurzel und Teile werden in derselben Transaktion ersetzt (kein Teilcache).
    encoded = json.dumps(data, ensure_ascii=True)
    key = _cache_key()
    root = db.get(SystemSettings, key)
    old_count = json.loads(root.value).get("chunk_count", 0) if root and root.value else 0
    chunks = [encoded[start:start + CACHE_CHUNK] for start in range(0, len(encoded), CACHE_CHUNK)]
    for number in range(max(old_count, len(chunks))):
        part_key = f"{key}_{number}"
        part = db.get(SystemSettings, part_key)
        if number >= len(chunks):
            if part is not None:
                db.delete(part)
            continue
        if part is None:
            part = SystemSettings(key=part_key)
            db.add(part)
        part.value = chunks[number]
        part.updated_at = now
    if root is None:
        root = SystemSettings(key=key)
        db.add(root)
    root.value = json.dumps({"chunk_count": len(chunks)})
    root.updated_at = now


def release_list(db: Session, client: httpx.Client) -> tuple[list[dict], dict]:
    cache = sync_status(db)
    cached_pages = cache.get("pages", {})
    pages = {}
    rows = []
    url = f"https://api.github.com/repos/{repository()}/releases?per_page=100"
    # Begrenzt auch bei boesartiger API-Antwort; keine unbeschraenkte Seitenschleife.
    for _ in range(10):
        cached = cached_pages.get(url, {})
        with tempfile.TemporaryFile() as output:
            status, headers = download(client, url, output, MAX_JSON, cached.get("etag"))
            if status == 304:
                if not cached:
                    raise ImportFailure("release_json_invalid")
                page = cached
            else:
                items = _json(output.read())
                if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                    raise ImportFailure("release_json_invalid")
                # Nur benoetigte oeffentliche Metadaten behalten, keine API-Rohpayloads.
                items = [{field: item.get(field) for field in ("tag_name", "prerelease", "draft", "assets")}
                         for item in items]
                for item in items:
                    if isinstance(item.get("assets"), list):
                        item["assets"] = [{k: asset.get(k) for k in ("name", "browser_download_url")}
                                          if isinstance(asset, dict) else asset for asset in item["assets"]]
                page = {"etag": headers.get("etag"), "items": items, "next": None}
                # httpx parse Link sans request; URL wird beim naechsten Download validiert.
                links = httpx.Response(200, headers=headers).links
                page["next"] = links.get("next", {}).get("url")
            pages[url] = page
            rows.extend(page["items"])
            url = page["next"]
            if not url:
                break
    return [row for row in rows if not row.get("draft")], pages


def _assets(row: dict) -> dict[str, str]:
    assets = row.get("assets")
    if not isinstance(assets, list) or len(assets) > 50:
        raise ImportFailure("release_json_invalid")
    result = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str) or not isinstance(
                asset.get("browser_download_url"), str) or asset["name"] in result:
            raise ImportFailure("release_json_invalid")
        result[asset["name"]] = asset["browser_download_url"]
    return result


def repository_key(db: Session, client: httpx.Client | None = None) -> str:
    """Nur Vorschau. Niemals hier oder beim Import einen Key speichern/aktivieren."""
    if client is None:
        with httpx.Client() as http:
            return repository_key(db, http)
    rows, _ = release_list(db, client)
    if not rows:
        raise ImportFailure("release_missing")
    try:
        pem = _bytes(client, _assets(rows[0])["release-key.pem"], MAX_KEY).decode("ascii")
        releases.parse_public_key(pem)
        return pem
    except KeyError as exc:
        raise ImportFailure("asset_missing") from exc
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, ImportFailure):
            raise
        raise ImportFailure("release_key_invalid") from exc


def _import_release(db: Session, client: httpx.Client, row: dict) -> str:
    assets = _assets(row)
    try:
        document = ReleaseDocument.model_validate(_json(_bytes(client, assets["release.json"], MAX_JSON)))
        if document.schema_version != 1 or (document.channel == "beta") != (row.get("prerelease") is True):
            raise ImportFailure("release_json_invalid")
        if document.key_id not in releases.public_keys(db):
            raise ImportFailure("key_not_pinned")
        metadata = []
        seen = set()
        for artifact in document.artifacts:
            expected_name = f"ec-infoscreen-agent_{document.version}_{artifact.arch}.deb"
            if artifact.arch in seen or artifact.package != expected_name:
                raise ImportFailure("release_json_invalid")
            if artifact.size > releases.MAX_PACKAGE:
                raise ImportFailure("size_limit")
            seen.add(artifact.arch)
            metadata.append(releases.ReleaseMetadata(version=document.version, channel=document.channel,
                key_id=document.key_id, **artifact.model_dump(exclude={"package"})))
    except (ValidationError, TypeError) as exc:
        raise ImportFailure("release_json_invalid") from exc
    except KeyError as exc:
        raise ImportFailure("asset_missing") from exc
    pending = [m for m in metadata if not db.query(InfoscreenRelease.id).filter_by(
        version=m.version, channel=m.channel, arch=m.arch).first()]
    if not pending:
        return "uebersprungen"
    try:
        script = _bytes(client, assets["install.sh"], releases.MAX_SCRIPT)
        for item in pending:
            with tempfile.TemporaryFile() as package:
                download(client, assets[f"ec-infoscreen-agent_{item.version}_{item.arch}.deb"],
                         package, releases.MAX_PACKAGE)
                saved = releases.store_release(db, item, package, script, document.install_sh.signature)
                write_audit(db, "infoscreen_agent.release_imported", entity_type="infoscreen_release",
                            entity_id=saved.id, payload={"repo": repository()})
        db.commit()
        return "importiert"
    except KeyError as exc:
        raise ImportFailure("asset_missing") from exc


def import_releases(db: Session, client: httpx.Client | None = None) -> list[dict]:
    if client is None:
        with httpx.Client() as http:
            return import_releases(db, http)
    results: list[dict] = []
    pages = sync_status(db).get("pages", {})
    try:
        if not releases.public_keys(db):
            raise ImportFailure("key_not_pinned")
        rows, pages = release_list(db, client)
        for row in rows:
            label = str(row.get("tag_name", "?"))[:100]
            try:
                state = _import_release(db, client, row)
                results.append({"release": label, "status": state, "code": ""})
            except (ValueError, IntegrityError) as exc:
                db.rollback()
                code = "release_duplicate" if isinstance(exc, IntegrityError) else str(exc)
                allowed = {"release_json_invalid", "asset_missing", "size_limit", "host_not_allowed", "redirect_limit",
                           "download_failed", "rate_limited", "key_not_pinned", "signature_invalid", "hash_mismatch",
                           "size_mismatch", "package_mismatch", "installer_invalid", "release_duplicate"}
                results.append({"release": label, "status": "abgelehnt",
                                "code": code if code in allowed else "release_json_invalid"})
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        results.append({"release": "GitHub", "status": "abgelehnt", "code": code if isinstance(exc, ImportFailure)
                        else "release_key_invalid"})
    now = datetime.now(UTC).replace(tzinfo=None)
    _save_status(db, {"pages": pages, "last_sync": now.isoformat(), "results": results}, now)
    db.commit()
    return results


def sync_once() -> float:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        last = sync_status(db).get("last_sync")
        if last:
            age = (datetime.now(UTC).replace(tzinfo=None) - datetime.fromisoformat(last)).total_seconds()
            if age < SYNC_S:
                return max(1, SYNC_S - age)
        results = import_releases(db)
        for result in results:
            if result["status"] == "abgelehnt":
                logger.warning("Agent-GitHub-Abgleich abgelehnt (code=%s)", result["code"])
    return SYNC_S


async def infoscreen_agent_github_sync_loop() -> None:
    # Ausschliesslich durch BackgroundRuntime unter dem vorhandenen Leader-Lock gestartet.
    if not settings.INFOSCREEN_AGENT_GITHUB_SYNC_ENABLED:
        return
    while True:
        delay: float = SYNC_S
        try:
            with iteration_watch(logger, "infoscreen_agent_github_sync_loop", SYNC_S):
                delay = await im_db_thread(sync_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Agent-GitHub-Abgleich fehlgeschlagen (error=%s)", type(exc).__name__)
        await asyncio.sleep(delay)
