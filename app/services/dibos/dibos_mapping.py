"""Abbildung bestaetigter DIBOS-Wachenstatus auf interne Werte."""

WACHE_STATUS_VALUES = ["einsatzbereit", "alarmiert", "übernommen", "ausgefahren", "am_einsatzort"]

_DIBOS_WACHE_STATUS_MAP = {
    "AL": "alarmiert",
    "UEB": "übernommen",
    "S2": "einsatzbereit",
    "S4": "ausgefahren",
    "S5": "am_einsatzort",
}


def map_wache_status(status_text: str | None) -> str | None:
    """Mappt nur im echten DIBOS-Katalog bestaetigte Wachenstatus."""
    if not status_text:
        return None
    return _DIBOS_WACHE_STATUS_MAP.get(status_text.strip().upper())
