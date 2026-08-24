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
| **Nowcast (15 min)** | Aktuell gemessene Werte: Temperatur, Wind, Böen, Niederschlag, Sicht |
| **Ist-Werte** | Aktuelle Messwerte der nächsten Wetterstation |
| **Vorhersage** | +6h / +12h / +24h (Temperatur, Niederschlag, Wind) |
| **Unwetterwarnungen** | Amtliche ZAMG-Warnungen für den Einsatzort |
| **Szenarien** | Sturm- und Waldbrand-Indikatoren (farblich hervorgehoben) |

Das Wetter-Panel zeigt Daten für den **Einsatzort** (Adresse / Koordinaten des Einsatzes). Ist keine Adresse hinterlegt, wird der Org-Standort als Fallback verwendet.

---

## Globale Wetter-Seite

**URL:** `/wetter`

Die globale Wetter-Seite zeigt Wetterdaten für den **Org-Standort** (konfiguriert in Einstellungen → Organisation → Standort). Sie eignet sich als Überblick ohne aktiven Einsatz.

---

## Wetter in der Großschadenslage

Im GSL-Board gibt es ein dediziertes **Wetter-Tab** mit denselben Daten wie das Einsatz-Board-Panel, aber für den Standort der Großschadenslage.

Zusätzlich: **Radar-Overlay** auf der Lagekarte (Niederschlagsradar via RainViewer, letzte 2h und Nowcast).

---

## Szenario-Indikatoren

Zwei Szenarien werden automatisch berechnet und farblich hervorgehoben:

| Szenario | Kriterien | Farbe |
|----------|-----------|-------|
| **Sturm** | Windböen ≥ 60 km/h oder Warnstufe „Sturm" | Orange / Rot |
| **Waldbrand** | Hohe Temperatur + geringe Luftfeuchtigkeit + Wind + wenig Niederschlag | Rot |

Ist ein Szenario aktiv, erscheint ein auffälliges Banner im Wetter-Panel.

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

- Wetterdaten werden serverseitig gecacht (15–30 min je nach Endpunkt)
- Es werden **keine personenbezogenen Daten** an Wetterdienste übermittelt — nur Koordinaten des Einsatzorts
- Die ZAMG-Daten (GeoSphere Austria) stehen unter **CC BY 4.0**

---

## Radar-Overlay auf der Lagekarte

In der Großschadenslage-Lagekarte kann das **Niederschlagsradar-Overlay** (RainViewer) über die Karten-Steuerung ein-/ausgeblendet werden:

- Letzte 2 Stunden (animiert rückwärts)
- Aktueller Nowcast
- Farbskala: blau (leicht) → rot (stark)

Hinweis: Das Radar-Overlay benötigt eine aktive Internetverbindung und ist nicht für den Offline-Betrieb verfügbar.
