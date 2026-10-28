# Konzept: Lokale Wetterstation (Davis Vantage Pro 2 Plus + Meteobridge) → Einsatzcockpit

**Stand:** 2026-06-23
**Status:** Entwurf / Planung
**Ziel-Org:** FF Wolfurt (Daten ausschließlich für die eigene ORG, nicht org-übergreifend)

## 1. Ausgangslage

- Hardware im FW-Haus: **Davis Vantage Pro 2 Plus (wireless, mit Belüftung)** + Konsole +
  **Meteobridge PRO RED**. Die Meteobridge empfängt die ISS-Daten direkt per RF und ist
  für Datenspeicherung und Internet-Transfer zuständig.
- **Einsatzcockpit kann die Station NICHT direkt erreichen** (kein eingehender Zugriff
  ins FW-Haus-Netz). Folglich muss die **Meteobridge die Daten aktiv an Einsatzcockpit
  pushen** (Outbound von der Meteobridge → Cloud).
- Es existiert bereits ein ausgereiftes Wetter-Subsystem:
  - `app/services/weather_service.py` (GeoSphere/ZAMG, Open-Meteo, Kachelmann, Nowcast,
    NWP-Forecast, ZAMG-Warnungen, Szenario-Analyse Sturm/Waldbrand/Starkregen/Schnee/Glatteis)
  - `app/services/abfluss_service.py` (Pegel/Abfluss je Org – **Vorbild-Pattern** für
    Stationsdaten je Org: `_store[org_id][station]`, Ring-Buffer, TTL, Sparkline)
  - `/wetter`-Seite + Partial `incident_major/_weather_panel.html` (Ist-Stand + Nowcast +
    Forecast + Warnungen + Pegel-Views; HTMX-Auto-Refresh alle 5 min)
  - Org-Schalter `OrgSettings.weather_enabled`; Org-Fallback-Koordinaten `FireDept.fallback_lat/lng`

## 2. Anforderungen (aus Auftrag)

1. **Org-Isolation:** Stationsdaten nur in der eigenen ORG sichtbar – keine anderen Orgs.
2. **DB nicht aufblähen:** Historische Zeitreihe darf die operative DB nicht belasten;
   ggf. **separate DB** für Wetterdaten; **historische Werte regelmäßig löschen** (Retention).
3. **Darstellung:** Auf der Wetter-Seite den **Ist-Stand der eigenen Station** anzeigen.
4. **Priorität Einsatz vor Wetter:** Einsatzrelevante Funktionen haben Vorrang – die
   Wetter-Ingestion/-Retention darf den operativen Betrieb (DB-Pool, Antwortzeiten) nie bremsen.

## 3. Architektur-Überblick

```
  Davis ISS ──RF──> Meteobridge PRO RED ──HTTPS push (alle 1–5 min)──>
       POST https://Einsatzcockpit/api/v1/weather/ingest?token=<STATION_TOKEN>
                       │
                       ▼
        ┌─────────────────────────────────────────────────────────┐
        │ Ingest-Endpoint (FastAPI, leichtgewichtig, rate-limited) │
        │  1. token → org_id + station (hash-Lookup, fail-closed)  │
        │  2. Werte validieren (Plausibilitätsgrenzen)             │
        │  3. Snapshot upsert  → HAUPT-DB  (1 Zeile/Station)        │  ← Ist-Stand (hot path)
        │  4. Sample insert    → WETTER-DB (Zeitreihe)             │  ← Historie (cold path)
        │  5. 204 No Content (schnell)                             │
        └─────────────────────────────────────────────────────────┘
                       │                                  │
        Haupt-DB: weather_station (tenant-scoped)   Wetter-DB: weather_reading
        - Config + LETZTER Messwert (denormalisiert) - Zeitreihe + Retention-Cron (Löschen)
                       │
                       ▼
            /wetter-Seite liest NUR den Snapshot der Haupt-DB
            → kein Zugriff auf die große Zeitreihen-Tabelle im Anzeige-Pfad
```

