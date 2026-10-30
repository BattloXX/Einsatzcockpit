"""Diagnose-Registry; Import registriert ausschliesslich lesende Checks."""
from app.services.diagnose import (  # noqa: F401
    datenbank,
    deploy,
    einsatz,
    gateway,
    katplan,
    kontakte,
    objekte,
    osm,
    redis,
    suche,
    worker,
)
from app.services.diagnose.registry import CHECKS

CHECKS.seal()
