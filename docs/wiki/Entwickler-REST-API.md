# REST-API

← [Zurück zur Startseite](Home)

Die REST-API ist für **externe Systeme** (Alarmierungssystem) gedacht. Alle Endpunkte erfordern einen gültigen API-Key.

## Authentifizierung

```http
X-API-Key: elh_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

API-Keys sind org-spezifisch. Der Key wird als SHA-256-Hash gespeichert, nie im Klartext.

## Rate-Limiting

Alarm-Endpunkte sind **per API-Key** rate-limited (nicht per IP). Jeder Key hat ein eigenes Budget:
- Standard: `60/minute` (konfigurierbar via `API_ALARM_RATELIMIT` in `.env`)
- Überschreitung: HTTP 429 Too Many Requests

Der Rate-Limit-Key ist `apikey:sha256(key)[:24]` — verschiedene Keys beeinflussen sich nicht gegenseitig.

## Endpunkte

### POST /api/v1/einsatz — Einsatz anlegen

Legt einen neuen Einsatz an (oder gibt den bestehenden zurück bei Idempotenz).

**Request:**

```http
POST /api/v1/einsatz
X-API-Key: elh_...
Content-Type: application/json
```

```json
{
  "Key": "426747e9-0126-45bc-a0c1-b51a182de14b",
  "Nummer": 1978,
  "AlarmDatumZeit": "2026-05-19T21:11:11.323",
  "Zeitzone": "Europe/Vienna",
  "Stufe": "t3",
  "Art": "T",
  "Meldung": "Wolfurt Senderstraße 34 Heizraum überflutet",
  "Einsatzgrund": "Heizraum überflutet",
  "Ort": "Wolfurt",
  "Strasse": "Senderstraße",
  "HausNr": "34",
  "Leitstellennummer": "fu26303655",
  "Uebung": false
}
```

**Felder:**

| Feld | Typ | Pflicht | Validierung | Beschreibung |
|------|-----|---------|-------------|-------------|
| `Key` | string | ja | 1–200 Zeichen, Strip, kein reines Whitespace | Idempotenz-Schlüssel |
| `Nummer` | integer | nein | ≥ 0 | Einsatznummer aus Alarmierungssystem |
| `Leitstellennummer` | string | nein | max. 40 Zeichen | Stabile Leitstellen-Einsatznummer (z. B. `fu26303655`). Matching-Schlüssel gegen bereits per LIS/DIBOS angelegte Einsätze (`Incident.lis_operation_number`) — verhindert doppelte Einsätze, wenn derselbe Alarm über mehrere Wege eintrifft. Koordinaten werden dabei nie über diesen Weg gesetzt/überschrieben — die kommen ausschließlich aus LIS/DIBOS bzw. dem eigenen Geocoding. |
| `AlarmDatumZeit` | ISO-8601 | nein | | Zeitpunkt des Alarms |
| `Zeitzone` | string (IANA) | nein | | Zeitzone für naive `AlarmDatumZeit` |
| `Stufe` | string | nein | max. 10 Zeichen, wird uppercase normalisiert | Alarmstufe (t1–t9, f1–f4) → F3 |
| `Art` | string | nein | | Einsatzart: `T` oder `F` |
| `Meldung` | string | nein | max. 5000 Zeichen | Freitext-Meldung |
| `Einsatzgrund` | string | nein | max. 500 Zeichen | Kurzer Grund |
| `Ort` | string | nein | max. 200 Zeichen | Ort/Gemeinde |
| `Strasse` | string | nein | max. 200 Zeichen | Straße |
| `HausNr` | string | nein | max. 20 Zeichen | Hausnummer |
| `Uebung` | boolean | nein | | Übungseinsatz? (Standard: `false`) |
| `Name` | string | nein | max. 200 Zeichen | Meldender |
| `Telefon` | string | nein | max. 50 Zeichen | Rückrufnummer |

#### Zeitzone-Handling

- **Mit UTC-Offset** (empfohlen): `"2026-05-19T21:11:11+02:00"` — wird direkt übernommen.
- **Naiv (ohne Offset)**: `"2026-05-19T21:11:11.323"` — Zeitzone-Priorität:
  1. `Zeitzone`-Feld im Request
  2. In der Organisation hinterlegte Zeitzone
  3. Server-Default (`DEFAULT_TIMEZONE`, Standard: `Europe/Vienna`)

Intern werden alle Zeitpunkte als UTC gespeichert.

**Response (200 OK):**

```json
{
  "id": 42,
  "external_key": "426747e9-0126-45bc-a0c1-b51a182de14b",
  "url": "/einsatz/42",
  "created": true,
  "board_token": "InVzZXJfaWQiOiAxfQ.abc123...",
  "board_url": "https://einsatzleiter.example.at/qr-login?incident_id=42&token=..."
}
```

Bei Idempotenz (Key bereits bekannt): `"created": false`, `"id": <vorhandene ID>`.

**Fehler-Responses:**

| Code | Bedeutung |
|------|-----------|
| 401 | API-Key ungültig oder fehlt |
| 422 | Payload-Validierungsfehler (z.B. Key zu lang, Lat außerhalb Bereich) |
| 429 | Rate-Limit überschritten |
| 500 | Serverfehler |

### POST /api/v1/lage/alarm — Lage-Alarm anlegen

Erstellt eine neue Einsatzstelle in einer laufenden Großschadenslage.

```http
POST /api/v1/lage/alarm
X-API-Key: elh_...
Content-Type: application/json
```

```json
{
  "Key": "lage-001",
  "Meldung": "Wasserschaden Erdgeschoss",
  "Ort": "Wolfurt",
  "Strasse": "Bahnhofstraße",
  "HausNr": "12",
  "Lat": 47.4664,
  "Lng": 9.7416
}
```

Zusätzliche Felder gegenüber AlarmPayload:

| Feld | Typ | Validierung |
|------|-----|-------------|
| `Lat` | float | -90.0 bis +90.0 |
| `Lng` | float | -180.0 bis +180.0 |

### GET /api/v1/einsatz/active — Aktive Einsätze

```http
GET /api/v1/einsatz/active
X-API-Key: elh_...
```

Response: Array von Einsatz-Objekten mit `id`, `alarm_type_code`, `started_at`, `is_exercise`.

### GET /api/v1/einsatz/{id} — Einzelner Einsatz

```http
GET /api/v1/einsatz/42
X-API-Key: elh_...
```

## Stufen-Normalisierung

Die API normalisiert `Stufe` automatisch: `f3` → `F3`, `T3` bleibt `T3`.

## curl-Beispiele

```bash
# Einsatz anlegen:
curl -X POST https://einsatzleiter.feuerwehr-wolfurt.at/api/v1/einsatz \
  -H "X-API-Key: elh_xxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "Key": "test-uuid-001",
    "Nummer": 100,
    "AlarmDatumZeit": "2026-05-22T14:30:00",
    "Zeitzone": "Europe/Vienna",
    "Stufe": "t1",
    "Art": "T",
    "Meldung": "Wasserschaden Keller",
    "Ort": "Wolfurt",
    "Strasse": "Teststraße",
    "HausNr": "1"
  }'

# Aktive Einsätze:
curl https://einsatzleiter.feuerwehr-wolfurt.at/api/v1/einsatz/active \
  -H "X-API-Key: elh_xxxx"

# Rate-Limit-Header in der Response:
# X-RateLimit-Limit: 60
# X-RateLimit-Remaining: 59
# X-RateLimit-Reset: 1717000060
```

## API-Key erstellen

```bash
python -m app.cli create-api-key --label "Alarmierungssystem Leitstelle" --org-id 1
```

Ausgabe:
```
API-Key: elh_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Key-ID: 1
Label: Alarmierungssystem Leitstelle
```

> Den Key sofort kopieren — er wird nur einmal im Klartext angezeigt.
