# Dienstüberwachung und Systemstatus (Administration)

Die Seite **Systemstatus** überwacht die technischen Anbindungen einer Organisation,
meldet bestätigte Ausfälle und stellt geschützte Health-Endpunkte für externe Monitore
wie Uptime Kuma bereit. Sie ist unter **Verwaltung → Einstellungen → Systemstatus**
erreichbar.

## Überwachte Dienste

| Dienst | Prüfung |
|--------|---------|
| Print-Gateway | Ein gekoppeltes Gateway muss sich innerhalb seines Health-Intervalls über `last_seen_at` gemeldet haben. Als Toleranz gelten mindestens 180 Sekunden beziehungsweise das Dreifache des konfigurierten Intervalls. |
| SMS-Gateway | Ein verbundenes Gateway oder ein Heartbeat innerhalb der letzten zehn Minuten gilt als erreichbar. Eine konfigurierte EUS-Anbindung gilt als eingerichtet, wird aber nicht aktiv auf Erreichbarkeit geprüft. |
| Alarm seriell (W&T) | Das Gateway muss über `last_seen_at` frisch sein und die serielle W&T-Verbindung als verbunden melden. |
| Alarm DIBOS | Der DIBOS-Poll muss erfolgreich sein und innerhalb des erwarteten Poll-Intervalls stattgefunden haben. |

Die Detailzeile jeder Karte nennt den konkreten Grund des aktuellen Rohstatus.

## Zustände

- **Nicht eingerichtet:** Der Dienst ist für die Organisation nicht konfiguriert. Er ist
  nicht relevant (`relevant == False`) und löst daher niemals einen Alarm aus.
- **OK:** Der Dienst ist erreichbar oder ein relevanter Ausfall ist noch nicht bestätigt.
- **Störung:** Der Ausfall besteht über die Karenz hinaus. Dieser Zustand ist unabhängig
  davon, ob eine Benachrichtigung versendet wurde. Eine Organisation ohne Empfänger oder
  mit deaktivierter Überwachung sieht damit denselben Zustand wie Uptime Kuma; die Kachel
  weist dann mit „Keine Benachrichtigung verschickt“ auf den fehlenden Versand hin. Die
  gemeinsame Logik liegt in `dienst_zustand` und verwendet `bestaetigt_down`.

## Karenz und Wiederholung

Die **Karenz** beträgt standardmäßig 5 Minuten. Kurze Unterbrechungen lösen dadurch
keinen Fehlalarm aus. Ein Ausfall gilt erst nach Ablauf dieser Zeit als bestätigt; auch
der Health-Endpunkt verwendet dafür die Prüfung `bestaetigt_down`.

Die **Wiederholung** beträgt standardmäßig 60 Minuten. Bei einer anhaltenden Störung
werden Erinnerungen frühestens in diesem Abstand versendet. Nach der Erholung folgt
eine Entwarnung.

## Benachrichtigungen

Störungs-, Wiederholungs- und Entwarnungsmeldungen können per **E-Mail**, über einen
**Microsoft-Teams-Webhook** oder per **SMS** versendet werden. Mehrere SMS-Nummern
werden durch Kommas getrennt. Aktivieren Sie die Überwachung und tragen Sie mindestens
einen Empfänger ein. Mit **E-Mail testen**, **Teams testen** und **SMS testen** lässt
sich jeder eingerichtete Weg separat prüfen.

## Rollen

| Rolle | Rechte |
|-------|--------|
| `org_admin` / `admin` | Systemstatus und Einstellungen der eigenen Organisation verwalten |
| `system_admin` | Zusätzlich eine andere Organisation über `?org_id=<ID>` auswählen und verwalten |

## Uptime-Token und Health-Endpunkte

Monitoring-Tokens werden nur gehasht gespeichert. Der Klartext und die fertigen URLs
werden deshalb **nur unmittelbar nach dem Anlegen einmal angezeigt**. Kopieren Sie sie
sofort. Ein widerrufener Token kann nicht mehr verwendet werden.

| Endpunkt | Token | Bedeutung / gültige Schlüssel |
|----------|-------|-------------------------------|
| `GET /health` | nein | Prüft Anwendung und Datenbankverbindung; antwortet mit `{"status":"ok","db":"up"}` oder bei nicht erreichbarer Datenbank mit HTTP 503 und `{"status":"error","db":"down"}` |
| `GET /health/dienste` | ja | Gesamtstatus aller vier Dienste |
| `GET /health/dienst/{key}` | ja | Einzelstatus; gültig sind `print_gateway`, `sms_gateway`, `alarm_seriell` und `alarm_dibos` |

`/health` beantwortet die Frage „Läuft die Cloud?“, während `/health/dienste`
beantwortet: „Laufen die Dienste dieser Organisation?“. Nur die Dienst-Endpunkte
benötigen einen Monitoring-Token.

Bei den Token-Endpunkten ist die Authentifizierung entweder als Query-Parameter
`?token=<TOKEN>` oder als HTTP-Header `Authorization: Bearer <TOKEN>` möglich.

Beispiel für die Antwort des Gesamtstatus:

```json
{
  "dienste": [
    {
      "key": "print_gateway",
      "label": "Print-Gateway",
      "status": "ok",
      "roh_status": "ok",
      "seit": null,
      "detail": "Alle Print-Gateways melden frische Heartbeats."
    }
  ],
  "loop_letzter_lauf": "2026-08-28T18:21:17.106670"
}
```

| HTTP-Status | Bedeutung |
|-------------|-----------|
| `200` | Alle relevanten Dienste sind OK; nicht eingerichtete Dienste gelten ebenfalls nicht als Ausfall |
| `503` | Mindestens ein Ausfall ist nach Ablauf der Karenz bestätigt |
| `401` | Token ist ungültig, abgelaufen oder widerrufen |

## Uptime Kuma einrichten

Es werden zwei Monitore empfohlen:

1. Einen Monitor vom Typ **HTTP(s)** für `/health` anlegen. Er ist sofort nutzbar und
   benötigt keinen Token.
2. Einen Monitoring-Token anlegen und einen zweiten HTTP(s)-Monitor für
   `/health/dienste?token=<TOKEN>` erstellen.
3. Bei beiden Monitoren das Intervall auf **60 Sekunden** setzen.
4. Bei beiden unter **Accepted Status Codes** ausschließlich `200` eintragen.

Uptime Kuma wertet damit HTTP 503 als „down“ und kann seine eigenen Alarmwege auslösen.

## Fehlersuche

| Anzeige / Fehler | Ursache und Lösung |
|------------------|-------------------|
| `/health` liefert `503` mit `"db":"down"` | Die Anwendung ist erreichbar, kann aber die Datenbank nicht erreichen. Das ist kein Dienstproblem der Organisation. |
| `401` | Der Token wurde widerrufen, ist abgelaufen oder falsch. Einen neuen Token anlegen und die URL ersetzen. |
| Letzter Monitorlauf: „noch nie“ | Der Hintergrund-Loop der Dienstüberwachung läuft nicht. Worker-Konfiguration und Anwendungsprotokoll prüfen. |
| Dauerhaft „Nicht eingerichtet“ | Das betreffende Gateway beziehungsweise DIBOS ist für diese Organisation nicht vollständig konfiguriert oder aktiviert. |

---

**Verwandt:** [Print & Alarm Gateway](Administration-Print-Alarm-Gateway) ·
[SMS-Einsatzinfo & Empfang](Administration-SMS-Einsatzinfo) ·
[Teams-Alarmierung](Administration-Teams-Alarmierung)
