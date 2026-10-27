"""Nur die drei explizit importierten Produktionsaktionen sind verfügbar."""

from app.services.repair import actions  # noqa: F401
from app.services.repair.registry import ACTIONS

ACTIONS.seal()
