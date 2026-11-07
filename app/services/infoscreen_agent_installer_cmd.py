"""Installationszeile ausschliesslich aus vertrauenswuerdiger Server-Konfiguration."""

from __future__ import annotations

from shlex import quote
from urllib.parse import urlsplit

from app.config import settings
from app.services.infoscreen_agent_releases import release_fingerprint


def installer_origin() -> str:
    server = settings.effective_public_base_url.rstrip("/")
    parts = urlsplit(server)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.path or parts.query or parts.fragment or "\\" in server
            or any(ord(char) < 32 or ord(char) == 127 for char in server)):
        raise ValueError("Installer braucht eine konfigurierte HTTPS-Serveradresse ohne Pfad")
    # urlsplit validiert den Port erst beim Zugriff.
    _ = parts.port
    return server


def installer_command(code: str, name: str | None = None, *, environment: bool = False) -> str:
    server = installer_origin()
    command = f"curl -fsSL {quote(server + '/infoscreen-agent/install.sh')} | sudo "
    if environment:
        command += f"EC_PAIRING_CODE={quote(code)} bash -s -- --server {quote(server)}"
    else:
        command += f"bash -s -- --server {quote(server)} --pairing-code {quote(code)}"
    fingerprint = release_fingerprint()
    if fingerprint:
        command += " --key-fingerprint " + quote(fingerprint)
    if name:
        command += " --name " + quote(name)
    return command
