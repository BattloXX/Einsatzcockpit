# iOS-Push über FCM und APNs

`send_fcm` in `app/services/push_service.py` unterscheidet anhand von
`FcmToken.platform == "ios"`. Für iOS wird genau eine sichtbare Nachricht mit
FCM-`Notification` (Titel und Text) und APNs-Konfiguration gesendet. Die Daten
enthalten weiterhin `url`, `title`, `body`, `channel_id`, gegebenenfalls
`delivery_id` und zusätzliche Aufruferdaten. Android-Capabilities ändern diesen
iOS-Pfad nicht; auch der Testalarm verwendet ihn.

| Kanal | interruption-level | Sound | Ablauf nach Versand |
| --- | --- | --- | --- |
| `einsatz_alarm` | `time-sensitive` | `default` | 600 Sekunden |
| `einsatz_alarm` mit Critical Alerts | `critical` | `CriticalSound(default, critical=True, volume=1.0)` | 600 Sekunden |
| Alle anderen | `active` | `default` | 3600 Sekunden |

Sichtbare Nachrichten verwenden `apns-push-type: alert`, `apns-priority: 10`
und `apns-expiration` als Unix-Zeit-String. `thread_id` entspricht dem Kanal
oder fällt auf `ecp` zurück.

Die boolesche Einstellung `IOS_CRITICAL_ALERTS_ENABLED` in `app/config.py`
wird über die gleichnamige Umgebungsvariable gesetzt und ist standardmäßig
`False`. Erst nach Freigabe der Critical-Alerts-Berechtigung durch Apple
aktivieren. Der Schalter betrifft ausschließlich Einsatzalarme.

Bei `wake_only=True` wird ausschließlich eine Hintergrundnachricht gesendet:
kein Notification-Block, Daten mit `silent="1"`, `Aps(content_available=True)`,
`apns-push-type: background` und Priorität `5`. Solche Hintergrundnachrichten
sind unter iOS unzuverlässig und erreichen eine durch Wischen beendete App
nicht zuverlässig; sichtbare Alarme hängen deshalb nicht von ihnen ab.

Android behält seine bisherigen Pfade: ein einzelner Datenalarm bei
`native_alarm`-Capability, sonst Wake plus Display (bei `wake_only` nur Wake).
iOS verwendet keine `AndroidConfig` und keinen zusätzlichen Wake vor einer
sichtbaren Nachricht. Fehler und Token-Löschung laufen über dieselbe
`_send`-Funktion; Erfolg und Fehler werden im Delivery-Protokoll festgehalten.

Die Capacitor-Bridge navigiert nach Antippen über
`action.notification.data.url`. Im Vordergrund führt sie keine eigene Anzeige
aus; die Banner-Darstellung übernimmt iOS gemäß `presentationOptions` der App.
