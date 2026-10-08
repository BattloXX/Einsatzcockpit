# Straßensperren & Anfahrtsrouting (Entwicklung)

## Abschnittsresolver

`osm_street_service` lädt und cached das OSM-Straßennetz. Der reine
`road_closure_section_resolver` schneidet daraus den Abschnitt zwischen Kreuzungen oder Hausnummern; der dünne
Service-Entry-Point nutzt bei Netzausfall weiterhin die bisherige Hausnummern-/OSRM-Näherung.

## Architektur

Zentrale Regel: Routing ist ausschließlich Zusatzinformation. Der Alarmpfad wird **nie** angefasst. `incident_route_loop` entdeckt aktive Einsätze selbst und legt je Einsatz/Organisation eine Route an. Der Leader-Loop markiert sie bei geänderten Einsatzkoordinaten, Routing-Startpunkt oder Fingerprint sichtbarer aktiver Sperren erneut als veraltet.

Migration `0257` ergänzt drei `OrgSettings`-Spalten (`strassensperren_modul_aktiv`, Routing-Startpunkt mit Breite/Länge/Bezeichnung) und fünf Tabellen: `road_closure`, `road_closure_share`, `road_closure_change`, `incident_route` und `incident_road_closure`.

`road_closure_service` hält Persistenz, Versionsschutz, Historie, Freigaben und `visible_closures_q`; Freigaben gehen ausschließlich an `OrgPartner`. `road_closure_incident_service` wertet Routen aus und sendet bei tatsächlicher Änderung `incident_route_updated`.

## Geometrie und Relevanz

`road_closure_geo_service` verarbeitet GeoJSON-Punkte, -Linien und -Flächen metrisch.

| Konstante | Wert | Regel |
|-----------|------|-------|
| `ROUTE_BUFFER_M` | 25 m | Korridor um die Normalroute |
| `MIN_LINE_OVERLAP_M` | 30 m | Mindestüberlappung einer Linien-Sperre mit dem Korridor |
| `DESTINATION_RADIUS_M` | 150 m | Nähe zum Einsatzort |
| `NEARBY_RADIUS_M` | 500 m | nahe Route bzw. Ziel |
| `AVOID_BUFFER_M` | 10 m | Puffer um eine zu meidende Sperrfläche |

Die 30-m-Überlappung verhindert Fehlalarme durch quer zur Route liegende Straßen. Nur eine **Vollsperre** mit Status `ok` und einer für den Provider verwendbaren geprüften Geometrie wird als Umfahrung berücksichtigt.

## Provider

`app/services/einsatz_routing/` definiert `RoutingProvider`, `RouteResult`, `RouteResponse` und `RoutingError`. Ein neuer Provider implementiert `calculate_route()` sowie `name` und `supports_avoid_polygons`, wird in `get_provider()` ausgewählt und muss Routingfehler als `RoutingError` melden. ORS unterstützt `avoid_polygons`; OSRM liefert Alternativrouten, aus denen eine nicht kollidierende gewählt wird.

## Betrieb und Tests

`IncidentRoute` enthält Status, Lease, Retry-Zähler, Fingerprint, Normal- und Alternativroute. Der Worker verwendet einen 30-Sekunden-Lease, verarbeitet bis zu drei fällige Routen und versucht Fehler nach 30 und 120 Sekunden erneut, maximal dreimal.

Der wichtigste Regressionstest ist `tests/test_incident_route_loop.py`: Ist Routing nicht erreichbar, müssen Einsatzanlage und Alarm-Jobs dennoch sofort weiterlaufen.
