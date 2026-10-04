"""Gemeinsame Redirect-Helfer."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse


def login_redirect(request: Request) -> RedirectResponse:
    """Leitet nicht angemeldete Browser-Nutzer zum Login (mit Rücksprung-Ziel)."""
    path = request.url.path
    if request.url.query:
        path += "?" + request.url.query
    return RedirectResponse(f"/login?next={quote(path, safe='')}", status_code=302)
