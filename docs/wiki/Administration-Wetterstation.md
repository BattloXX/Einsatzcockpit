# Lokale Wetterstation

← [Zurück zur Startseite](Home)

> URL: `/admin/settings#wetterstation`
> Zugänglich für: `org_admin`, `admin`, `system_admin`

Das Wetterstation-Modul verbindet eine lokale **Davis Vantage Pro 2 Plus** (oder kompatible Station) über einen **Meteobridge PRO RED** mit dem System. Da der Server die Station im FW-Haus-Netz nicht direkt erreichen kann, **pusht Meteobridge die Daten aktiv** per HTTPS-Aufruf.

---

## Voraussetzungen

| Komponente | Hinweis |
|-----------|---------|
| **Davis Vantage Pro 2 Plus** | Wetterkonsole mit ISS (kabelloses Außensensoren-Set) |
| **Meteobridge PRO RED** | Bridge-Gerät; Firmware ≥ 5.x empfohlen |
| **HTTPS nach außen** | Meteobridge muss einsatzcockpit.com auf Port 443 erreichen — nur **ausgehend** nötig, kein Inbound-Port nötig |

---

## Station anlegen

1. **Admin → Einstellungen → Wetterstation** aufrufen
2. **„Station hinzufügen"** — Name eingeben (z.B. „FW-Haus Wolfurt"), optionale GPS-Koordinaten
3. Nach dem Speichern erscheint der **einmalige Push-Token** (`wxst_...`) sowie die fertige **Meteobridge-URL**

> Der Token wird **nur einmalig** angezeigt. Sofort in Meteobridge eintragen, dann schließen.

---

## Meteobridge konfigurieren

In der Meteobridge-Weboberfläche unter **Services → Custom Push**:

| Feld | Wert |
|------|------|
| **URL** | `https://einsatzcockpit.com/api/v1/weather/ingest?token=wxst_...&temp=[th0temp-act]&hum=[th0hum-act]&wind=[wind0avgwind-act]&gust=[wind0wind-act]&dir=[wind0dir-act]&press=[thb0seapress-act]&rainrate=[rain0rate-act]&rainday=[rain0total-act]&dew=[th0dew-act]&solar=[sol0rad-act]&uv=[uv0index-act]` |
| **Methode** | GET |
| **Intervall** | 5 Minuten (300 s) |
| **Aktiv** | Ja |

Die `[...]`-Platzhalter sind Meteobridge-Variablen — einfach die URL so eintragen, Meteobridge ersetzt sie automatisch.

---

## Token rotieren

Über **„Token neu generieren"** kann der Ingest-Token jederzeit widerrufen und ein neuer erzeugt werden. Den alten Token in Meteobridge sofort durch den neuen ersetzen.

---

## Station entfernen

**„Entfernen"** löscht die Station und den Token. Historische Zeitreihen in der Wetter-Datenbank werden durch den nächsten nächtlichen Retention-Lauf bereinigt.

---

## Anzeige im Tool

Nach dem ersten erfolgreichen Push erscheint die Station im Wetter-Panel:

| Element | Beschreibung |
|---------|-------------|
| **Live-Chip** | Grüner Punkt + „Live" — erscheint wenn letzter Push < 15 min |
| **Offline-Chip** | Grauer Punkt + „Offline" — wenn kein Push seit > 15 min |
| **Letzter Empfang** | Zeitstempel, z.B. „vor 4 min" |
| **Messwerte** | Temperatur, Luftfeuchtigkeit, Wind/Böen/Richtung, Luftdruck, Regen, Taupunkt, Solar, UV |
| **24-h-Sparkline** | Temperatur (orange) und Wind (blau) als SVG-Miniplot; erscheint wenn genug Verlauf vorhanden |

Die lokalen Messwerte werden automatisch für die **Szenario-Analyse** (Sturm, Waldbrand) herangezogen — Vorrang vor dem NWP-Modellwert.

Details: [Wetter-Integration (Anwender)](Anwender-Wetter)

---

## Datenbankarchitektur

Die lokale Wetterstation nutzt **zwei separate Datenbanken**:

| Datenbank | Inhalt | Warum getrennt |
|-----------|--------|----------------|
| **Haupt-DB** (`einsatzleiter`) | `weather_station`-Tabelle: ein denormalisierter Snapshot-Datensatz je Station (aktuellste Messwerte, `last_*`-Felder) | Schneller Anzeigepfad; Einsatz hat Vorrang |
| **Wetter-DB** (`einsatzleiter_weather`) | `weather_reading`-Tabelle: vollständige Zeitreihe je Push | Kein Bloat der operativen DB; eigener Connection-Pool |

Die Zeitreihe wird täglich um **03:30 Uhr (Europe/Vienna)** automatisch bereinigt — Einträge älter als `WEATHER_READING_RETENTION_DAYS` (Standard: 365 Tage) werden gelöscht.

---

## .env-Konfiguration

```dotenv
# Separate DB für die Zeitreihe. Leer = Feature deaktiviert.
WEATHER_DATABASE_URL=mysql+pymysql://einsatzleiter:passwort@127.0.0.1:3306/einsatzleiter_weather

# Push-Endpoint global aktiv/inaktiv
WEATHER_STATION_INGEST_ENABLED=true

# Aufbewahrungsdauer historischer Messwerte (Tage)
WEATHER_READING_RETENTION_DAYS=365

# Mindestabstand zwischen akzeptierten Pushes je Station (Sekunden)
WEATHER_INGEST_MIN_INTERVAL_S=60
```

---

## Alembic-Migration

Beim Deployment einmalig ausführen:

```bash
alembic upgrade head   # legt weather_station in der Haupt-DB an
```

Die `weather_reading`-Zeitreihe wird beim ersten App-Start automatisch per `create_all` in der Wetter-DB angelegt — **kein Alembic nötig**.

---

## Multi-Tenancy

Jede Organisation pflegt ihre eigene Station — keine Org sieht Stationen oder Messwerte einer anderen Org. Der Ingest-Endpoint identifiziert die Station anhand des `wxst_`-Tokens (SHA-256-Hash in der DB) und schreibt ausschließlich auf die zugehörige Station.
