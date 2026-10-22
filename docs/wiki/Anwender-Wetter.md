# Wetter-Integration

← [Zurück zur Startseite](Home)

Das Wetter-Modul zeigt Echtzeit-Wetterdaten, Vorhersagen und amtliche Unwetterwarnungen direkt im Tool an — integriert in Einsatz-Board und Großschadenslage sowie als eigene Seite.

---

## Aktivierung

Das Wetter-Modul ist systemweit standardmäßig aktiv. Jede Organisation kann es in den **Einstellungen → Organisation** individuell deaktivieren.

---

## Lokale Wetterstation

Orgs mit einer konfigurierten **Davis Vantage Pro 2 Plus / Meteobridge**-Station sehen im Wetter-Panel zusätzlich eine **Lokale Wetterstation**-Karte:

| Element | Beschreibung |
|---------|-------------|
| **Live / Offline** | Grüner Punkt: letzter Push < 15 min — Grauer Punkt: kein aktueller Empfang |
| **Letzter Empfang** | Zeitstempel des letzten Pushes, z.B. „vor 3 min" |
| **Messwerte** | Temperatur, Luftfeuchtigkeit, Wind + Böen + Windrichtung, Luftdruck, Regenrate, Taupunkt, Solar, UV — jeweils nur wenn übermittelt |
| **24-h-Sparkline** | Miniplot der letzten 24 Stunden: Temperatur (orange) und Wind (blau); erscheint sobald genügend Verlauf vorhanden |

Die lokale Station erscheint **zuerst** im Wetter-Panel — noch vor den externen Wetterdiensten.

Wenn die Station **online** ist, werden ihre Messwerte automatisch für die **Szenario-Analyse** (Sturm, Waldbrand) herangezogen statt des NWP-Modellwertes. Lokale Böen aus dem Garten der Feuerwehr sind verlässlicher als ein Gitterpunkt-Modell.

> Administration der Wetterstation: [Admin → Lokale Wetterstation](Administration-Wetterstation)

---

## Einsatz-Board (Wetter-Panel)

Im laufenden Einsatz erscheint rechts im Board ein **Wetter-Panel** mit:

| Bereich | Inhalt |
|---------|--------|
| **Nowcast (15 min)** | Modellvorhersage für Niederschlag in den nächsten rund 3 Stunden |
| **Ist-Werte** | Aktuelle Messwerte der nächsten Wetterstation |
| **Kurzprognose** | Niederschlagssumme und Böenmaximum für +3/+6 h; GSL zusätzlich +12 h |
| **Unwetterwarnungen** | Amtliche ZAMG-Warnungen für den Einsatzort |
| **Interne Hinweise** | Mögliche Gefahren aus Kennzahlen; keine amtlichen Warnungen |

Das Wetter-Panel zeigt Daten für den **Einsatzort** (Adresse / Koordinaten des Einsatzes). Ist keine Adresse hinterlegt, wird der Org-Standort als Fallback verwendet.

---

## Globale Wetter-Seite

**URL:** `/wetter`

Die globale Wetter-Seite zeigt Wetterdaten für den **Org-Standort** (konfiguriert in Einstellungen → Organisation → Standort). Sie eignet sich als Überblick ohne aktiven Einsatz.

Die Abschnitte **Aktuell**, **Kurzfristig** (+1/+3/+6 h), **Prognose** (+12/+24 h
und Tagesprognose) und **Warnungen** unterscheiden Messungen, Modellvorhersagen
und amtliche Warninformationen. Regenwerte sind Fenstersummen in mm, Böen sind
Maxima des jeweiligen Fensters in m/s und km/h. Spitzenzeiten erscheinen in der
Zeitzone der Organisation (einschließlich Sommer-/Winterzeit).

Der **Datenstand** nennt Quelle, Modell bzw. Station, Alter in Minuten und Frische.
**Ersatzquelle** bedeutet, dass der bevorzugte Anbieter ausgefallen ist. Ein Strich
steht für fehlende Werte; **0.0 mm** bedeutet eine vorhandene Prognose ohne Regen.
Unvollständige oder veraltete Fenster erlauben keine vollständige Bewertung.

Der Infoscreen zeigt den Kurzfristblock nur, wenn alle drei Fenster vollständig
vorliegen. Seine Frost-/Glättehinweise stammen aus der gemeinsamen Kennzahlenlogik.
Die öffentlichen JSON-Endpunkte ergänzen `prognose_kurz`, `datenstand`,
`interne_hinweise`, `frost` und `glaette`; bisherige Felder bleiben erhalten.
Die Board-Kopfzeile übernimmt die Kurzprognose aus dem zuletzt geladenen Panel
(für höchstens fünf Minuten); sie ruft dafür keine weiteren Prognosen ab.


---

## Wetter in der Großschadenslage