**Kerngedanke gegen DB-Bloat & für Einsatz-Vorrang:** Trennung von **Ist-Stand** (eine
denormalisierte Zeile pro Station in der Haupt-DB, blitzschnell, indexiert) und **Historie**
(Zeitreihe in separater DB mit eigenem Connection-Pool + Auto-Retention). Der Anzeige- und
Einsatz-Pfad berührt die große Tabelle nie.

## 4. Datenmodell

### 4.1 `weather_station` — Haupt-DB, tenant-scoped (`TenantScoped`-Mixin)

Konfiguration **und** denormalisierter letzter Messwert (für sofortige Ist-Stand-Anzeige):

| Spalte | Typ | Zweck |
|---|---|---|
| `id` | PK | |
| `org_id` | FK fire_dept (via TenantScoped) | **Org-Isolation – Fail-Closed-Filter greift automatisch** |
| `name` | str | z. B. „FW-Haus Wolfurt" |
| `lat`, `lng` | float | Standort der Station (Karte/Anzeige) |
| `quelle` | str | „davis_meteobridge" |
| `ingest_token_hash` | str(64) | gehashter Push-Token (wie `hash_api_key`) |
| `active` | bool | Station aktiv |
| `last_seen_at` | datetime | letzter erfolgreicher Push (Online/Offline-Ampel) |
| `last_*` (Snapshot) | float/datetime | `last_temp_c`, `last_hum_pct`, `last_wind_ms`, `last_gust_ms`, `last_wind_dir_deg`, `last_pressure_hpa`, `last_rain_rate_mmh`, `last_rain_day_mm`, `last_dewpoint_c`, `last_solar_wm2`, `last_uv`, `last_measured_at` |

→ Migration als nächste Alembic-Revision (`0097_*`). Tabelle in
`app/core/tenant.py::_TENANT_TABLE_NAMES` aufnehmen ⇒ automatische Org-Scoping-Garantie.

### 4.2 `weather_reading` — Wetter-DB (separat), Zeitreihe

| Spalte | Typ |
|---|---|
| `id` | PK |
| `org_id` | int (indexiert, **kein** Tenant-Listener – separate DB/Base) |
| `station_id` | int |
| `ts` | datetime (indexiert) |
| Messwerte | temp_c, hum_pct, wind_ms, gust_ms, wind_dir_deg, pressure_hpa, rain_rate_mmh, rain_day_mm, dewpoint_c, solar_wm2, uv |

Index `(org_id, station_id, ts)` für Verlauf-Abfragen; Retention löscht über `ts`.
Org-Isolation hier über **explizites `WHERE org_id = ?` im Service** (separate Base ⇒ der
globale Tenant-Listener greift nicht; daher Service-seitig strikt filtern).

## 5. Zweite DB – Entscheidung

**Gewählt (2026-06-23): separate MariaDB-Datenbank** `einsatzleiter_weather` auf demselben Server.
Retention **365 Tage**, Push-Intervall **5 min** (⇒ ≈ 288 Zeilen/Tag, ≈ 105k Zeilen/Jahr je Station).

- **Eigener SQLAlchemy-Engine + Sessionmaker + eigene `Base`** (`app/db_weather.py`),
  Connection-String aus `settings.WEATHER_DATABASE_URL` (Default = leer ⇒ Feature aus).
- **Eigener Connection-Pool** ⇒ Wetter-Last konkurriert nicht mit dem Haupt-Pool
  (der von Einsatz-Features genutzt wird) → erfüllt „Einsatz hat Vorrang".
- Eigenes, minimales Schema-Setup (`Base.metadata.create_all` beim Start oder separater
  Alembic-Branch) – **nicht** in die Haupt-Alembic-Kette mischen.
- Backup/Restore der Wetterdaten unabhängig von der operativen DB.

**Alternative (geringerer Aufwand):** SQLite-Datei `weather.db` für die Zeitreihe. Auch das
isoliert Schema und Pool sauber. Für eine Single-Server-Installation und 1 Station völlig
ausreichend (Davis @1 min ≈ 1.440 Zeilen/Tag). MariaDB ist aber bei Backups/Ops konsistenter.

