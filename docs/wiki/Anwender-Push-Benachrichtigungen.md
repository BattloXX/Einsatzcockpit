# Push-Benachrichtigungen

← [Zurück zur Startseite](Home)

## Was sind Web-Push-Benachrichtigungen?

Push-Benachrichtigungen werden direkt auf dem Gerät angezeigt, auch wenn die App geschlossen ist. Für Feuerwehr-Einsätze ermöglichen sie:

- **Sofortige Alarmierung** bei neuem Einsatz
- **Warnung** wenn ein Atemschutz-Trupp den Rückzugsdruck erreicht
- **5-/10-Minuten-Warnungen** auch bei nicht-aktivem Browser-Tab

## Push-Benachrichtigungen aktivieren

### Schritt 1: App öffnen und einloggen

`https://einsatzleiter.feuerwehr-wolfurt.at/login`

### Schritt 2: Benachrichtigungen aktivieren

Profil-Menü (oben rechts) → **Push-Benachrichtigungen aktivieren**

Oder: Wenn eine Benachrichtigung ausgelöst wird, erscheint ein Toast mit dem Angebot zur Aktivierung.

### Schritt 3: Browser-Erlaubnis erteilen

Der Browser fragt nach Erlaubnis für Benachrichtigungen. **Erlauben** klicken.

> Auf iOS (Safari) ist dies erst ab iOS 16.4 möglich und nur wenn die App als PWA installiert wurde.

## Welche Benachrichtigungen werden gesendet?

| Ereignis | Empfänger | Inhalt |
|----------|-----------|--------|
| Neuer Einsatz (API) | Alle Abonnenten | Stichwort, Adresse, „Öffnen"-Button |
| 5-Minuten-Warnung | Alle Abonnenten | Einsatznummer, Meldungstext |
| 10-Minuten-Warnung | Alle Abonnenten | Einsatznummer, Meldungstext |
| Rückzugsdruck erreicht | Alle Abonnenten | Trupppname, Druckwert |
| Übungs-Einsatz | Alle Abonnenten | Wie oben, aber mit **[ÜBUNG]**-Präfix |

## Push-Benachrichtigungen deaktivieren

Profil-Menü → **Push-Benachrichtigungen verwalten** → **Deaktivieren**

Oder direkt im Browser: Adressleiste → Schloss-Symbol → Benachrichtigungen → **Blockieren**

## Benachrichtigungen funktionieren nicht?

Mögliche Ursachen:

| Problem | Lösung |
|---------|--------|
| Browser hat Erlaubnis verweigert | Einstellungen → Datenschutz → Benachrichtigungen → App erlauben |
| iOS ohne PWA-Installation | App zuerst zum Homescreen hinzufügen |
| Keine Internet-Verbindung | Push-Server ist nicht erreichbar |
| VAPID-Keys nicht konfiguriert | Admin: `.env` prüfen (VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY) |
| Firefox | Firefox-Push-Benachrichtigungen funktionieren nur mit Mozilla-Push-Server (nicht per VAPID) |

## Benachrichtigungen auf mehreren Geräten

Du kannst auf beliebig vielen Geräten Push-Benachrichtigungen aktivieren. Jedes Gerät erhält dieselben Benachrichtigungen unabhängig.
