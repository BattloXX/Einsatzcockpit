# Native Android-App

← [Zurück zur Startseite](Home)

Neben der [PWA](Anwender-Mobile-Nutzung-PWA) gibt es eine eigenständige **native Android-App** —
ein schlanker Capacitor-Wrapper um dieselbe Webapp, der um native Android-Funktionen ergänzt
wird, die im Browser nicht zuverlässig funktionieren: zuverlässige Push-Benachrichtigungen,
ein echter Offline-Datenbestand für Objekte und Kontakte, Homescreen-Widgets und optional der
SMS-Gateway-Modus.

Quellcode: [`BattloXX/Einsatzcockpit-Android`](https://github.com/BattloXX/Einsatzcockpit-Android)
auf GitHub.

> Die Web-App, das Dashboard und alle Browser-Nutzer funktionieren unverändert weiter — die
> native App ist ein optionaler zusätzlicher Client für den Einsatzbetrieb, kein Ersatz.

---

## Download & Installation

Es gibt **kein Play-Store-Listing** — die App wird als signierte APK direkt installiert
(„Sideload"):

1. Aktuelle APK von [GitHub Releases](https://github.com/BattloXX/Einsatzcockpit-Android/releases)
   auf das Android-Gerät laden (USB, E-Mail, Link)
2. **Einstellungen → Sicherheit → Unbekannte Quellen** einmalig erlauben
3. APK antippen → installieren

Die App prüft danach selbst laufend gegen die GitHub Releases und zeigt bei verfügbarem
Update im Nutzer-Menü unter **„Über die App"** einen Download-Button an.

---

## Anmeldung

Beim ersten Start bietet die App vier Anmeldewege:

| Weg | Für wen | Wie |
|-----|---------|-----|
| **QR-Code scannen** | Geräte-Pairing (Tablet, Anzeigegerät, SMS-Gateway) | Admin erzeugt QR-Code unter **Admin → Geräte-Login**, App scannt ihn |
| **PIN eingeben statt scannen** | Geräte-Pairing ohne Kamerazugriff | Admin zeigt zusätzlich zum QR-Code eine PIN (10 Minuten gültig, einmal verwendbar); PIN in der App eintippen |
| **Mit Account anmelden** | Persönliche Accounts | Normale Benutzername/Passwort-Anmeldung, bleibt bis zu 30 Tage aktiv |
| **Per SMS-PIN anmelden** | Persönliche Accounts, passwortlos | Handynummer eingeben → Einmal-PIN per SMS → PIN eintippen. Setzt ein verbundenes SMS-Gateway der eigenen Organisation voraus |

Geräte-Pairing (QR-Code/PIN) erzeugt eine dauerhafte Geräte-Session ohne Ablauf — gedacht für
fest verbaute/gemeinsam genutzte Geräte (Fahrzeug-Tablet, Gerätehaus-Anzeige,
SMS-Gateway-Handy). Die persönliche Anmeldung (Account-Login/SMS-PIN) ist an den einzelnen
Nutzer gebunden und läuft nach spätestens 30 Tagen ab. Nach **Mit Account anmelden** merkt sich
die App das und überspringt den Anmelde-Screen bei künftigen Starts, solange die Session gültig
ist.

Ein Android-Gerät kann gleichzeitig als Einheit-Gerät (zeigt das Einsatz-Board) **und** als
SMS-Gateway laufen — unter **Admin → Geräte-Login → + Gerät registrieren** die Option
„Gerät + SMS-Gateway" wählen. Details zum Gateway-Modus: [SMS-Gateway einrichten](Installation-SMS-Gateway).

---

## Funktionen im Überblick

| Funktion | Nutzen |
|---|---|
| **Zuverlässige Push-Benachrichtigungen** | Firebase Cloud Messaging weckt die App sofort, auch bei geschlossener App — statt auf den System-Default zu warten |
| **Live-Einsatzstatus (Dauerbenachrichtigung)** | Laufender Einsatz mit Stichwort, Adresse, Phase und Chronometer auf Sperrbildschirm/Statusleiste, ohne die App zu öffnen — siehe unten |
| **Alarmton trotz Lautlos/Vibration** | Optionaler eigener Benachrichtigungskanal nur für neue Einsätze, umgeht die Stummschaltung |
| **Dauerhafter Login** | Kein tägliches Neu-Einloggen dank Geräte-Token im Secure Storage |
| **GPS-Standort im Einsatz** | Nur bei aktivem Einsatz, fließt in die Lagekarte ein |
| **Bildschirm aktiv halten** | Für Atemschutzüberwachung/Screensaver-Ansichten |
| **SMS-Gateway-Modus** | Optional: Versand/Empfang über die SIM-Karte des Geräts, siehe [SMS-Gateway einrichten](Installation-SMS-Gateway) |
| **Homescreen-Widgets** | Direktzugriff ohne App zu öffnen — siehe unten |
| **Offline-Datenbestand für Objekte & Kontakte** | Funktioniert im Funkloch weiter — siehe unten |

### Live-Einsatzstatus (Dauerbenachrichtigung)

- **Aktivieren:** Beim Login mit persönlichem Account erscheint die Option „Live-Einsatzstatus
  aktivieren"; nachträglich lässt sie sich in der App an-/abschalten. Geräte-Logins (QR/PIN,
  z. B. Fahrzeug-Tablets) haben den Live-Status automatisch aktiv.
- **Wie es funktioniert:** Eine Push-Nachricht (FCM) weckt die App im Hintergrund sofort, sobald
  ein neuer Einsatz beginnt oder sich der Status ändert; die Benachrichtigung bleibt dann
  aktuell, solange der Einsatz läuft.
- **Kein Dauerbetrieb:** Die App hält dafür **nicht permanent** einen Hintergrunddienst am
  Laufen. Ohne aktiven Einsatz beendet sich der Hintergrunddienst nach spätestens 15 Minuten
  Leerlauf von selbst — das schont Akku und vermeidet eine dauerhaft sichtbare
  „App läuft im Hintergrund"-Meldung. Bei einem neuen Einsatz startet er automatisch wieder.
- **Deaktivieren:** In der App unter dem Live-Status-Schalter ausschalten, oder beim Abmelden —
  Dauerbenachrichtigung und Hintergrunddienst werden dann sofort beendet.

### Homescreen-Widgets

Vier Widgets für den Direktzugriff (wie bei jeder Android-App: langer Druck auf den Homescreen
→ **Widgets** hinzufügen):

| Widget | Zeigt | Öffnet |
|--------|-------|--------|
| **Kontakte** | Fixer Shortcut | Kontaktliste |
| **Objekte** | Fixer Shortcut | Objektliste |
| **Einsatzstatus** | Laufenden Einsatz (Stichwort, Adresse, Phase) oder „Kein aktiver Einsatz"; in der großen Darstellung zusätzlich eine Kartenvorschau | Laufenden Einsatz bzw. die Startseite |
| **Fahrt erfassen** | „Fahrt erfassen", optional das Fahrzeug-Kurzzeichen als zweite Zeile | Das [Fahrtenbuch-Erfassungsformular](Anwender-Fahrtenbuch) |

Alle Widgets nutzen die bestehende Anmeldung der App — kein separater Login nötig. Beim
Widget „Fahrt erfassen" lässt sich optional ein Fahrzeug fest hinterlegen; ist das Gerät per
Geräte-Login bereits einem Fahrzeug zugeordnet, gewinnt immer dieses Fahrzeug vor der
Widget-Konfiguration.

---

## Offline-Funktionalität

Die native App geht deutlich über den Offline-Cache der PWA hinaus und hält ganze
Datenbestände aktiv im Hintergrund aktuell — gedacht für den Klassiker „Fahrzeug im Funkloch":

- **Objekte offline:** Freigegebene Objekte (Stammdaten, Gefahren, BMA/Schlüssel, Pläne, PDFs)
  werden alle 6 Stunden automatisch heruntergeladen und bleiben auch ohne Netzverbindung
  vollständig abrufbar, inklusive der Detailseiten-Unterabschnitte.
- **Kontakte offline:** Alle Kontakte (Telefonnummern, E-Mail, Objektzuordnungen)
  synchronisieren in eine eigene, von der Web-Ansicht unabhängige Datenbank auf dem Gerät —
  erreichbar über einen eigenen App-Shortcut „Kontakte offline" (langes Drücken auf das
  App-Icon), inklusive Live-Suche, Direktanruf und SMS auch ganz ohne Netz.
- **Offline-Start:** Die App erkennt einen fehlenden Netzzugang beim Start und springt direkt
  zur zuletzt zwischengespeicherten Startseite, statt an einer netzwerkabhängigen
  Anmelde-Weiterleitung hängen zu bleiben.
- **Einsatz-Vorladen:** Wird ein neuer Einsatz per Push gemeldet, lädt die App die Einsatzseite
  im Hintergrund einmal still vor, damit sie offline aktuell verfügbar ist, auch wenn man sie
  nach dem Alarm nicht sofort öffnet.

Beide Offline-Bestände (Objekte, Kontakte) laufen unabhängig von einer geöffneten App — der
Abgleich passiert automatisch im Hintergrund, auch auf einem reinen SMS-Gateway-Gerät.

Ist das Gerät wieder online, gelten für Änderungen dieselben Regeln wie in der
[PWA](Anwender-Mobile-Nutzung-PWA#offline-queue-ausstehende-aktionen): lokal gepufferte
Aktionen synchronisieren automatisch, Konflikte lösen sich per Toast-Benachrichtigung manuell.

---

## Verwandt

- [Mobile Nutzung / PWA](Anwender-Mobile-Nutzung-PWA) — Installation als Web-App auf
  iOS/Android/Desktop, allgemeines Offline-Verhalten
- [SMS-Gateway einrichten](Installation-SMS-Gateway) — dasselbe Gerät zusätzlich als
  SMS-Versand-/Empfangsweg einrichten
- [Push mit Firebase Cloud Messaging](Administration-Push-FCM) — globale FCM-Konfiguration für
  Push-Nachrichten an die App
- [GitHub-Repository `Einsatzcockpit-Android`](https://github.com/BattloXX/Einsatzcockpit-Android) —
  Quellcode, Releases, Issues
