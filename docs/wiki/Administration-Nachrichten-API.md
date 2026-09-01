# Nachrichten-API administrieren

← [Zurück zur Startseite](Home)

> URL: `/admin/api-keys`  
> Zugänglich für: `org_admin`, `system_admin`

Über die Nachrichten-API können externe Systeme SMS und E-Mails über die bereits im
Einsatzcockpit eingerichteten Versandwege und Verteiler senden. Jeder Auftrag wird
persistiert, erhält eine Job-ID und kann je Empfänger nachverfolgt werden.

## Voraussetzungen

Vor dem Anlegen eines API-Keys muss mindestens ein passender Versandweg eingerichtet sein:

- SMS: Gateway oder EUS unter `/admin/sms-provider`
- E-Mail: Office 365, Resend oder SMTP unter `/admin/mail`

Ohne Versandweg lehnt die API neue Aufträge mit HTTP `409` ab. Ein idempotenter Wiederholungsaufruf
liefert den bereits vorhandenen Auftrag auch dann zurück, wenn der Versandweg inzwischen fehlt.

## Schritt 1: API-Key mit Scopes anlegen

1. **Verwaltung → API-Keys** öffnen.
2. Eine eindeutige Bezeichnung eingeben.
3. `sms:send` und/oder `mail:send` aktivieren.
4. **API-Key erstellen** wählen.
5. Den Key sofort sicher kopieren; er wird nur einmal angezeigt.

Die Nachrichten-Scopes sollten nur an Systeme vergeben werden, die tatsächlich senden dürfen.
Bestehende Keys haben nach der Migration nur `einsatz:write,mailing:import` und können keine
Nachrichten versenden.

## Schritt 2: Empfänger festlegen

### SMS

SMS-Aufträge können freie E.164-Nummern, `gruppen_ids` aus den SMS-Gruppen und
`mitglieder_ids` kombinieren. Die API normalisiert und dedupliziert nach Rufnummer. Ungültige
freie Nummern werden in `abgelehnt` gemeldet, gültige Empfänger aber trotzdem angenommen. Wenn
kein gültiger Empfänger übrig bleibt, antwortet die API mit `422`.

### E-Mail

E-Mail-Aufträge können freie Adressen und `listen_ids` aus dem Mailing-Modul kombinieren.
Adressen auf der Mailing-Sperrliste werden als `suppressed` gespeichert und nie versendet.
Besteht ein Auftrag ausschließlich aus unterdrückten Adressen, ist er sofort mit Status `sent`
abgeschlossen; `erfolg_anzahl` und `fehler_anzahl` bleiben jeweils `0`.

Fremde oder unbekannte Gruppen-, Mitglieder- oder Listen-IDs werden mit `404` abgelehnt. Daten
anderer Organisationen werden nie aufgelöst oder angezeigt.

## Limits

| Einstellung | Standard | Bedeutung |
|-------------|----------|-----------|
| `API_MESSAGE_RATELIMIT` | `20/minute` | POST-Anfragen je API-Key |
| `API_MESSAGE_MAX_RECIPIENTS` | `200` | aufgelöste Empfänger je Auftrag |
| `API_SMS_SYNC_MAX_RECIPIENTS` | `20` | Empfänger je synchronem Gateway-Auftrag |
| `API_SMS_DAILY_LIMIT` | `500` | SMS-Empfänger je Organisation in 24 Stunden |
| `API_MAIL_DAILY_LIMIT` | `2000` | Mail-Empfänger je Organisation in 24 Stunden |
| `API_MESSAGE_MAX_BODY_CHARS` | `10000` | maximale Text-/HTML-Gesamtlänge |

Ein Tageslimit von `0` deaktiviert das jeweilige Tageslimit. Überschreitungen liefern `429`.

## Idempotenz

`Key` ist pro Organisation und Kanal eindeutig. Ein wiederholter Request mit demselben `Key`
liefert dieselbe Job-ID und `idempotent_hit: true`; es entstehen keine neuen Empfänger und kein
zweiter Versand. Für unterschiedliche fachliche Nachrichten immer neue Schlüssel verwenden.

## Uptime Kuma als Alarmweg einrichten

1. Unter **Verwaltung → API-Keys** einen Key mit dem Scope `sms:send` anlegen.
2. In Uptime Kuma **Settings → Notifications → Setup Notification** öffnen und den Typ
   **SMS Gateway** wählen.
3. Als Server-URL die Basis-URL des Einsatzcockpits ohne `/api/v1/sms/send` eintragen, den
   API-Key hinterlegen und im Feld *To* eine oder mehrere E.164-Rufnummern angeben.

Der Gateway-Endpunkt sendet synchron und wartet auf die Bestätigung des eingerichteten
SMS-Versandwegs. Der Uptime-Kuma-Provider ist derzeit kein veröffentlichter Standard: Der
[Upstream-PR #7720](https://github.com/louislam/uptime-kuma/pull/7720) ist noch offen. Je nach
Uptime-Kuma-Version kann der Typ **SMS Gateway** daher fehlen; ändert sich dessen Format vor dem
Merge, muss die Integration angepasst werden.

## Status abfragen

```bash
curl https://einsatzcockpit.example.at/api/v1/nachricht/41 \
  -H "X-API-Key: ec_..."
```

Job-Status:

- `queued`: wartet auf Versand oder nächsten Mail-Retry
- `sending`: wird gerade verarbeitet
- `sent`: ohne Versandfehler abgeschlossen
- `partial`: teilweise erfolgreich
- `failed`: kein Empfänger erfolgreich

Empfänger haben `queued`, `sending`, `sent`, `failed` oder `suppressed`. E-Mail-Versand wird
höchstens dreimal versucht, mit wachsendem Abstand. SMS wird nach einem Timeout nicht erneut
versucht, weil ein unbemerkter Doppelversand nicht ausgeschlossen werden kann.

## Protokoll und Audit

API-SMS erscheinen unter `/admin/sms-senden` mit dem Label **API-Key** und Empfängerdetails.
Annahmevorgänge stehen im Audit-Protokoll als `api.sms.send` beziehungsweise `api.mail.send`,
inklusive Job-ID sowie Anzahl angenommener und abgelehnter Empfänger.

## Fehlerbehebung

| Code / Symptom | Ursache und Lösung |
|----------------|--------------------|
| `401` | Key falsch, gesperrt oder abgelaufen; Key im Admin prüfen |
| `403` | `sms:send` beziehungsweise `mail:send` fehlt; neuen Key mit passendem Scope anlegen |
| `404` | Gruppe, Mitglied, Liste oder Job existiert nicht in der Organisation |
| `409` | Kein nutzbarer Versandweg; SMS-Provider oder Mail-Konfiguration prüfen |
| `422` | Payload ungültig, Nachricht zu lang oder kein gültiger Empfänger |
| `429` | Request-Rate oder Tages-Empfängerlimit erreicht |
| Job bleibt `queued` | Mail wartet möglicherweise auf den nächsten Retry; Status später erneut abrufen |
| SMS ist `failed` | Gateway-Verbindung und EUS-Konfiguration prüfen; kein automatischer Retry |

Die technische Referenz mit vollständigen Payloads und curl-Beispielen steht unter
[Entwickler – REST-API](Entwickler-REST-API).

**Verwandt:** [SMS-Einsatzinfo](Administration-SMS-Einsatzinfo) ·
[Mail-Versand](Administration-Mail-Versand) · [REST-API](Entwickler-REST-API)
