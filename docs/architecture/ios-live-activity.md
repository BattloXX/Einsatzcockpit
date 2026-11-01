# iOS Live Activity

Die App zeigt laufende Einsätze mit ActivityKit. Das Backend sendet dafür direkt
über HTTP/2 an APNs. Der Versand ist unabhängig von den sichtbaren
[FCM-Pushes für iOS](ios-push.md).

## Registrierung

`POST /api/v1/device/live-activity-token` verwendet dieselbe Authentifizierung wie
`/fcm-token`: Session-Cookie oder Bearer-Geräte-Token. Der JSON-Body lautet:

```json
{"kind":"start","token":"aabbcc","incident_id":null}
```

`kind=start` registriert einen Push-to-Start-Token (ab iOS 17.2).
`kind=activity` registriert den Token einer laufenden Activity und verlangt eine
`incident_id`. Der Einsatz muss für den Benutzer beziehungsweise seine
Organisation sichtbar sein, einschließlich kollaborativer Einsätze.

Tokens bestehen aus 1–512 Hex-Zeichen. Ungültige Eingaben liefern 400,
unsichtbare oder fehlende Einsätze 404, fehlende Authentifizierung 401.
Erfolg liefert `{"ok":true}`. Erneute Registrierung aktualisiert Besitzer,
Gerätebezug und Zeitstempel; alte Start-Tokens desselben Geräts und Benutzers
werden ersetzt. Ohne Gerätebezug gilt dies für den Benutzer mit Gerätebezug `null`.

`DELETE /api/v1/device/live-activity-token` mit `{"token":"aabbcc"}` entfernt nur
eigene Tokens und antwortet ebenfalls mit `{"ok":true}`.
Die Tabelle `live_activity_token` wird bei Benutzerlöschung berücksichtigt und
ist als flüchtiges Sicherheitsartefakt vom Organisations-Export ausgeschlossen.

## Konfiguration

| Variable | Bedeutung |
| --- | --- |
| `APNS_KEY_PATH` | Lesbare .p8-Datei außerhalb des Repos |
| `APNS_KEY_ID` | Schlüssel-ID aus dem Apple-Developer-Portal |
| `APNS_TEAM_ID` | Apple Developer Team-ID |
| `APNS_BUNDLE_ID` | Standard: `cloud.einsatzleiter.app` |
| `APNS_SANDBOX` | `true` für Entwicklungs-Builds, sonst `false` |

Der .p8-Schlüssel benötigt APNs-Berechtigung. Ohne vollständige Konfiguration
und lesbare Schlüsseldatei bleibt der Versand inaktiv. Der Schlüssel wird nicht
protokolliert. Das Backend signiert ES256-JWTs mit Header `kid` sowie Claims
`iss` und `iat` und erneuert sie nach 40 Minuten. `httpx[http2]` stellt die
HTTP/2-Abhängigkeit bereit; der Request-Timeout beträgt zehn Sekunden.

## Ablauf und Idempotenz

Der Erstalarm läuft über `notify_incident_created` → Alarm-Outbox → `_send_push`.
Dort startet ActivityKit nach der Übungsprüfung zusammen mit dem Alarm-Push.
Der alternative Pfad `_dispatch_live_push` ruft ActivityKit nach
`notify_org_web` auf. Beide Pfade beanspruchen atomar den bestehenden
`Incident.live_push_phase`-Merker: Nur der erste `created`-Versuch mit leerem
Merker darf starten. Die frühere Vorbelegung in `notify_incident_created`
entfällt. Nachalarme, Outbox-Wiederholungen und spätere `created`-Aufrufe lösen
keinen zweiten Start aus. Ein Absturz nach Beanspruchung kann einen Start
verlieren; APNs bietet hier keine transaktionale Zustellgarantie.

Starts gehen an Start-Tokens der Organisationsbenutzer, die einen iOS-FCM-Token
besitzen. Geräte mit bereits registriertem Activity-Token für diesen Einsatz
werden übersprungen (Vergleich über Benutzer-ID und Geräte-ID).
Andere Live-Ereignisse aktualisieren alle Activity-Tokens des Einsatzes.
Die vorhandene 90-Sekunden-Drosselung und die Übungsregeln gelten weiter.
`closed` umgeht die Drosselung, sendet `end` und entfernt die Activity-Tokens
auch bei Versandfehlern. Bei jedem Dispatch werden Activity-Tokens entfernt,
deren letzte Registrierung mehr als 13 Stunden zurückliegt. APNs-Versand
aktualisiert diesen Registrierungszeitstempel nicht.

## APNs-Vertrag

Produktion verwendet `api.push.apple.com`, Entwicklung
`api.sandbox.push.apple.com`, jeweils `POST /3/device/<token>` mit:

```text
apns-push-type: liveactivity
apns-topic: <APNS_BUNDLE_ID>.push-type.liveactivity
apns-priority: 10
apns-expiration: <Unix-Zeit jetzt + 3600>
authorization: bearer <JWT>
```

Jede Nachricht enthält `aps.timestamp` als ganzzahlige Unix-Zeit,
`aps.event` (`start`, `update`, `end`) und `aps.content-state`.
Der Content-State wird aus `build_incident_live_payload` abgeleitet:

| Schlüssel | Typ / Quelle |
| --- | --- |
| `alarm_code` | String / `alarm_type_code` |
| `address` | String / `address` |
| `phase_index` | Integer |
| `phase_count` | Integer |
| `phase_label` | String |
| `started_at` | Zahl, Unix-Sekunden UTC, aus dem ISO-Zeitstempel umgerechnet |
| `unit_count` | Integer |
| `is_exercise` | Boolean |
| `ended` | Boolean, normalerweise `false` |

`start` ergänzt `attributes-type: EinsatzActivityAttributes`,
`attributes: {incident_id: <int>, url: "/einsatz/12"}` und
`alert: {title: <Titel>, body: <Text>}` innerhalb von `aps`.
`update` enthält keinen Alert. `end` setzt `content-state.ended=true` und
`aps.dismissal-date` auf die ganzzahlige Unix-Zeit jetzt plus 900 Sekunden.

## Fehler und Grenzen

HTTP 200 gilt als Erfolg. HTTP 410 sowie HTTP 400 mit `BadDeviceToken`,
`Unregistered` oder `DeviceTokenNotForTopic` entfernen den Token.
HTTP 429 und Serverfehler erhalten ihn und setzen `last_error`; ein späteres
Live-Ereignis kann erneut senden. Es gibt keinen separaten APNs-Retry-Worker.
Transport- und Empfängerfehler werden abgefangen, damit die Alarmierung weiterläuft.
Logs enthalten höchstens die letzten sechs Token-Zeichen; HTTPX-Fehlertexte mit
vollständiger Request-URL werden unterdrückt.

Push-to-Start setzt iOS 17.2 voraus. Eine Activity läuft höchstens acht Stunden
und bleibt anschließend bis zu vier Stunden am Sperrbildschirm; die Bereinigung
nach 13 Stunden enthält einen Puffer. Ohne neuen Activity-Token kann das Backend
eine gestartete Activity nicht aktualisieren. Zustellung und Darstellung werden
von APNs, ActivityKit und den Geräteeinstellungen bestimmt.
