"""Signierte Release-Artefakte; keine Dateinamen oder Befehle aus CI-Eingaben."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import re
import secrets
import tarfile
from datetime import UTC, datetime, timedelta
from functools import cmp_to_key
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_api_key
from app.models.infoscreen_agent import (
    InfoscreenDevice,
    InfoscreenDisplay,
    InfoscreenRelease,
    InfoscreenReleaseKey,
    InfoscreenReleaseUploadToken,
)
from app.models.master import FireDept, OrgSettings
from app.services.infoscreen_aktivitaet import lade_aktivitaet

MAX_PACKAGE = 512 * 1024 * 1024
MAX_SCRIPT = 1024 * 1024
VERSION_PATTERN = r"[0-9][A-Za-z0-9.+:~\-]{0,99}"


def valid_version(value: str) -> str:
    if not re.fullmatch(VERSION_PATTERN, value):
        raise ValueError("version_invalid")
    epoch, sep, upstream = value.partition(":")
    if sep and not epoch.isdigit():
        raise ValueError("version_invalid")
    upstream = upstream if sep else value
    if not re.match(r"^[0-9]", upstream):
        raise ValueError("version_invalid")
    upstream, sep, revision = upstream.rpartition("-")
    if sep and not re.fullmatch(r"[A-Za-z0-9+.~]+", revision):
        raise ValueError("version_invalid")
    return value


class ReleaseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    channel: Literal["stable", "beta"]
    arch: Literal["amd64", "arm64"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: StrictInt = Field(gt=0, le=MAX_PACKAGE)
    min_version: str
    key_id: str = Field(min_length=1, max_length=100, pattern=r"^[\x21-\x7e]+$")
    signature: str = Field(min_length=1, max_length=100)

    _versions = field_validator("version", "min_version")(valid_version)


def canonical_manifest(metadata: ReleaseMetadata | InfoscreenRelease) -> bytes:
    return ("ec-infoscreen-agent\n"
            f"version={metadata.version}\nchannel={metadata.channel}\narch={metadata.arch}\n"
            f"sha256={metadata.sha256}\nsize={metadata.size}\nmin_version={metadata.min_version}").encode("ascii")


def parse_public_key(pem: str) -> Ed25519PublicKey:
    try:
        if not re.fullmatch(r"\s*-----BEGIN PUBLIC KEY-----\s+[A-Za-z0-9+/=\s]+-----END PUBLIC KEY-----\s*", pem):
            raise ValueError("release_key_invalid")
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("release_key_invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("release_key_invalid")
    return key


def public_keys(db: Session | None = None) -> dict[str, Ed25519PublicKey]:
    """Setting gewinnt bei gleicher ID; DB-Reihenfolge ist deterministisch (id)."""
    if db is None:
        from app.db import SessionLocal
        with SessionLocal() as session:
            return public_keys(session)
    keys = {row.key_id: parse_public_key(row.pem) for row in db.query(InfoscreenReleaseKey).filter_by(
        aktiv=True).order_by(InfoscreenReleaseKey.id).all()
        if row.key_id not in settings.INFOSCREEN_AGENT_RELEASE_PUBKEYS}
    keys.update({key_id: parse_public_key(pem) for key_id, pem in settings.INFOSCREEN_AGENT_RELEASE_PUBKEYS.items()})
    return keys


def fingerprint(key: Ed25519PublicKey) -> str:
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return "sha256:" + hashlib.sha256(der).hexdigest()


def release_fingerprint(db: Session | None = None) -> str:
    if settings.INFOSCREEN_AGENT_KEY_FINGERPRINT:
        return settings.INFOSCREEN_AGENT_KEY_FINGERPRINT
    if db is None:
        from app.db import SessionLocal
        with SessionLocal() as session:
            return release_fingerprint(session)
    try:
        keys = public_keys(db)
        # Ueberschattete DB-Keys sind kein wirksamer Vertrauensanker.
        for row in db.query(InfoscreenReleaseKey).filter_by(aktiv=True).order_by(InfoscreenReleaseKey.id).all():
            if fingerprint(keys[row.key_id]) == row.fingerprint:
                return row.fingerprint
        return next((fingerprint(parse_public_key(pem)) for pem in
                     settings.INFOSCREEN_AGENT_RELEASE_PUBKEYS.values()), "")
    except ValueError:
        return ""


def verify_signature(key_id: str, signature: str, content: bytes, db: Session | None = None) -> None:
    key = public_keys(db).get(key_id)
    if key is None:
        raise ValueError("release_key_unknown")
    try:
        decoded = base64.b64decode(signature, validate=True)
        if len(decoded) != 64:
            raise ValueError("signature_invalid")
        key.verify(decoded, content)
    except (InvalidSignature, binascii.Error, UnicodeError, ValueError) as exc:
        raise ValueError("signature_invalid") from exc


def create_upload_token(db: Session) -> str:
    token = "eciru_" + secrets.token_urlsafe(32)
    db.add(InfoscreenReleaseUploadToken(token_hash=hash_api_key(token)))
    return token


def resolve_upload_token(db: Session, token: str) -> InfoscreenReleaseUploadToken | None:
    if not token.startswith("eciru_") or len(token) > 128:
        return None
    return db.query(InfoscreenReleaseUploadToken).filter(
        InfoscreenReleaseUploadToken.token_hash == hash_api_key(token),
        InfoscreenReleaseUploadToken.revoked_at.is_(None)).first()


def _control_bytes(name: str, content: bytes) -> bytes:
    """Dekompression begrenzen, keine Archivpfade extrahieren."""
    import bz2
    import gzip
    import lzma
    import zlib
    from compression import zstd

    try:
        source = io.BytesIO(content)
        reader: io.BytesIO | gzip.GzipFile | lzma.LZMAFile | bz2.BZ2File | zstd.ZstdFile
        if name.endswith(".gz"):
            reader = gzip.GzipFile(fileobj=source)
        elif name.endswith(".xz"):
            reader = lzma.LZMAFile(source)
        elif name.endswith(".bz2"):
            reader = bz2.BZ2File(source)
        elif name.endswith(".zst"):
            reader = zstd.ZstdFile(source)
        elif name == "control.tar":
            reader = source
        else:
            raise ValueError("package_mismatch")
        with reader:
            data = reader.read(2 * 1024 * 1024 + 1)
    except (OSError, EOFError, lzma.LZMAError, zstd.ZstdError, zlib.error) as exc:
        raise ValueError("package_mismatch") from exc
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("package_mismatch")
    return data


def verify_package(file: BinaryIO, metadata: ReleaseMetadata) -> None:
    """ar und Debian-control lesen; niemals dpkg/maintainer scripts ausfuehren."""
    file.seek(0)
    try:
        if file.read(8) != b"!<arch>\n":
            raise ValueError("package_mismatch")
        names = set()
        control = None
        while header := file.read(60):
            if len(header) != 60 or header[58:] != b"`\n":
                raise ValueError("package_mismatch")
            name = header[:16].decode("ascii").strip().rstrip("/")
            length = int(header[48:58])
            if name in names or length < 0 or file.tell() + length > metadata.size:
                raise ValueError("package_mismatch")
            names.add(name)
            if name == "debian-binary":
                if length != 4 or file.read(length) != b"2.0\n":
                    raise ValueError("package_mismatch")
            elif name.startswith("control.tar"):
                if control is not None or length > 2 * 1024 * 1024:
                    raise ValueError("package_mismatch")
                control = _control_bytes(name, file.read(length))
            else:
                file.seek(length, 1)
            if length % 2:
                if file.read(1) != b"\n":
                    raise ValueError("package_mismatch")
        if control is None or "debian-binary" not in names or not any(n.startswith("data.tar") for n in names):
            raise ValueError("package_mismatch")
        with tarfile.open(fileobj=io.BytesIO(control), mode="r:") as archive:
            entries = [entry for entry in archive if entry.name in ("control", "./control")]
            if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 65536:
                raise ValueError("package_mismatch")
            stream = archive.extractfile(entries[0])
            assert stream is not None
            fields: dict[str, str] = {}
            for line in stream.read().decode("utf-8").splitlines():
                if line.startswith((" ", "\t")):
                    continue
                key, sep, value = line.partition(":")
                if sep:
                    if key in fields:
                        raise ValueError("package_mismatch")
                    fields[key] = value.strip()
            if any(fields.get(k) != v for k, v in {"Package": "ec-infoscreen-agent",
                                                     "Architecture": metadata.arch,
                                                     "Version": metadata.version}.items()):
                raise ValueError("package_mismatch")
    except (tarfile.TarError, OSError, EOFError, UnicodeError, ValueError) as exc:
        raise ValueError("package_mismatch") from exc
    finally:
        file.seek(0)


def store_release(db: Session, metadata: ReleaseMetadata, package: BinaryIO,
                  script: bytes, script_signature: str) -> InfoscreenRelease:
    verify_signature(metadata.key_id, metadata.signature, canonical_manifest(metadata), db)
    if not script or len(script) > MAX_SCRIPT:
        raise ValueError("installer_invalid")
    # Das Skript wird separat ueber seine unveraenderten Bytes signiert.
    verify_signature(metadata.key_id, script_signature, script, db)
    digest = hashlib.sha256()
    size = 0
    package.seek(0)
    while chunk := package.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_PACKAGE or size > metadata.size:
            raise ValueError("size_mismatch")
        digest.update(chunk)
    if size != metadata.size or digest.hexdigest() != metadata.sha256:
        raise ValueError("hash_mismatch")
    verify_package(package, metadata)
    if db.query(InfoscreenRelease.id).filter_by(version=metadata.version, channel=metadata.channel,
                                               arch=metadata.arch).first():
        raise ValueError("release_duplicate")
    root = Path(settings.INFOSCREEN_AGENT_RELEASE_STORAGE_DIR)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    artifact = uuid4().hex
    paths: list[Path] = []
    try:
        for suffix, stream in ((".deb", package), (".sh", io.BytesIO(script))):
            destination = root / (artifact + suffix)
            temporary = root / (artifact + suffix + ".tmp")
            paths.extend((temporary, destination))
            with temporary.open("xb") as output:
                os.chmod(temporary, 0o600)
                while chunk := stream.read(1024 * 1024):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        row = InfoscreenRelease(**metadata.model_dump(), rollout_percent=0, blocked=False,
                                storage_path=artifact + ".deb", installer_path=artifact + ".sh",
                                installer_sha256=hashlib.sha256(script).hexdigest(),
                                installer_signature=script_signature)
        db.add(row)
        db.flush()
        # Commit im Aufrufer; bei Rollback auch die bereits umbenannten Dateien entfernen.
        db.info.setdefault("isa_release_files", []).extend(paths)
        return row
    except BaseException:
        for path in paths:
            path.unlink(missing_ok=True)
        raise


def artifact_path(name: str | None) -> Path:
    # Auch korrupte DB-Eintraege duerfen die konfigurierte Ablage nicht verlassen.
    if not name or not re.fullmatch(r"[0-9a-f]{32}\.(deb|sh)", name):
        raise ValueError("artifact_missing")
    root = Path(settings.INFOSCREEN_AGENT_RELEASE_STORAGE_DIR).resolve()
    path = root / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
        raise ValueError("artifact_missing")
    return path


def _part_compare(left: str, right: str) -> int:
    """Debian verrevcmp: Tilde, Ende, Buchstaben, sonstige Zeichen, Ziffern."""
    def order(char: str) -> int:
        if char == "~":
            return -1
        if not char or char.isdigit():
            return 0
        return ord(char) if char.isalpha() else ord(char) + 256

    while left or right:
        while (left and not left[0].isdigit()) or (right and not right[0].isdigit()):
            a, b = left[:1], right[:1]
            if order(a) != order(b):
                return 1 if order(a) > order(b) else -1
            left, right = left[1:], right[1:]
        a_match, b_match = re.match(r"[0-9]*", left), re.match(r"[0-9]*", right)
        assert a_match is not None and b_match is not None
        a_digits, b_digits = a_match.group(), b_match.group()
        a, b = a_digits.lstrip("0"), b_digits.lstrip("0")
        left, right = left[len(a_digits):], right[len(b_digits):]
        if (len(a), a) != (len(b), b):
            return 1 if (len(a), a) > (len(b), b) else -1
    return 0


def version_compare(left: str, right: str) -> int:
    def parts(value: str) -> tuple[int, str, str]:
        epoch, sep, rest = value.partition(":")
        epoch_num = int(epoch) if sep and epoch.isdigit() else 0
        version = rest if sep and epoch.isdigit() else value
        upstream, sep, revision = version.rpartition("-")
        return epoch_num, upstream if sep else version, revision if sep else "0"
    a, b = parts(left), parts(right)
    return ((a[0] > b[0]) - (a[0] < b[0])) or _part_compare(a[1], b[1]) or _part_compare(a[2], b[2])


def rollout_bucket(device_id: int, version: str) -> int:
    return int(hashlib.sha256(f"{device_id}{version}".encode("ascii")).hexdigest(), 16) % 100


def eligible(row: InfoscreenRelease, device: InfoscreenDevice, *, initial: bool = False,
             current: str = "0") -> bool:
    if row.blocked or row.rollout_percent <= 0 or row.channel != device.channel:
        return False
    if initial:
        return True
    return (not device.update_locked
            and (not device.update_pinned_version or row.version == device.update_pinned_version)
            and rollout_bucket(device.id, row.version) < row.rollout_percent
            and version_compare(current, row.min_version or "0") >= 0
            and version_compare(row.version, current) > 0)


def select_release(db: Session, device: InfoscreenDevice, arch: str, *, initial: bool = False,
                   current: str = "0") -> InfoscreenRelease | None:
    rows = db.query(InfoscreenRelease).filter(InfoscreenRelease.arch == arch,
                                            InfoscreenRelease.channel == device.channel,
                                            InfoscreenRelease.key_id != "",
                                            InfoscreenRelease.min_version.is_not(None)).all()
    rows = [row for row in rows if eligible(row, device, initial=initial, current=current)]
    def compare(a: InfoscreenRelease, b: InfoscreenRelease) -> int:
        return version_compare(a.version, b.version)

    return max(rows, key=cmp_to_key(compare)) if rows else None


def manifest(row: InfoscreenRelease, *, update: bool = False, current: str | None = None) -> dict:
    data = {key: getattr(row, key) for key in
            ("version", "channel", "arch", "sha256", "size", "min_version", "key_id", "signature")}
    data["download_url"] = f"/api/v1/infoscreen-agent/download/artifact/{row.id}"
    if update:
        data["critical"] = row.critical
        if current is not None:
            from urllib.parse import urlencode

            data["download_url"] += "?" + urlencode({"current": current})
    return data


def update_policy(db: Session, device: InfoscreenDevice, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    org = db.query(FireDept).filter(FireDept.id == device.org_id).first()
    policy = db.query(OrgSettings).filter(OrgSettings.org_id == device.org_id).first()
    assert device.org_id is not None
    activity = lade_aktivitaet(db, device.org_id)
    delays = db.query(InfoscreenDisplay.nachlauf_min).filter(
        InfoscreenDisplay.org_id == device.org_id, InfoscreenDisplay.aktiv.is_(True)).all()
    delay = max((d[0] for d in delays), default=10)
    quiet = not (activity.gsl or activity.incident or
                 (activity.letztes_ende and now < activity.letztes_ende + timedelta(minutes=delay)))
    return {"update_allowed": quiet, "locked": device.update_locked,
            "pinned_version": device.update_pinned_version,
            "maintenance_window": {"start": policy.infoscreen_update_start if policy else "02:00",
                                   "end": policy.infoscreen_update_end if policy else "04:30",
                                   "timezone": (org.timezone if org else None) or settings.DEFAULT_TIMEZONE},
            "critical_allowed": bool(policy and policy.infoscreen_critical_allowed), "check_interval_h": 6}


def read_installer(row: InfoscreenRelease, db: Session | None = None) -> bytes:
    with artifact_path(row.installer_path).open("rb") as source:
        script = source.read(MAX_SCRIPT + 1)
    if not script or len(script) > MAX_SCRIPT or hashlib.sha256(script).hexdigest() != row.installer_sha256:
        raise ValueError("installer_invalid")
    verify_signature(row.key_id, row.installer_signature or "", script, db)
    return script


def latest_installer(db: Session) -> InfoscreenRelease | None:
    for row in db.query(InfoscreenRelease).filter(InfoscreenRelease.blocked.is_(False),
            InfoscreenRelease.installer_path.is_not(None)).order_by(InfoscreenRelease.id.desc()).all():
        try:
            read_installer(row, db)
            return row
        except (OSError, ValueError):
            continue
    return None


def installer_enabled(db: Session) -> bool:
    if settings.INFOSCREEN_AGENT_INSTALLER_ENABLED:
        return True
    keys = public_keys(db)
    return any(fingerprint(keys[row.key_id]) == row.fingerprint for row in
               db.query(InfoscreenReleaseKey).filter_by(aktiv=True).all())


def installer_ready(db: Session) -> bool:
    if not installer_enabled(db) or not latest_installer(db):
        return False
    for row in db.query(InfoscreenRelease).filter(InfoscreenRelease.channel == "stable",
            InfoscreenRelease.blocked.is_(False), InfoscreenRelease.rollout_percent > 0).all():
        try:
            verify_signature(row.key_id, row.signature, canonical_manifest(row), db)
            if artifact_path(row.storage_path).stat().st_size == row.size:
                return True
        except (ValueError, OSError):
            continue
    return False


# Ein DB-Fehler darf keine verwaisten Downloads hinterlassen.


@event.listens_for(Session, "after_rollback")
def _cleanup_files(db: Session) -> None:
    for path in db.info.pop("isa_release_files", []):
        path.unlink(missing_ok=True)


@event.listens_for(Session, "after_commit")
def _forget_files(db: Session) -> None:
    db.info.pop("isa_release_files", None)
