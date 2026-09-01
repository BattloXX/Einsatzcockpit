# REST-API

← [Zurück zur Startseite](Home)

Die REST-API ist für **externe Systeme** (Alarmierungssystem) gedacht. Alle Endpunkte erfordern einen gültigen API-Key.

## Authentifizierung

```http
X-API-Key: ec_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

API-Keys sind org-spezifisch. Der Key wird als SHA-256-Hash gespeichert, nie im Klartext.

## Scopes

Jeder API-Key besitzt explizite Berechtigungen. Für die Nachrichten-API sind relevant:

| Scope | Endpunkte |
|-------|-----------|
| `sms:send` | `POST /api/v1/sms`, Status eigener Nachrichtenjobs |
| `mail:send` | `POST /api/v1/mail`, Status eigener Nachrichtenjobs |

Für `GET /api/v1/nachricht/{id}` genügt einer der beiden Scopes. Ein gültiger Key ohne den
benötigten Scope erhält HTTP `403`. Bestehende Keys wurden mit
`einsatz:write,mailing:import` migriert und dürfen nicht automatisch Nachrichten versenden.

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
X-API-Key: ec_...
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
X-API-Key: ec_...
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
X-API-Key: ec_...
```

Response: Array von Einsatz-Objekten mit `id`, `alarm_type_code`, `started_at`, `is_exercise`.

### GET /api/v1/einsatz/{id} — Einzelner Einsatz

```http
GET /api/v1/einsatz/42
X-API-Key: ec_...
```

### POST /api/v1/sms — SMS senden

Nimmt einen persistenten Versandauftrag an. Erforderlicher Scope: `sms:send`.

| Feld | Typ | Pflicht | Validierung | Beschreibung |
|------|-----|---------|-------------|-------------|
| `Key` | string | ja | 1–200 Zeichen | Idempotenz-Token je Organisation und Kanal |
| `text` | string | ja | 1–`API_MESSAGE_MAX_BODY_CHARS` Zeichen | SMS-Inhalt |
| `empfaenger` | object | ja | mindestens ein gültig aufgelöstes Ziel | Empfängerauswahl |
| `empfaenger.nummern` | string[] | nein | strikt E.164 (`+` und 8–15 Ziffern) | freie Nummern |
| `empfaenger.gruppen_ids` | integer[] | nein | Gruppe muss zur Key-Organisation gehören | SMS-Gruppen |
| `empfaenger.mitglieder_ids` | integer[] | nein | Mitglied muss zur Key-Organisation gehören | einzelne Mitglieder |

```bash
curl -X POST https://einsatzcockpit.example.at/api/v1/sms \
  -H "X-API-Key: ec_..." -H "Content-Type: application/json" \
  -d '{
    "Key":"sms-2026-09-01-001",
    "text":"Probealarm Sirene 12:00. Keine Aktion erforderlich.",
    "empfaenger":{"nummern":["+436641234567"],"gruppen_ids":[3],"mitglieder_ids":[12]}
  }'
```

Ungültige freie Nummern werden unter `abgelehnt` zurückgegeben und blockieren gültige Ziele nicht.
Alle Ziele werden nach normalisierter Nummer dedupliziert.

### POST /api/v1/mail — E-Mail senden

Nimmt einen persistenten Mail-Auftrag an. Erforderlicher Scope: `mail:send`.

| Feld | Typ | Pflicht | Validierung | Beschreibung |
|------|-----|---------|-------------|-------------|
| `Key` | string | ja | 1–200 Zeichen | Idempotenz-Token je Organisation und Kanal |
| `betreff` | string | ja | 1–500 Zeichen | Mail-Betreff |
| `text` | string/null | bedingt | zusammen mit HTML höchstens `API_MESSAGE_MAX_BODY_CHARS` | Text-Version |
| `html` | string/null | bedingt | mindestens `text` oder `html` | HTML-Version |
| `empfaenger` | object | ja | mindestens ein gültig aufgelöstes Ziel | Empfängerauswahl |
| `empfaenger.adressen` | string[] | nein | plausible, Header-sichere E-Mail-Adresse | freie Adressen |
| `empfaenger.listen_ids` | integer[] | nein | Liste muss zur Key-Organisation gehören | Mailinglisten |

