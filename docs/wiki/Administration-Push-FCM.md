# Push-Benachrichtigungen mit Firebase Cloud Messaging (FCM)

← [Zurück zur Startseite](Home.md)

Firebase Cloud Messaging wird fuer Push-Benachrichtigungen an die native Android-App verwendet. Die FCM-Konfiguration ist **global** und gilt nicht pro Feuerwehr-Organisation: Ein einziges Firebase-Projekt versorgt alle Organisationen.

## Firebase-Dienstkonto einrichten

1. In der Firebase Console das Projekt `cloud.einsatzleiter.app` öffnen.
2. Unter **Projekteinstellungen → Dienstkonten** einen neuen privaten Schluessel generieren und die Dienstkonto-JSON herunterladen.
3. Die JSON-Datei auf dem Server außerhalb des Repositorys ablegen, zum Beispiel unter `/etc/einsatzleiter/fcm-service-account.json`. Dateirechte und Eigentümer wie bei anderen Server-Secrets einschränken.

## Einsatzcockpit konfigurieren

Die Konfiguration kann auf zwei Wegen erfolgen. Werte aus der Datenbank haben Vorrang vor der `.env`-Datei.

### Admin-Oberfläche

Unter **Admin → System → System-Einstellungen** folgende Felder setzen:

| Feld | Wert |
|---|---|
| `fcm_enabled` | `true` |
| `fcm_project_id` | Firebase-Projekt-ID (z. B. `einsatzleiter-12345`, in der Firebase Console unter Projekteinstellungen — **nicht** der Android-Package-Name `cloud.einsatzleiter.app`) |
| `fcm_credentials_path` | Absoluter Pfad, zum Beispiel `/etc/einsatzleiter/fcm-service-account.json` |

Diese Datenbank-Einstellungen werden ohne Neustart wirksam.

### `.env`

Alternativ können folgende Variablen gesetzt werden:

```dotenv
FCM_ENABLED=true
FCM_PROJECT_ID=einsatzleiter-12345
FCM_CREDENTIALS_PATH=/etc/einsatzleiter/fcm-service-account.json
```

Nach einer Aenderung der `.env` muss die Anwendung neu gestartet werden.

## Paket und Funktion pruefen

Das Python-Paket `firebase-admin` muss in der produktiven Umgebung installiert sein. Bei einer normalen Installation beziehungsweise einem Redeploy aus dem Projekt wird es über die Projektabhängigkeiten installiert.

Nach der Konfiguration darf die Warnung `FCM ist nicht konfiguriert` nicht mehr im Server-Log erscheinen. Ein Testversand ist unter `/admin/push-nachrichten` möglich (Buttons **Push jetzt registrieren** und **Test senden**, sichtbar sobald die Seite über die native App geöffnet wird). Falls die Registrierung dort fehlschlägt, in der App stattdessen **Über die App → Push jetzt registrieren** nutzen — dort ist die Registrierung zuverlässiger, da die Capacitor-Bridge auf dieser lokalen Seite garantiert verfügbar ist.

## Fehlerbehebung

- **Warnung bleibt sichtbar:** `fcm_enabled`, Projekt-ID und Pfad kontrollieren. Bei `.env`-Aenderungen den Dienst neu starten.
- **Dienstkonto kann nicht geladen werden:** Absoluten Pfad, Dateirechte und Inhalt der JSON-Datei prüfen.
- **Importfehler für Firebase:** Sicherstellen, dass `firebase-admin` in der aktiven virtuellen Umgebung installiert ist.
- **Firebase lehnt den Versand ab:** Projekt-ID auf Tippfehler prüfen und sicherstellen, dass die Dienstkonto-Datei aus demselben Firebase-Projekt stammt.

---

**Verwandt:** [Push-Benachrichtigungen](Anwender-Push-Benachrichtigungen.md) · [Systemd-Service](Installation-Systemd-Service.md)
