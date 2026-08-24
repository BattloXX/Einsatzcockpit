# WebSocket-Events

← [Zurück zur Startseite](Home)

## Verbindung

```
wss://einsatzleiter.feuerwehr-wolfurt.at/ws/incident/{incident_id}
```

Die Verbindung erfordert eine aktive Session (Session-Cookie). Ohne Login wird die Verbindung abgelehnt.

Channel `0` ist der globale Channel (neue Einsätze, systemweite Meldungen).

## Event-Format

Alle Events sind JSON-Objekte mit einem `type`-Feld.

```json
{
  "type": "event_name",
  "...": "weitere Felder"
}
```

## Event-Typen

### `incident_created`
Neuer Einsatz wurde über die API angelegt.

```json
{
  "type": "incident_created",
  "incident_id": 42,
  "alarm": "T3",
  "address": "Teststraße 1, Wolfurt",
  "url": "/einsatz/42"
}
```

### `board_update`
Ein Partial des Boards wurde geändert (Fahrzeug verschoben, Auftrag erledigt, etc.).

```json
{
  "type": "board_update",
  "target": "#vehicle-card-7",
  "html": "<div id='vehicle-card-7'>...</div>"
}
```

`target` ist ein CSS-Selektor. Das Frontend swappt den HTML-Inhalt (`htmx.swap`).

### `alarm_popup`
5-Minuten- oder 10-Minuten-Warnung.

```json
{
  "type": "alarm_popup",
  "title": "10-Minuten-Meldung",
  "message_id": 15,
  "text": "Zweite Lagemeldung an Leitstelle"
}
```

### `troop_warning`
Eine Warnschwelle wurde für einen Atemschutztrupp überschritten.
Wird vom Server-Watchdog (alle 5 s) gesendet, sobald eine neue Schwelle erreicht ist.

`kind` kann sein:
- `"one_third"` – 1/3 der Einsatzzeit verstrichen, keine Lagemeldung seit Einsatzbeginn
- `"withdraw"` – Rückzugsdruck unterschritten
- `"max_time"` – Max-Einsatzzeit überschritten

```json
{
  "type": "troop_warning",
  "troop_id": 3,
  "kind": "withdraw"
}
```

### `troop_warning_acked`
Eine Warnung wurde manuell quittiert (via POST `/atemschutz/{troop_id}/ack`).

```json
{
  "type": "troop_warning_acked",
  "troop_id": 3,
  "kind": "one_third"
}
```

### `troop_meldung`
Eine Lagemeldung (Freitext) wurde abgesetzt.

```json
{
  "type": "troop_meldung",
  "troop_id": 3,
  "text": "Trupp im Treppenhaus 2. OG",
  "time_warning": "ok"
}
```

### `troop_standort`
Standort eines Trupps wurde aktualisiert.

```json
{
  "type": "troop_standort",
  "troop_id": 3,
  "location_text": "Treppenhaus Süd"
}
```

### `incident_closed`
Einsatz wurde abgeschlossen.

```json
{
  "type": "incident_closed",
  "incident_id": 42
}
```

## Frontend-Handler (app.js)

```javascript
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  switch (data.type) {
    case 'board_update':
      htmx.swap(data.target, data.html, { swapStyle: 'outerHTML' });
      break;
    case 'alarm_popup':
      Alpine.store('app').showTimerAlert(data);
      break;
    case 'troop_warning':
      Alpine.store('app').showTroopWarning(data);
      break;
    case 'incident_created':
      Alpine.store('app').showNewIncidentToast(data);
      break;
  }
};
```

## Reconnect-Logik

Bei Verbindungsabbruch: Exponentielles Backoff (1s → 2s → 4s → max 30s).  
Nach Reconnect: Vollständiger Reload des Board-Inhalts (`hx-get` auf den Board-Endpoint).

## Mehrere Worker und Sticky Sessions

Bei mehreren Gunicorn-Workern halten verschiedene Worker verschiedene WebSocket-Verbindungen. Wenn Worker A eine Änderung macht, muss Worker B die Verbindungen von Worker A nicht kennen.

**Lösung für Produktion:** Redis Pub/Sub als gemeinsamen Message-Bus nutzen.  
**Einfachste Lösung für Einzelserver:** `-w 1` (ein Worker, kein verteilter State nötig).
