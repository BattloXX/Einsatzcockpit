"""Org-Auswahl fuer Systemadmins ohne eigene Organisation.

Seiten, die eine Organisation brauchen, zeigten einem organisationslosen
Systemadmin nur "Keine Organisation ausgewaehlt". Stattdessen wird die erste
Organisation vorgewaehlt; die Auswahl auf der Seite erlaubt den Wechsel.
"""

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import has_role
from app.models.master import FireDept


def erste_org_weiterleitung(
    request: Request, db: Session, user, org_id: int | None
) -> RedirectResponse | None:
    """303 auf dieselbe Seite mit ``org_id`` der ersten Organisation, sonst ``None``."""
    if org_id or getattr(user, "org_id", None) or not has_role(user, "system_admin"):
        return None
    erste = db.query(FireDept).order_by(FireDept.name).first()
    if erste is None:
        return None
    ziel = request.url.include_query_params(org_id=erste.id)
    # Relativ weiterleiten: hinter einem Reverse-Proxy ist Host/Schema der URL nicht verlaesslich.
    return RedirectResponse(f"{ziel.path}?{ziel.query}", status_code=303)

