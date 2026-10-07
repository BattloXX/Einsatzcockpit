"""Providerneutrales Routing für die Einsatz-Anfahrt."""
from app.services.einsatz_routing.base import (
    RouteResponse,
    RouteResult,
    RoutingError,
    RoutingProvider,
    choose_alternative,
)
from app.services.einsatz_routing.ors import OrsProvider
from app.services.einsatz_routing.osrm import OsrmProvider

__all__ = [
    "OrsProvider",
    "OsrmProvider",
    "RouteResponse",
    "RouteResult",
    "RoutingError",
    "RoutingProvider",
    "choose_alternative",
    "get_provider",
]


def get_provider() -> RoutingProvider:
    """Ermittelt den aktivierten Routingprovider aus den Einstellungen."""
    from app.config import settings

    if not settings.EINSATZ_ROUTING_ENABLED:
        raise RoutingError("disabled")
    provider = settings.EINSATZ_ROUTING_PROVIDER.lower().strip()
    if provider == "ors":
        return OrsProvider()
    if provider == "osrm":
        return OsrmProvider()
    raise RoutingError("disabled", f"Unbekannter Routing-Provider: {provider}")
