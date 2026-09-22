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

Nach **Mit Account anmelden** merkt sich die App das und überspringt den Anmelde-Screen bei künftigen App-Starts, solange die Web-Session noch gültig ist — die App landet dann direkt auf dem Einsatz-Board. Ist die Session abgelaufen, erscheint automatisch die normale Login-Seite.

### Gerät + SMS-Gateway kombinieren

Ein Android-Gerät kann gleichzeitig als Einheit-Gerät (zeigt das Einsatz-Board) **und** als
SMS-Gateway (sendet/empfängt SMS über die eingebaute SIM) laufen — z. B. ein fest verbautes
Fahrzeug-Tablet mit SIM-Karte. Unter **Admin → Geräte-Login → + Gerät registrieren** die Option
„Gerät + SMS-Gateway" wählen: es wird ein einziger QR-Code erzeugt, der beide Rollen koppelt. Nach
dem Scan zeigt die App das Einsatz-Board als Hauptansicht; der SMS-Gateway-Dienst läuft im
Hintergrund weiter (erkennbar an der dauerhaften Benachrichtigung „SMS-Gateway aktiv").

### Über die App

Im Nutzer-Menü (Profil-Dropdown bzw. mobiles Menü) erscheint innerhalb der nativen Android-App
der Eintrag **„Über die App"**. Dort werden die installierte und die aktuell verfügbare
App-Version angezeigt, ein Update kann direkt heruntergeladen werden (wie beim SMS-Gateway-Status),
und ist dieses Gerät als SMS-Gateway konfiguriert, führt ein Button direkt zum
SMS-Gateway-Status. Der Eintrag ist nur innerhalb der App sichtbar, nicht im Browser/PWA.

### Live-Einsatzstatus (Dauerbenachrichtigung)

Die native Android-App kann laufende Einsätze zusätzlich als **Dauerbenachrichtigung** auf
Sperrbildschirm und in der Statusleiste anzeigen — mit Stichwort, Adresse, Phase und
Einsatzdauer (Chronometer), auch wenn die App gerade nicht geöffnet ist.

- **Aktivieren:** Beim Login mit persönlichem Account erscheint die Option „Live-Einsatzstatus
  aktivieren"; nachträglich lässt sie sich in der App an-/abschalten. Geräte-Logins (QR/PIN,
  z. B. Fahrzeug-Tablets) haben den Live-Status automatisch aktiv.
- **Wie es funktioniert:** Eine Push-Nachricht (FCM) weckt die App im Hintergrund sofort, sobald
  ein neuer Einsatz beginnt oder sich der Status ändert; die Benachrichtigung bleibt dann
  aktuell, solange der Einsatz läuft.
- **Kein Dauerbetrieb:** Anders als früher hält die App dafür **nicht mehr permanent** einen
  Hintergrunddienst am Laufen. Ohne aktiven Einsatz (und ohne Dienst) beendet sich der
  Hintergrunddienst nach spätestens 15 Minuten Leerlauf von selbst — das schont Akku und
  vermeidet eine dauerhaft sichtbare „App läuft im Hintergrund"-Meldung. Bei einem neuen
  Einsatz startet er automatisch wieder.
- **Deaktivieren:** In der App unter dem Live-Status-Schalter ausschalten, oder beim Abmelden
  — die Dauerbenachrichtigung und der Hintergrunddienst werden dann sofort beendet.

## Widgets auf dem Homescreen

Die native Android-App bietet vier Homescreen-Widgets für den Direktzugriff, ohne die App
erst zu öffnen (wie bei jeder Android-App per langem Druck auf den Homescreen → **Widgets**
hinzufügen):

| Widget | Zeigt | Öffnet |
|--------|-------|--------|
| **Kontakte** | Fixer Shortcut | Kontaktliste |
| **Objekte** | Fixer Shortcut | Objektliste |
| **Einsatzstatus** | Laufenden Einsatz (Stichwort, Adresse, Phase) oder „Kein aktiver Einsatz"; in der großen Darstellung zusätzlich eine Kartenvorschau | Laufenden Einsatz bzw. die Startseite |
| **Fahrt erfassen** | „Fahrt erfassen", optional das Fahrzeug-Kurzzeichen als zweite Zeile | Das [Fahrtenbuch-Erfassungsformular](Anwender-Fahrtenbuch) |

Alle Widgets nutzen die bestehende Anmeldung der App — kein separater Login nötig.

### Fahrt erfassen: welches Fahrzeug wird vorausgewählt?

Beim Hinzufügen des Widgets „Fahrt erfassen" lässt sich optional ein Fahrzeug fest
hinterlegen. Welches Fahrzeug beim Öffnen tatsächlich vorausgewählt ist, folgt dieser
Reihenfolge:

1. **Geräte-Login-Fahrzeug** — ist dieses Gerät per QR/PIN fest mit einem Fahrzeug
   verknüpft (z. B. ein fest verbautes Fahrzeug-Tablet), gewinnt immer dieses Fahrzeug.
2. **Widget-Konfiguration** — nur wenn das Gerät selbst keinem Fahrzeug zugeordnet ist.
3. **Manuelle Auswahl** — ist auch im Widget kein Fahrzeug hinterlegt, erscheint die
   normale Fahrzeugauswahl im Formular.

Ist ein Fahrzeug per Geräte-Login oder Widget-Konfiguration bekannt, zeigt das Widget
dessen Kurzzeichen als zweite Zeile an (z. B. „RLF").

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

Die PWA (Browser bzw. installierte Web-App) cached folgende Inhalte für Offline-Nutzung:
- Login-Seite (Kein Zugriff ohne vorherigen Login möglich)
- CSS, JavaScript, Icons (App lädt schneller)
- Zuletzt geöffneter Einsatz (read-only)

**Was offline NICHT funktioniert:**
- Änderungen speichern (werden in Queue gepuffert)
- Neue Einsätze sehen
- Echtzeit-Sync

### Offline-Funktionen der nativen Android-App

Die native Android-App geht darüber hinaus deutlich weiter und hält ganze Datenbestände
aktiv im Hintergrund aktuell — gedacht für den Klassiker „Fahrzeug im Funkloch":

- **Objekte offline:** Freigegebene Objekte (Stammdaten, Gefahren, BMA/Schlüssel, Pläne,
  PDFs) werden alle 6 Stunden automatisch heruntergeladen und bleiben auch ohne
  Netzverbindung vollständig abrufbar, inklusive der Detailseiten-Unterabschnitte.
- **Kontakte offline:** Alle Kontakte (Telefonnummern, E-Mail, Objektzuordnungen)
  synchronisieren in eine eigene, von der Web-Ansicht unabhängige Datenbank auf dem
  Gerät — erreichbar über einen eigenen App-Shortcut „Kontakte offline" (langes Drücken
  auf das App-Icon), inklusive Live-Suche, Direktanruf und SMS auch ganz ohne Netz.
- **Offline-Start:** Die App erkennt einen fehlenden Netzzugang beim Start und springt
  direkt zur zuletzt zwischengespeicherten Startseite, statt an einer
  netzwerkabhängigen Anmelde-Weiterleitung hängen zu bleiben.
- **Einsatz-Vorladen:** Wird ein neuer Einsatz per Push gemeldet, lädt die App die
  Einsatzseite im Hintergrund einmal still vor, damit sie offline aktuell verfügbar
  ist, auch wenn man sie nach dem Alarm nicht sofort öffnet.

Beide Offline-Bestände (Objekte, Kontakte) laufen unabhängig von einer geöffneten App —
der Abgleich passiert automatisch im Hintergrund, auch auf einem reinen SMS-Gateway-Gerät.

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
