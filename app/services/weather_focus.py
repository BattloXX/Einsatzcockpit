"""Schwerpunkt-Logik: geografischer Bezugspunkt einer Großschadenslage.

Der Schwerpunkt ist der Ort mit den meisten aktiven Einsatzstellen. Bei
Gleichstand gewinnt die Gruppe mit der höchsten Dringlichkeit (niedrigste
SitePriority-Summe), danach die jüngste Gruppe.
"""
import statistics
from dataclasses import dataclass

from app.models.major_incident import IncidentSite, MajorIncident, SitePhase


_DONE_PHASES = frozenset({SitePhase.erledigt, SitePhase.abgebrochen})


@dataclass
class WeatherFocus:
    lat: float
    lng: float
    label: str       # e.g. "Wolfurt (3 Einsatzstellen)"
    site_count: int


def resolve_weather_focus(lage: MajorIncident) -> WeatherFocus | None:
    """Returns the centroid of the busiest active-site location group.

    Returns None if no sites have coordinates — caller should use org fallback.
    """
    active = [
        s for s in lage.sites
        if s.phase not in _DONE_PHASES and s.lat is not None and s.lng is not None
    ]
    if not active:
        return None

    # Group by normalised ort
    groups: dict[str, list[IncidentSite]] = {}
    for site in active:
        key = (site.ort or "").strip().lower()
        groups.setdefault(key, []).append(site)

    def _score(items: list[IncidentSite]) -> tuple[int, int, float]:
        prio_sum = sum(int(s.priority) if s.priority is not None else 3 for s in items)
        newest = max((s.created_at.timestamp() for s in items), default=0.0)
        # sort: most sites > most urgent (lowest prio_sum) > newest
        return (len(items), -prio_sum, newest)

    best = max(groups.values(), key=_score)
    ort_name = best[0].ort or "unbekannt"
    return WeatherFocus(
        lat=statistics.mean(s.lat for s in best),
        lng=statistics.mean(s.lng for s in best),
        label=f"{ort_name} ({len(best)} Einsatzstelle{'n' if len(best) != 1 else ''})",
        site_count=len(best),
    )
