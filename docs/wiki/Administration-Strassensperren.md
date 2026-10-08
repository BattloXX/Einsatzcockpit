# Straßensperren & Anfahrtsrouting (Administration)

Anwender-Doku: [Straßensperren & Anfahrtsrouting](Anwender-Strassensperren).

## Aktivierung und Rechte

Das Modul ist zweistufig geschaltet: Systemadministratoren aktivieren es unter `/admin/settings` systemweit; danach kann der Org-Admin es in den Organisations-Einstellungen einschalten. Beide Schalter müssen aktiv sein.

| Berechtigung | Rechte |
|-------------|--------|
| Alle Benutzer der Organisation | Sperren lesen |
| `objekt_verwalter` | anlegen, ändern, deaktivieren und reaktivieren |
| `org_admin` | zusätzlich endgültig löschen und Organisationsschalter verwalten |
| Nachbarwehr mit Freigabe | nur lesen |

## Routing-Startpunkt

In den Organisations-Einstellungen wird die **Fahrzeugausfahrt** als Routing-Startpunkt über den Map-Picker hinterlegt (Koordinaten und Bezeichnung). Fehlt dieser Startpunkt, findet keine Routenprüfung statt; vorhandene Routendaten werden nicht weiter angezeigt.

## Routingdienst

Routing ist standardmäßig aus. Beim Aktivieren werden Einsatzkoordinaten an den gewählten Routingdienst übertragen; das ist vorab datenschutzrechtlich zu beurteilen.

| Variable | Standard | Zweck |
|----------|----------|-------|
| `EINSATZ_ROUTING_ENABLED` | `false` | Opt-in für die Routingberechnung |
| `EINSATZ_ROUTING_PROVIDER` | `ors` | `ors` (openrouteservice) oder `osrm` |
| `EINSATZ_ROUTING_API_URL` | leer | Eigene Dienst-URL; leer nutzt ORS-Standard bzw. bei OSRM `ROUTING_OSRM_URL` |
| `EINSATZ_ROUTING_API_KEY` | leer | API-Key für ORS am öffentlichen Standarddienst |
| `EINSATZ_ROUTING_PROFILE` | leer | ORS: `driving-car`; OSRM: `driving` |
| `EINSATZ_ROUTING_TIMEOUT_SECONDS` | `5` | HTTP-Timeout in Sekunden |

openrouteservice ist empfohlen: Es kann geprüfte Sperrflächen als zu meidende Flächen übergeben, auch an einem eigenen Server. OSRM dient als Alternative und kann nur aus Alternativrouten auswählen, keine Sperrfläche direkt meiden.

Die URL für die Sperren-Statusansicht auf einem Monitor wird über die Alarm-Infoscreen-Verwaltung bereitgestellt.

## Fehlersuche

Prüfe die Logger `einsatzleiter.einsatz_route`, `einsatzleiter.einsatz_routing`, `einsatzleiter.incident_route_loop` und `einsatzleiter.road_closure`.

| Routenstatus | Bedeutung |
|--------------|-----------|
| `pending` | wartet auf Berechnung |
| `ok` | berechnet, keine Sperre auf der Route |
| `affected` | berechnet, mindestens eine Sperre betrifft die Route |
| `error` | Routingdienst oder Berechnung fehlgeschlagen |
| `no_start` | kein Routing-Startpunkt gesetzt |
| `no_location` | Einsatz hat keine Koordinaten |
| `disabled` | Einsatz nicht aktiv oder Routing nicht verfügbar/aktiviert |

Nach Fehlern erfolgen höchstens drei Versuche: nach 30 Sekunden, dann nach 120 Sekunden; danach keine automatische Wiederholung. Kontrolliere außerdem System-/Org-Schalter, Startpunkt, Dienst-URL, API-Key und Timeout.

## OSM-Abschnittsermittlung

Die Abschnittsermittlung fragt das OSM-Straßennetz über Overpass ab – zuerst im Gemeindegebiet (Feld *Ort* der Sperre bzw. Ort der Organisation), sonst im Umkreis.

| Variable | Standard | Bedeutung |
|----------|----------|-----------|
| `STRASSEN_OVERPASS_URL` | `https://overpass-api.de/api/interpreter` | Overpass-Endpunkt |
| `STRASSEN_OVERPASS_TIMEOUT_SECONDS` | `20` | Timeout je Anfrage; bei Überlast (429/502/503/504, Timeout) wird einmal wiederholt |
| `STRASSEN_SUCHRADIUS_M` | `6000` | Umkreis um Ort/Organisation, falls das Gemeindegebiet nichts liefert |

Die öffentliche Overpass-Instanz ist zeitweise überlastet. Fällt sie aus, wird eine Näherung über Hausnummern versucht (Qualität *niedrig*). Die Ermittlung läuft nur beim Anlegen/Bearbeiten, nie im Alarmpfad.
## Öffentliche Tokens

Einzelansichten verwenden Tokens mit dem Präfix `rcd_` (Status `rcs_`, Infoscreen `rci_`). Tokens können widerrufen werden und optional ablaufen. Öffentlich sind nur freigegebene Felder der Sperre; `PUBLIC_BASE_URL` bestimmt die Basis der erzeugten Links.
