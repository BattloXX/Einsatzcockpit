"""Installationszeile ausschliesslich aus vertrauenswuerdiger Server-Konfiguration."""

from __future__ import annotations

from shlex import quote
from urllib.parse import urlsplit

from app.config import settings


def installer_command(code: str, name: str | None = None, *, environment: bool = False) -> str:
    server = settings.effective_public_base_url.rstrip("/")
    parts = urlsplit(server)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Ungueltige konfigurierte Server-URL")
    command = f"curl -fsSL {quote(server + '/infoscreen-agent/install.sh')} | sudo "
    if environment:
        command += f"EC_PAIRING_CODE={quote(code)} bash -s -- --server {quote(server)}"
    else:
        command += f"bash -s -- --server {quote(server)} --pairing-code {quote(code)}"
    if settings.INFOSCREEN_AGENT_KEY_FINGERPRINT:
        command += " --key-fingerprint " + quote(settings.INFOSCREEN_AGENT_KEY_FINGERPRINT)
    if name:
        command += " --name " + quote(name)
    return command