```bash
curl -X POST https://einsatzcockpit.example.at/api/v1/mail \
  -H "X-API-Key: ec_..." -H "Content-Type: application/json" \
  -d '{
    "Key":"mail-2026-09-01-001",
    "betreff":"Übungstermin verschoben",
    "text":"Die Übung am Freitag entfällt.",
    "html":"<p>Die Übung am Freitag entfällt.</p>",
    "empfaenger":{"adressen":["max@example.at"],"listen_ids":[5]}
  }'
```

Adressen auf der Mailing-Sperrliste werden als `suppressed` gespeichert und nicht versendet.

### Antwort auf Versandaufträge

Beide POST-Endpunkte antworten mit `202 Accepted`:

```json
{
  "id": 41,
  "kanal": "sms",
  "status": "queued",
  "empfaenger_anzahl": 12,
  "abgelehnt": [{"wert": "0664 abc", "grund": "ungueltige_nummer"}],
  "idempotent_hit": false
}
```

Ein bereits bekannter `Key` liefert dieselbe `id`, keine neuen Empfänger und
`idempotent_hit: true`. Der Lookup geschieht vor der Versandwegprüfung.

### GET /api/v1/nachricht/{id} — Versandstatus

Erforderlicher Scope: `sms:send` oder `mail:send`. Ein Job einer fremden Organisation liefert
`404`.

```bash
curl https://einsatzcockpit.example.at/api/v1/nachricht/41 \
  -H "X-API-Key: ec_..."
```

```json
{
  "id": 41,
  "kanal": "sms",
  "status": "partial",
  "erstellt_am": "2026-09-01T10:00:00Z",
  "abgeschlossen_am": "2026-09-01T10:00:14Z",
  "empfaenger_anzahl": 2,
  "erfolg_anzahl": 1,
  "fehler_anzahl": 1,
  "empfaenger": [
    {"ziel":"+436641234567","name":"Max M.","status":"sent","provider":"gateway",
     "gesendet_am":"2026-09-01T10:00:03Z","fehler":null}
  ]
}
```

Job-Status: `queued`, `sending`, `sent`, `partial`, `failed`. Empfängerstatus: `queued`,
`sending`, `sent`, `failed`, `suppressed`. Zeitstempel sind UTC mit `Z`-Suffix. Mail-Empfänger
werden bis zu dreimal versucht; SMS wird wegen des Doppelversandrisikos nicht wiederholt.

### Fehler der Nachrichten-API

| Code | Bedeutung |
|------|-----------|
| 401 | Key ungültig, gesperrt oder abgelaufen |
| 403 | erforderlicher Scope fehlt |
| 404 | fremde oder unbekannte Gruppe, Liste, Mitglied oder Job-ID |
| 409 | kein SMS- beziehungsweise Mail-Versandweg konfiguriert |
| 422 | Payload ungültig, zu viele oder keine gültigen Empfänger |
| 429 | Key-basiertes Request-Limit oder 24-Stunden-Empfängerlimit überschritten |

Standardmäßig gelten `20/minute`, höchstens 200 Empfänger pro Auftrag, 500 SMS-Empfänger und
2.000 Mail-Empfänger je Organisation und 24 Stunden. Die Umgebungsvariablen sind in
[Nachrichten-API administrieren](Administration-Nachrichten-API) beschrieben.

## Stufen-Normalisierung

Die API normalisiert `Stufe` automatisch: `f3` → `F3`, `T3` bleibt `T3`.

## curl-Beispiele

```bash
# Einsatz anlegen:
curl -X POST https://einsatzleiter.feuerwehr-wolfurt.at/api/v1/einsatz \
  -H "X-API-Key: ec_xxxx" \
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
  -H "X-API-Key: ec_xxxx"

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
API-Key: ec_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Key-ID: 1
Label: Alarmierungssystem Leitstelle
```

> Den Key sofort kopieren — er wird nur einmal im Klartext angezeigt.