Im GSL-Board gibt es ein dediziertes **Wetter-Tab** mit denselben Daten wie das Einsatz-Board-Panel, aber für den Standort der Großschadenslage.

Zusätzlich: **Radar-Overlay** auf der Lagekarte (Niederschlagsradar via RainViewer, letzte 2h und Nowcast).

---

## Szenario-Indikatoren

Interne Hinweise zu Starkregen, Sturm, Schnee, Glätte und Gewitter erscheinen als
„mögliche Gefahr“, wenn die erforderlichen Daten vollständig und ausreichend frisch
sind. Fehlende Messfenster oder Modellwerte werden als **Daten unvollständig**
gekennzeichnet; daraus folgt keine Entwarnung. Die Hinweise sind **keine amtlichen
Warnungen** und lösen keinen zusätzlichen SMS-/Teams-Versand aus. Für Hochwasser
sind Niederschläge nur Kontext; Pegel und amtliche Informationen bleiben maßgeblich.

---

## Datenquellen

| Priorität | Quelle | Beschreibung |
|-----------|--------|-------------|
| 1 | **Kachelmann Plus-API** | Beste Auflösung — erfordert kostenpflichtigen API-Key |
| 2 | **GeoSphere Austria / ZAMG** | CC BY 4.0, amtliche österreichische Messdaten + Warnungen |
| 3 | **Open-Meteo** | Kostenloses Fallback — automatisch wenn Primärquelle nicht erreichbar |
| Radar | **RainViewer** | Niederschlagsradar weltweit, kein API-Key nötig |

Die Quelle wird in der Systemadmin-Konsole konfiguriert (API-Keys in den System-Einstellungen).

---

## Datenschutz & Caching

- Wetterdaten werden serverseitig gecacht: aktuell und Nowcast 5 Minuten,
  Prognose 30 Minuten, Tagesprognose 60 Minuten, Warnungen 5 Minuten.
  Konfiguration: `WEATHER_CACHE_TTL_CURRENT`, `WEATHER_CACHE_TTL_NOWCAST`,
  `WEATHER_CACHE_TTL_NWP`, `WEATHER_DAILY_TTL` (Env-Alias `WEATHER_CACHE_TTL_DAILY`),
  `WEATHER_CACHE_TTL_WARN`. Bestehende Namen und Standardwerte bleiben gültig.
- Mit `REDIS_URL` teilen Worker einen JSON-Cache und Sperren (`wx:`-Präfix).
  Ohne Redis oder bei Redis-Fehlern läuft der lokale Cache weiter; Redis-Ausfälle
  werden höchstens einmal pro Minute protokolliert. Kachelmann-Ergebnisse sind
  nach Organisation getrennt. API-Schlüssel werden nicht im Wettercache gespeichert.
  Der ursprüngliche Abrufzeitpunkt bleibt erhalten, damit die Frische korrekt angezeigt wird.
- `WEATHER_HTTP_RETRIES=1` erlaubt einen zusätzlichen Versuch (maximal 2),
  nur bei Timeout, Verbindungsfehlern und HTTP 5xx. HTTP 4xx einschließlich 429
  werden nicht wiederholt. `WEATHER_RETRY_BACKOFF_S=0.5` begrenzt die Pause
  (maximal 2 Sekunden), `WEATHER_HTTP_TIMEOUT=8` jeden Versuch.
  Pro HTTP-Abfrage gilt zusätzlich ein Gesamtbudget aus Versuchen und Pausen.
- `WEATHER_LOCK_TTL_S=120` begrenzt Sperrdauer und Wartezeit zwischen Workern.
  Das Abrufbudget liegt bei 90 % der Sperrdauer. Bei erschöpftem Budget werden
  keine weiteren Provider gestartet; Daten können dann vorübergehend fehlen.
  Bei einer Redis-Störung ist die Deduplizierung nur innerhalb eines Workers möglich.
- `weather_health.provider_health()` liefert letzte Erfolge/Fehler, Latenzen und Zähler.
  Die Diagnose belegt nur vier Provider-Einträge im Speicher; gemeinsame Redis-Zähler
  verfallen nach 24 Stunden ohne Aktualisierung. Es entstehen keine Datenbanktabellen.
- Es werden **keine personenbezogenen Daten** an Wetterdienste übermittelt — nur Koordinaten des Einsatzorts
- Die ZAMG-Daten (GeoSphere Austria) stehen unter **CC BY 4.0**

---

## Radar-Overlay auf der Lagekarte

In der Großschadenslage-Lagekarte kann das **Niederschlagsradar-Overlay** (RainViewer) über die Karten-Steuerung ein-/ausgeblendet werden:

- Letzte 2 Stunden (animiert rückwärts)
- Aktueller Nowcast
- Farbskala: blau (leicht) → rot (stark)

Hinweis: Das Radar-Overlay benötigt eine aktive Internetverbindung und ist nicht für den Offline-Betrieb verfügbar.
