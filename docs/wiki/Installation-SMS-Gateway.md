# SMS-Gateway einrichten (Android-App)

← [Zurück zur Startseite](Home)

Der SMS-Versand/-Empfang läuft über die native **Einsatzcockpit-Android-App** (Repo [`BattloXX/Einsatzcockpit-Android`](https://github.com/BattloXX/Einsatzcockpit-Android)) auf einem beliebigen Android-Gerät mit SIM-Karte. Die App verbindet sich **ausgehend** per WebSocket mit der Haupt-App und versendet/empfängt SMS über die eingebaute `SmsManager`-API — kein separater Server, kein Modem, keine offenen Ports nötig.

**Zweck:** SMS-Versand/-Empfang für die Telefonnummern-Verifizierung im Bürgerportal, [Einsatzinfo-SMS bei Alarm](Administration-SMS-Einsatzinfo), manuellen Versand an Gruppen/Mitglieder und die Weiterleitung eingehender SMS (Teams-Webhook, Gruppen, Mitglieder, Ad-hoc-Nummern).

---

## Voraussetzungen

| Anforderung | Details |
|---|---|
| Android-Gerät mit SIM-Karte | Bleibt dauerhaft eingesteckt/geladen am Standort (z.B. altes Diensthandy im Gerätehaus) |
| Einsatzcockpit-Android-APK | Sideload-APK aus [GitHub Releases](https://github.com/BattloXX/Einsatzcockpit-Android/releases) (kein Play Store nötig) |
| Rolle `admin` | Zum Anlegen des SMS-Gateway-Geräts unter **Admin → Geräte-Login** |
| WLAN oder Mobilfunk am Gerätestandort | Die App hält eine dauerhafte WebSocket-Verbindung zur Haupt-App |

---

## Schritt 1 — App installieren

1. Aktuelle APK von [Releases](https://github.com/BattloXX/Einsatzcockpit-Android/releases) auf das Android-Gerät laden (USB, E-Mail, Link)
2. **Einstellungen → Sicherheit → Unbekannte Quellen** erlauben (einmalig)
3. APK antippen → installieren

---

## Schritt 2 — SMS-Gateway-Gerät anlegen (Admin-UI)

Unter **Admin → Geräte-Login** (`/admin/geraete-login`):

1. **+ Gerät registrieren**
2. Gerätetyp: **📡 SMS-Gateway** auswählen (statt „Einheit-Gerät")
3. Bezeichnung eingeben (z.B. `SMS-Gateway Gerätehaus`)
4. **Erstellen** — der QR-Code wird angezeigt und enthält den Verbindungs-Token; er wird **nur einmal** angezeigt

---

## Schritt 3 — QR-Code mit der App scannen

1. Einsatzcockpit-Android-App auf dem Gateway-Gerät öffnen
2. **QR-Code scannen** antippen und den QR-Code aus Schritt 2 einscannen
3. Die App erkennt automatisch den SMS-Gateway-Modus und wechselt in den Gateway-Bildschirm
4. **Akku-Optimierung deaktivieren**, sobald die App danach fragt (siehe unten — zwingend für Dauerbetrieb)

Der Gateway-Bildschirm zeigt danach laufend: Verbindungsstatus, Server, Token (maskiert), die letzten gesendeten SMS, empfangene SMS (falls SMS-Empfang aktiviert ist) sowie ein Verbindungslog zur Fehlersuche.

---

## Akku-Optimierung deaktivieren (zwingend für 24/7-Betrieb)

Ohne Ausnahme kappt Android (Doze-Modus) die Gateway-Verbindung, sobald das Gerät länger ruht — SMS würden dann nicht zugestellt. Die App fordert das direkt beim ersten Start an; manuell geht es über:

**Einstellungen → Apps → Einsatzcockpit → Akku → „Nicht eingeschränkt" / „Keine Einschränkungen"**

Bei manchen Herstellern reicht das allein nicht aus:

| Hersteller | Zusätzlich erforderlich |
|---|---|
| Xiaomi/Redmi (MIUI/HyperOS) | „Autostart" aktivieren; Akku → „Keine Einschränkungen"; App in den Recent-Apps „sperren" (Schloss-Symbol) |
| Huawei/Honor (EMUI) | App-Start → „Manuell verwalten" → Autostart/Hintergrund/Wecken erlauben |
| Samsung (One UI) | Akku → „Apps in den Ruhezustand versetzen" → App ausschließen; „Nicht überwachte Apps" |
| OnePlus/Oppo/Realme (ColorOS) | Akku → „Hintergrundaktivität zulassen"; Autostart an |

Übersicht je Hersteller: https://dontkillmyapp.com/

Zusätzlich empfohlen: Gerät dauerhaft am Strom lassen und ein zuverlässiges Netz (WLAN/Mobil) sicherstellen. Bei Netzwechsel verbindet die App automatisch neu.

---

## SMS-Empfang aktivieren (optional)

Ist [SMS-Empfang](Administration-SMS-Einsatzinfo) für die Organisation serverseitig eingeschaltet, zeigt der Gateway-Bildschirm eine Karte **„SMS-Empfang aktiviert – Berechtigung fehlt"** — dort **SMS-Empfang erlauben** antippen, damit die App die Android-Berechtigung `RECEIVE_SMS` erhält. Ohne diese Berechtigung werden weiterhin SMS versendet, aber keine eingehenden SMS erkannt/weitergeleitet.

---

## Status prüfen

- **In der App**: Gateway-Bildschirm zeigt Verbindungsstatus (grün = verbunden), Anzahl gesendeter SMS und ein Verbindungslog (kopierbar/herunterladbar für die Fehlersuche)
- **Im Admin-Bereich**: **Admin → Geräte-Login** zeigt bei jedem SMS-Gateway-Eintrag den aktuellen Verbindungsstatus; eine Test-SMS lässt sich über **Admin → SMS senden** direkt vom Server aus verschicken

---

## Gerät widerrufen / austauschen

Unter **Admin → Geräte-Login** beim jeweiligen SMS-Gateway-Eintrag **Widerrufen** wählen. Widerrufene Geräte können bei Bedarf wieder reaktiviert werden; für ein Ersatzgerät einfach ein neues SMS-Gateway-Gerät anlegen (Schritt 2) und den neuen QR-Code scannen.

---

## App-Updates

Der Gateway-Bildschirm prüft automatisch gegen die [GitHub Releases](https://github.com/BattloXX/Einsatzcockpit-Android/releases) und zeigt bei verfügbarem Update einen Download-Button an.

---

**Verwandt:** [SMS-Einsatzinfo, manueller Versand & SMS-Empfang](Administration-SMS-Einsatzinfo) — Einrichtung der eigentlichen SMS-Funktionen, sobald das Gateway verbunden ist.

**Nächster Schritt:** [Erst-Setup](Installation-Erst-Setup)