> **Entscheidung erforderlich:** (a) separate MariaDB-DB vs. SQLite-Datei, (b) Retention-Dauer
> (Default-Vorschlag **90 Tage**). Beides per ENV konfigurierbar gestalten.

## 6. Ingestion (Meteobridge → Cloud)

### 6.1 Transport
Meteobridge „**Custom HTTP**"-Upload (Services → „HTTP"): konfigurierbare URL mit
Template-Platzhaltern, periodischer Outbound-Push (Intervall einstellbar, z. B. 1–5 min).
Da Meteobridge custom HTTP keine zuverlässigen Custom-Header garantiert, wird der
**Token als Query-Parameter** übergeben (HTTPS ⇒ verschlüsselt).

### 6.2 Endpoint
`POST /api/v1/weather/ingest?token=<STATION_TOKEN>` (auch GET zulässig, da Meteobridge
oft GET sendet). Ablauf:
1. `hash(token)` → `weather_station` per `ingest_token_hash` finden ⇒ liefert `org_id`+`station`.
   Kein Treffer ⇒ 401 (fail-closed).
2. Werte parsen + **Plausibilitätsgrenzen** (Temp −50..+60 °C, Wind 0..120 m/s, Feuchte 0..100 %,
   Druck 850..1100 hPa …) – Ausreißer verwerfen statt speichern.
3. **Snapshot-Upsert** auf `weather_station.last_*` + `last_seen_at` (Haupt-DB, 1 Zeile).
4. **Sample-Insert** in `weather_reading` (Wetter-DB).
5. `204 No Content`.

**Dedizierter Token** (nicht der Incident-`X-API-Key`!): least privilege – ein geleakter
Wetter-Token kann keine Einsätze anlegen. Token-Generierung/Anzeige in den Org-Einstellungen.

### 6.3 Meteobridge-Feld-Mapping (Davis-Template-Variablen)
| Meteobridge-Variable | Query-Param | Einheit |
|---|---|---|
| `[th0temp-act]` | `temp` | °C |
| `[th0hum-act]` | `hum` | % |
| `[wind0wind-act]` | `wind` | m/s |
| `[wind0gust-act]` | `gust` | m/s |
| `[wind0dir-act]` | `dir` | ° |
| `[thb0seapress-act]` | `press` | hPa |
| `[rain0rate-act]` | `rainrate` | mm/h |
| `[rain0total-daysum]` | `rainday` | mm |
| `[th0dew-act]` | `dew` | °C |
| `[sol0rad-act]` | `solar` | W/m² |
| `[uv0index-act]` | `uv` | – |

(Genaue Variablennamen in der Meteobridge-Doku bestätigen; PRO RED unterstützt das volle Davis-Set.)

## 7. Anzeige (Wetter-Seite)

- Neue **Stations-Karte „Eigene Wetterstation (Ist-Stand)"** im
  `incident_major/_weather_panel.html`, oberhalb/neben den vorhandenen GeoSphere-Werten.
- Inhalt: Temp, Feuchte, Wind/Böe + Richtung, Druck (mit Tendenz), Regenrate + Tagessumme,
  Taupunkt, Solar/UV, **„Stand: vor X min"** + **Online/Offline-Ampel** aus `last_seen_at`.
- Quelle liest **nur** den denormalisierten Snapshot der Haupt-DB (kein Zeitreihen-Zugriff).
- View-Builder analog `_build_abfluss_views()` in `ui_weather.py`; in alle drei Panel-Pfade
  (`/wetter`, GSL-Board, Einzeleinsatz) einhängen (Anzeige nur wenn Station für Org konfiguriert).
- Optional **24-h-Sparkline** (Temp/Wind) aus `weather_reading` – nur on-demand/lazy, nicht
  im 5-min-Auto-Refresh, um die Zeitreihen-DB im Hot-Path nicht zu treffen.

