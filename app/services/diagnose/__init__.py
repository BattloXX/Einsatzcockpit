"""Diagnose-Registry; Import registriert ausschliesslich lesende Checks."""
from app.services.diagnose import (  # noqa: F401
    datenbank,
    einsatz,
    gateway,
    katplan,
    kontakte,
    objekte,
    redis,
    suche,
    worker,
)
from app.services.diagnose.registry import CHECKS

CHECKS.seal()
