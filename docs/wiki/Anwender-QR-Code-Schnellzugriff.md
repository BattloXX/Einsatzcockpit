# QR-Code Schnellzugriff

← [Zurück zur Startseite](Home)

## Was ist der QR-Code-Schnellzugriff?

Im laufenden Einsatz kann ein QR-Code generiert werden, der einem zweiten Gerät sofortigen Zugriff auf den Einsatz ermöglicht — **ohne Passwort-Eingabe**.

Typischer Anwendungsfall: Ein weiterer Abschnittsleiter kommt dazu und soll auf seinem Smartphone sofort den aktuellen Stand sehen und mitarbeiten.

## QR-Code anzeigen

Im Einsatz-Board: **QR-Code anzeigen** (Button in der Kopfzeile)

Ein Modal mit dem QR-Code erscheint. Der Code kann:
- Mit dem Smartphone abgescannt werden
- Als Link kopiert werden

## QR-Code scannen (zweites Gerät)

1. Kamera-App oder QR-Scanner öffnen
2. Code scannen
3. Browser öffnet die Einsatz-URL mit Token
4. Automatischer Login → direkt auf dem Einsatz-Board

Das eingeloggte Konto ist dasselbe wie das des Nutzers, der den QR-Code generiert hat (Session wird gespiegelt).

## Token-Gültigkeit

Der QR-Token ist **exakt für die Dauer des Einsatzes gültig**:

- **Einsatz aktiv** → Token gültig
- **Einsatz abgeschlossen** → Token ungültig → Gerät wird auf Login-Seite weitergeleitet

Es gibt kein separates Ablaufdatum. So kann ein Token für den gesamten Einsatz (auch mehrstündige Übungen) verwendet werden, ohne neu generiert werden zu müssen.

## Mehrere Geräte

Pro Einsatz können mehrere QR-Codes generiert werden (für verschiedene Nutzer). Jeder Code führt zum selben Einsatz, ist aber an den ausstellenden User gebunden.

## Sicherheitshinweis

- QR-Codes niemals öffentlich teilen (Social Media, Screenshots in öffentlichen Chats)
- Jede Person, die den QR-Code scannt, bekommt die vollen Berechtigungen des ausstellenden Nutzers
- Nach Einsatzende ist der Code automatisch ungültig

## Token manuell widerrufen

Ein Admin kann Token vorzeitig widerrufen:  
**Admin** → **Audit-Log** → Token suchen → **Widerrufen**

Oder per CLI:
```bash
python -m app.cli revoke-tokens --incident-id 42
```
