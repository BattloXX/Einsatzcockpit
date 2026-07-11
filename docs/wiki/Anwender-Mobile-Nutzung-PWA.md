# Mobile Nutzung und PWA

← [Zurück zur Startseite](Home)

## Progressive Web App (PWA)

Die Webapp kann wie eine native App auf dem Gerät installiert werden. Installierte Apps:
- Starten ohne Browser-Chrome (Vollbild)
- Funktionieren auch bei schlechter Verbindung (Offline-Cache)
- Erhalten Push-Benachrichtigungen
- Erscheinen auf dem Homescreen

## Anmeldung in der Android-App

Die native Android-App (separates APK, siehe [SMS-Gateway](Installation-SMS-Gateway) für den Download-Link) bietet beim ersten Start vier Anmeldewege:

| Weg | Für wen | Wie |
|-----|---------|-----|
| **QR-Code scannen** | Geräte-Pairing (Tablet, Anzeigegerät, SMS-Gateway) | Admin erzeugt QR-Code unter **Admin → Geräte-Login**, App scannt ihn |
| **PIN eingeben statt scannen** | Geräte-Pairing ohne Kamerazugriff | Admin zeigt zusätzlich zum QR-Code eine PIN (10 Minuten gültig, einmal verwendbar); PIN in der App eintippen |
| **Mit Account anmelden** | Persönliche Accounts | Normale Benutzername/Passwort-Anmeldung, bleibt bis zu 30 Tage aktiv |
| **Per SMS-PIN anmelden** | Persönliche Accounts, passwortlos | Handynummer eingeben → Einmal-PIN per SMS → PIN eintippen. Setzt ein verbundenes SMS-Gateway der eigenen Organisation voraus |

Geräte-Pairing (QR-Code/PIN) erzeugt eine dauerhafte Geräte-Session ohne Ablauf — gedacht für fest verbaute/gemeinsam genutzte Geräte (Fahrzeug-Tablet, Gerätehaus-Anzeige, SMS-Gateway-Handy). Die persönliche Anmeldung (Account-Login/SMS-PIN) ist an den einzelnen Nutzer gebunden und läuft nach spätestens 30 Tagen ab.

### Gerät + SMS-Gateway kombinieren

Ein Android-Gerät kann gleichzeitig als Einheit-Gerät (zeigt das Einsatz-Board) **und** als
SMS-Gateway (sendet/empfängt SMS über die eingebaute SIM) laufen — z. B. ein fest verbautes
Fahrzeug-Tablet mit SIM-Karte. Unter **Admin → Geräte-Login → + Gerät registrieren** die Option
„Gerät + SMS-Gateway" wählen: es wird ein einziger QR-Code erzeugt, der beide Rollen koppelt. Nach
dem Scan zeigt die App das Einsatz-Board als Hauptansicht; der SMS-Gateway-Dienst läuft im
Hintergrund weiter (erkennbar an der dauerhaften Benachrichtigung „SMS-Gateway aktiv").

## Installation auf iOS (Safari)

1. App in **Safari** öffnen (`https://einsatzleiter.feuerwehr-wolfurt.at`)
2. Teilen-Symbol (Rechteck mit Pfeil nach oben) → **Zum Homescreen**
3. Name bestätigen → **Hinzufügen**

## Installation auf Android (Chrome)

1. App in **Chrome** öffnen
2. Drei-Punkte-Menü → **App installieren** oder **Zum Startbildschirm hinzufügen**
3. Bestätigen

Alternativ erscheint Chrome automatisch ein "Installieren"-Banner.

## Installation auf Windows/Mac (Chrome/Edge)

1. App im Browser öffnen
2. In der Adressleiste: Install-Symbol (Bildschirm mit Pfeil) klicken
3. Oder: Drei-Punkte-Menü → **App installieren**

## Offline-Verhalten

Die PWA cached folgende Inhalte für Offline-Nutzung:
- Login-Seite (Kein Zugriff ohne vorherigen Login möglich)
- CSS, JavaScript, Icons (App lädt schneller)
- Zuletzt geöffneter Einsatz (read-only)

**Was offline NICHT funktioniert:**
- Änderungen speichern (werden in Queue gepuffert)
- Neue Einsätze sehen
- Echtzeit-Sync

## Offline-Queue (ausstehende Aktionen)

Wenn du offline eine Aktion durchführst (z.B. Auftrag erledigen):
1. Aktion wird lokal gespeichert (Queue)
2. Beim nächsten Verbindungsaufbau wird die Aktion automatisch synchronisiert
3. Falls ein Konflikt entsteht: Toast-Benachrichtigung → manuelle Entscheidung

## Touch-Optimierungen

Die App ist für Touch-Bedienung optimiert:
- Alle Buttons mindestens 44×44 Pixel
- Drag&Drop auf Touch-Geräten unterstützt (SortableJS)
- Responsive: auf Tablet horizontal, auf Smartphone vertikal gestapelt

## Auf Tablets (empfohlen für Einsatzleitung)

Empfohlene Gerätegröße: **10 Zoll oder größer** für das vollständige Kanban-Board.

Auf Smartphones wird das Board vertikal gestapelt mit kollabierbaren Spalten-Headern.

## Bildschirmhelligkeit

Bei Außeneinsätzen (Sonneneinstrahlung): Helligkeit auf Maximum. Die Farbgestaltung mit hoher Sättigung und dunklem Hintergrund ist für 200 Lux Sonneneinstrahlung auf einem Tablet lesbar.
