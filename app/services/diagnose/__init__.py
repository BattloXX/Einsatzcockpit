"""Diagnose-Registry; Import registriert ausschliesslich lesende Checks."""
from app.services.diagnose import (  # noqa: F401
    app_geraete,
    datenbank,
    deploy,
    einsatz,
    gateway,
    katplan,
    kontakte,
    objekte,
    osm,
    redis,
    standort,
    suche,
    worker,
)
from app.services.diagnose.registry import CHECKS

CHECKS.seal()
