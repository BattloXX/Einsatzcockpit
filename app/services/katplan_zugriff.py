"""Zentrale Rollen- und Modulpruefung fuer Katastrophenplaene."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.permissions import (
    KATPLAN_FREIGEBER_ROLES,
    KATPLAN_LESE_ROLLEN,
    KATPLAN_VERTRAULICH_ROLES,
    KATPLAN_VERWALTER_ROLES,
    has_role,
)
from app.db import get_db
from app.models.user import User
from app.services.katplan_flags import katplan_effective_enabled


def darf_lesen(user: User | None) -> bool:
    return has_role(user, *KATPLAN_LESE_ROLLEN)


def darf_bearbeiten(user: User | None) -> bool:
    return has_role(user, *KATPLAN_VERWALTER_ROLES)


def darf_freigeben(user: User | None) -> bool:
    return has_role(user, *KATPLAN_FREIGEBER_ROLES)


def sieht_vertraulich(user: User | None) -> bool:
    return has_role(user, *KATPLAN_VERTRAULICH_ROLES)


def sieht_arbeitsstand(user: User | None) -> bool:
    return darf_bearbeiten(user)


def _require_katplan(request: Request, db: Session, rollen: tuple[str, ...] | set[str]) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if not katplan_effective_enabled(user.org_id, db):
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    if not has_role(user, *rollen):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return user


def require_katplan_lesen(request: Request, db: Session = Depends(get_db)) -> User:
    return _require_katplan(request, db, KATPLAN_LESE_ROLLEN)


def require_katplan_bearbeiten(request: Request, db: Session = Depends(get_db)) -> User:
    return _require_katplan(request, db, KATPLAN_VERWALTER_ROLES)


def require_katplan_freigeben(request: Request, db: Session = Depends(get_db)) -> User:
    return _require_katplan(request, db, KATPLAN_FREIGEBER_ROLES)