### Mehrwert: lokale Messwerte in die Szenario-Analyse
`weather_service.analyze_weather()` kann statt/zusätzlich zu GeoSphere die **gemessenen**
Stationswerte (Wind/Böe, Temp, Feuchte) als `CurrentWeather` erhalten ⇒ lokale, real gemessene
Sturm-/Waldbrand-/Glatteis-Warnungen für die FF (hoher Einsatzwert, bestehender Code wird wiederverwendet).

## 8. Retention & Einsatz-Vorrang

- **Retention-Loop** analog den bestehenden `asyncio`-Loops in `app/main.py` (lifespan):
  täglich (z. B. 03:30) `DELETE FROM weather_reading WHERE ts < now()-:retention_days`,
  **in Chunks (LIMIT N)**, um lange Locks zu vermeiden. **365 Tage** (ENV).
- **Rate-Limit** am Ingest-Endpoint (z. B. min. 20 s zwischen Pushes je Token) gegen Floods.
- **Eigener Pool** der Wetter-DB ⇒ keine Konkurrenz zum operativen Pool.
- Ingest schreibt asynchron + minimal; bei Wetter-DB-Fehler wird der Snapshot trotzdem
  gesetzt (Anzeige bleibt aktuell) und der Fehler nur geloggt – Wetter ist „best effort".
- Alle Wetter-Pfade hinter `WEATHER_ENABLED` + `OrgSettings.weather_enabled` (bestehend).

## 9. Konfiguration (neue ENV / Settings)

| Key | Default | Zweck |
|---|---|---|
| `WEATHER_DATABASE_URL` | `""` | Connection-String Wetter-DB (`einsatzleiter_weather`); leer ⇒ Zeitreihe/Feature aus |
| `WEATHER_STATION_INGEST_ENABLED` | `true` | Ingest-Endpoint aktiv |
| `WEATHER_READING_RETENTION_DAYS` | `365` | Aufbewahrung Zeitreihe |
| `WEATHER_INGEST_MIN_INTERVAL_S` | `60` | Rate-Limit je Token (Push alle 5 min ⇒ 60 s reicht) |

Pro-Org-Config (`OrgSettings`/`weather_station`): Stationsname, Koordinaten, Token (gehasht).

## 10. Umsetzung in PRs (analog Projekt-Workflow)

- **PR 1 – Modell & Wetter-DB:** `weather_station` (Haupt-DB, Migration `0097`, TenantScoped +
  in `_TENANT_TABLE_NAMES`); `app/db_weather.py` (zweiter Engine/Base) + `weather_reading`;
  ENV-Keys in `config.py` + `.env.example`.
- **PR 2 – Ingest-Endpoint:** `POST/GET /api/v1/weather/ingest`, Token-Auth (hash, fail-closed),
  Validierung, Snapshot-Upsert + Zeitreihen-Insert, Rate-Limit, Tests.
- **PR 3 – Org-Einstellungen:** UI zum Anlegen/Bearbeiten der Station + Token-Generierung
  (einmalige Klartext-Anzeige), Anleitung Meteobridge-URL.
- **PR 4 – Anzeige:** Stations-Snapshot-Karte in `_weather_panel.html` + View-Builder in
  `ui_weather.py` (alle 3 Pfade), Online/Offline-Ampel.
- **PR 5 – Retention-Loop:** täglicher Chunk-Delete in `lifespan`, konfigurierbare Dauer, Tests.
- **PR 6 (optional) – Szenario-Integration + 24-h-Sparkline** aus gemessenen Werten.

## 11. Entscheidungen & offene Punkte

Entschieden (2026-06-23):
1. **Wetter-DB:** separate MariaDB `einsatzleiter_weather`. ✅
2. **Retention:** 365 Tage. ✅
3. **Push-Intervall:** 5 min. ✅

Offen / im Zuge der Umsetzung zu prüfen:
4. **Custom-Header-Fähigkeit** der konkreten Meteobridge-Firmware prüfen (sonst Token in URL – Default-Plan).
5. Exakte Meteobridge-Template-Variablennamen gegen die PRO-RED-Doku verifizieren.
</content>
</invoke>
