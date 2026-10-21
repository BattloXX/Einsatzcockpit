# Einsatzcockpit

Digitales Einsatzleiter-Werkzeug für österreichische Feuerwehren: Einsatzführung in Echtzeit auf
mehreren Geräten, Großschadenslage nach SKKM und die Module, die man rund um den Einsatz braucht.

**Neu hier?** Je nach Aufgabe:

| Ich möchte … | Hier anfangen |
|---|---|
| … im Einsatz damit arbeiten | [Erste Schritte](Anwender-Erste-Schritte) → [Einsatz starten](Anwender-Einsatz-starten) → [Einsatzboard bedienen](Anwender-Kanban-Board-bedienen) |
| … am Handy oder Tablet arbeiten | [Mobile Nutzung / PWA](Anwender-Mobile-Nutzung-PWA) oder [Native Android-App](Anwender-Native-Android-App) |
| … eine Großschadenslage führen | [Großschadenslage](Anwender-Grosschadenslage) |
| … die eigene Organisation einrichten | [Erst-Setup](Installation-Erst-Setup) → [Einstellungen](Administration-Einstellungen) → [Benutzer und Rollen](Administration-Benutzer-und-Rollen) |
| … einen eigenen Server betreiben | [Server-Voraussetzungen](Installation-Server-Voraussetzungen) |
| … einen Fehler melden oder etwas vorschlagen | [Feedback & Support](Feedback-und-Support) |

Die Startseite ist so aufgebaut wie das Menü im Einsatzcockpit: zuerst der Einsatz selbst, dann die
Module in derselben Reihenfolge wie unter **Module ▾**, danach Verwaltung, Installation und Entwicklung.

---

## 1. Einsatz führen

Was im laufenden Einsatz passiert, von der Alarmierung bis zum Abschluss.

| Seite | Worum es geht |
|---|---|
| [Einsatz starten](Anwender-Einsatz-starten) | Manuell oder automatisch über Alarmierungssystem und Leitstelle |
| [Einsatzinfo](Anwender-Einsatzinfo) | Alarmdaten, Anfahrt, Hydranten, Objektunterlagen und Gefahren auf einen Blick |
| [Einsatzboard bedienen](Anwender-Kanban-Board-bedienen) | Spalten, Fahrzeugkarten, Drag & Drop, Status-Ampel |
| [Aufträge und Meldungen](Anwender-Auftraege-und-Meldungen) | Anlegen, zuteilen, erledigen, Sprachdiktat |
| [Personen erfassen](Anwender-Personen-erfassen) | Betroffene Personen im 4-Stufen-Assistenten |
| [Atemschutzüberwachung](Anwender-Atemschutzueberwachung) | Trupps, Drücke, Warnungen, Rückzugsdruck |
| [Lageführung](Anwender-Lagefuehrung) | Einsatzbezogene Lagekarte: Auto-Layer, taktische Zeichen, Replay, Druck & PDF-Lagebericht |
| [Taktische Lagekarte (ÖBFV E-27)](Anwender-Taktische-Lagekarte) | Genormte Symbole, Magnetfarben, Legende |
| [Lagekarte.info](Anwender-Lagekarte) | Adresse, Koordinaten und Live-Fahrzeuge auf lagekarte.info |
| [Wetter](Anwender-Wetter) | Nowcast, Vorhersage, Unwetterwarnungen, Radar |
| [Zentrale Suche](Anwender-Suche) | Einsätze, Objekte, Kontakte, Pläne und mehr in einem Suchfeld |
| [QR-Code-Schnellzugriff](Anwender-QR-Code-Schnellzugriff) | Zweites Gerät per Scan in den Einsatz holen |
| [Archiv und PDF-Export](Anwender-Archiv-und-PDF-Export) | Einsatz abschließen, Bericht drucken, später nachlesen |
| [Übungsmodus](Anwender-Uebungsmodus) | Was bei Übungseinsätzen anders ist |

## 2. Module

Gleiche Gruppen und Reihenfolge wie im Menü **Module ▾**. Welche Module sichtbar sind, hängt davon ab,
was die Organisation aktiviert hat und welche Rollen man hat. Die Spalte **Einrichtung** richtet sich an
Administratoren.

### Im Einsatz

Was man während des Einsatzes nachschlägt.

| Modul | Bedienung | Einrichtung | Worum es geht |
|---|---|---|---|
| Einsatz- & Katastrophenpläne | [Anleitung](Anwender-Katastrophenplaene) | [Admin](Administration-Katastrophenplaene) | Alarm- und Einsatz-, Maßnahmen- und Katastrophenpläne mit Akteuren, Prüfzyklus, Karte, Suche, Quellen und Offline-PDF |
| Nachschlagewerke | [Anleitung](Anwender-Nachschlagewerke) | [Admin](Administration-Nachschlagewerke) | Gefahrgut nach UN-Nummer oder Stoff, Rettungsdatenblätter, Evakuierungsradius und Ausbreitung, offlinefähig |
| Kontakte | [Anleitung](Anwender-Kontaktverwaltung) | [Admin](Administration-Kontaktverwaltung) | Zentrale Kontakte, Zuordnung zu Objekten, Einsatzinfo-Freigabe, Import/Export |
| Straßensperren | [Anleitung](Anwender-Strassensperren) | [Admin](Administration-Strassensperren) | Sperren auf der Karte pflegen und die Anfahrt im Einsatz prüfen |
| Förderstrecke | [Anleitung](Anwender-Foerderstrecken-Planer) | [Admin](Administration-Foerderstrecken-Planer) | Wasserförderung über lange Wegstrecke berechnen, Sollwerte für Maschinisten, PDF |

### Einsatzbegleitend

Was mit oder direkt nach dem Einsatz zu erledigen ist.

| Modul | Bedienung | Einrichtung | Worum es geht |
|---|---|---|---|
| Fahrtenbuch | [Anleitung](Anwender-Fahrtenbuch) | [Admin](Administration-Fahrtenbuch) | Fahrten per QR oder Token erfassen: Fahrzeug, Maschinist, km/BH, Seilwinde |
| Atemschutz-Geräteprüfung | [Anleitung](Anwender-Atemschutz-Geraetepruefung) | [Admin](Administration-Atemschutz-Geraetepruefung) | Geräte nach Einsatz oder Übung prüfen und Defekte melden |
| Drohne / UAS | [Anleitung](Anwender-Drohne-UAS) | [Admin](Administration-Drohne-UAS) | Flugbuch, Checklisten, Notfall und PDF nach RL-UAS |

### Verwaltung & Planung

| Modul | Bedienung | Einrichtung | Worum es geht |
|---|---|---|---|
| Objekte | [Anleitung](Anwender-Objekte) | [Admin](Administration-Objektverwaltung) | Einsatzunterlagen, Gefahren, Melderpläne mit Volltextsuche, Objektblatt |
| Externe Objektpflege | [Anleitung](Anwender-Objektpflege) | [Admin](Administration-Objektpflege) | Objektverantwortliche ohne Login zur Prüfung ihrer Daten einladen |
| Probenplan | [Anleitung](Anwender-Probenplan) | – | Jahresplanung, Teilnehmerappell, Nachbereitung, öffentlicher Kalender |
| Planspiele | [Anleitung](Anwender-Planspiele) | [Admin](Administration-Planspiele) | Übungseinsätze und Unwetter-GSL erzeugen, allein oder mit Regie üben |
| Mailing | [Anleitung](Anwender-Mailing-Kampagnen) | [Admin](Administration-Mailing-Modul) | Vorlagen, Empfängerlisten, Kampagnen und Zustellstatistik |

### Auswertung

| Modul | Bedienung | Einrichtung | Worum es geht |
|---|---|---|---|
| Statistik | [Anleitung](Administration-Statistik-Dashboard) | – | Kennzahlen und Diagramme zu den Einsätzen |

## 3. Großschadenslage

Führung mehrerer Einsatzstellen nach SKKM, in der Reihenfolge, in der man sie im Einsatz braucht.

| Seite | Worum es geht |
|---|---|
| [Großschadenslage](Anwender-Grosschadenslage) | Überblick: Phasen-Board, Einsatzstellen, Regelkreis Lage → Auftrag → Kontrolle |
| [GSL-Meldungseingang](Anwender-GSL-Meldungseingang) | Meldungen per Portal, QR, SMS und Mail, Übernahme als Einsatzstelle |
| [GSL-Ressourcenverwaltung](Anwender-GSL-Ressourcenverwaltung) | Einheiten anlegen, disponieren, Zugänge verteilen |
| [GSL-Einheitenansicht](Anwender-GSL-Einheitenansicht) | Was die Einheit auf Tablet oder GK-Handy sieht: Aufträge, Karte, Meldungen, Offline-Betrieb |
| [Lagekarte der Großschadenslage](Anwender-Grosschadenslage-Karte) | Interaktive Karte, Polygone, Druck |
| [GSL-Sektoren und Planung](Anwender-GSL-Sektoren-und-Planung) | Abschnitte, automatische Zuordnung, Planungsübersicht |
| [GSL-Stab und Journal](Anwender-GSL-Stab-und-Journal) | SKKM-Stab, Stabstafel, Einsatz- und Funkjournal, Bericht |
| [Übergreifende Meldungen](Anwender-Uebergreifende-Meldungen) | Lageweite Meldungen mit Status, Medien und Karte |
| [Geräteverleih](Anwender-Geraeteverleih) | Material ausgeben und zurücknehmen, auch an der Einsatzstelle belassenes Material |

Einrichtung: [GSL-Einstellungen](Administration-GSL-Einstellungen) · [Geräteverleih (Admin)](Administration-Geraeteverleih)

## 4. Unterwegs, Benachrichtigungen und KI

| Seite | Worum es geht |
|---|---|
| [Mobile Nutzung / PWA](Anwender-Mobile-Nutzung-PWA) | Installieren, Bedienung am Handy, Offline-Verhalten, Widgets |
| [Native Android-App](Anwender-Native-Android-App) | Sideload-APK, Live-Einsatzstatus, Widgets, Offline-Daten für Objekte und Kontakte |
| [Push-Benachrichtigungen](Anwender-Push-Benachrichtigungen) | Auf Handy und PC aktivieren |
| [MCP-Assistent](Anwender-MCP-Assistent) | KI-Anwendung wie Claude oder ChatGPT sicher mit dem Einsatzcockpit verbinden |

## 5. Administration

Für Org-Administratoren. Die Einrichtung der einzelnen Module steht oben bei den [Modulen](#2-module).

**Grundeinrichtung**

| Seite | Worum es geht |
|---|---|
| [Einstellungen](Administration-Einstellungen) | Org-Stammdaten, Logo, Module ein/aus, Auto-Schließen |
| [Benutzer und Rollen](Administration-Benutzer-und-Rollen) | Benutzer anlegen und einladen, Rollen, Sperren |
| [Stammdaten pflegen](Administration-Stammdaten-pflegen) | Fahrzeuge, Alarmtypen, Auftragsvorschläge |
| [Mannschaftsregister](Anwender-Mannschaftsregister) | Mitglieder und Qualifikationen |
| [Single Sign-On (Entra ID)](Administration-Single-Sign-On) | Microsoft-365-Login, Gruppen-Mapping |
| [Organisationen verwalten](Administration-Organisations-verwalten) | Mehrere Organisationen, Seed-Profile, System-Konsole |

**Alarmierung und Leitstelle**

| Seite | Worum es geht |
|---|---|
| [LIS/IPR-Anbindung](Administration-LIS-Anbindung) | Leitstellensystem: Einsatz- und Fahrzeugabgleich, Diagnose |
| [DIBOS-/Elvis-Anbindung](Administration-DIBOS-Elvis-Anbindung) | EventHub der Landeswarnzentrale: Einsätze, Wachenstatus, Zu-/Absagen |
| [Print & Alarm Gateway](Administration-Print-Alarm-Gateway) | Serieller Leitstellenalarm und Druckregeln je Alarmstufe |
| [SMS-Einsatzinfo & Empfang](Administration-SMS-Einsatzinfo) | Alarm-SMS-Verteiler, Weiterleitungsregeln für eingehende SMS |
| [SMS-Versandweg](Administration-SMS-Versandweg) | Primärer SMS-Provider und Fallback |
| [Teams-Alarmierung](Administration-Teams-Alarmierung) | Alarmkarte per Webhook, optional Bot mit Zu-/Absage |
| [Mail-Versand (SMTP / Office 365)](Administration-Mail-Versand) | Eigener Mailserver je Organisation, Fallback-Kette |
| [Alarm-Infoscreen](Administration-Alarm-Infoscreen) | Wandmonitor mit URL-Rotation, Wetter und Rückmeldungen |

**Wetter**

| Seite | Worum es geht |
|---|---|
| [Wetterwarnungen](Administration-Wetterwarnungen) | Schwellwerte und Benachrichtigung per Mail oder Teams |
| [Lokale Wetterstation](Administration-Wetterstation) | Davis/Meteobridge anbinden |

**Schnittstellen**

| Seite | Worum es geht |
|---|---|
| [API-Keys verwalten](Administration-API-Keys-verwalten) | Anlegen, rotieren, sperren |
| [Einsatz-Feed](Administration-Einsatz-Feed) | Einsätze für externe Systeme bereitstellen |
| [Nachrichten-API](Administration-Nachrichten-API) | SMS und Mail von außen versenden |
| [MCP-Server](Administration-MCP-Server) | KI-Anwendungen freischalten, OAuth, Sicherheit |
| [WordPress-Berichte](Administration-WordPress-Berichte) | Beitragsentwurf im Wehr-Blog beim Einsatzabschluss |
| [Push mit Firebase Cloud Messaging](Administration-Push-FCM) | Push an die native Android-App |
| [Zentrale Suche](Administration-Suche) | Suchindex, Neuaufbau, Fehlersuche |

**Kontrolle und Sicherung**

| Seite | Worum es geht |
|---|---|
| [Audit-Log und Zeitreise](Administration-Audit-Log-und-Zeitreise) | Änderungen nachvollziehen, früheren Stand ansehen |
| [Systemstatus & Überwachung](Administration-Dienstueberwachung) | Dienstüberwachung, Benachrichtigungen, Health-Endpunkte |
| [Datensicherung (Org)](Administration-Org-Datensicherung) | Eigene Daten herunterladen oder geplant sichern |

## 6. Installation und Betrieb

Für den Betrieb eines eigenen Servers, in der Reihenfolge der Einrichtung. CloudPanel ist der
Standardweg, ohne Panel geht die manuelle Debian-Installation, containerisiert Docker Compose.

| Seite | Worum es geht |
|---|---|
| [Server-Voraussetzungen](Installation-Server-Voraussetzungen) | Hardware, Ports, Pakete und Wahl des Installationswegs |
| [Datenbank-Einrichtung](Installation-Datenbank-Einrichtung) | MariaDB, Benutzer, Zeichensatz |
| [App-Installation](Installation-App-Installation) | Klonen, venv, `.env`, Migrationen, Seed |
| [Debian/Ubuntu manuell](Installation-Debian-Manuell) | Ohne CloudPanel mit systemd, NGINX und Certbot |
| [Docker Compose](Installation-Docker) | App, MariaDB und Redis als Container |
| [Systemd-Service](Installation-Systemd-Service) | Dienst einrichten, starten, Logs |
| [NGINX-Reverse-Proxy](Installation-NGINX-Reverse-Proxy) | Vhost, WebSocket-Upgrade, TLS |
| [Erst-Setup](Installation-Erst-Setup) | Admin-Benutzer, API-Key, Stammdaten prüfen |
| [Updates](Installation-Updates) | Aktualisieren, Migrationen, Neustart |
| [Backups](Installation-Backups) | Datenbank-Dumps, Audit-Log-Sicherung |
| [Backup & Disaster-Recovery](Betrieb-Backup-und-Disaster-Recovery) | Automatische Dumps, Restore-Probe, Runbook |
| [SMS-Gateway](Installation-SMS-Gateway) | Android-Gateway-App einrichten |
| [Print & Alarm Gateway](Installation-Print-Alarm-Gateway) | Lokalen Container koppeln, Alarmleitung, Drucker |
| [Cloudflare Turnstile](Installation-Cloudflare-Turnstile) | Spam-Schutz für das Kontaktformular |
| [Troubleshooting](Installation-Troubleshooting) | Häufige Fehler und Lösungen |

## 7. Entwicklung

| Seite | Worum es geht |
|---|---|
| [Lokale Entwicklung](Entwickler-Lokale-Entwicklung) | Starten, Datenbank, CSS-Build |
| [Beitragen](Entwickler-Beitragen) | Branches, Pull Requests, Commits |
| [Architektur](Entwickler-Architektur) | Schichten, Datenfluss, Multi-Tenancy |
| [Datenmodell](Entwickler-Datenmodell) | Tabellen und Beziehungen |
| [Sicherheit](Entwickler-Sicherheit) | Authentifizierung, CSRF, Tenant-Isolation, Rate-Limiting |
| [Tests](Entwickler-Tests) | pytest, Fixtures, CI |
| [Icons](Entwicklung-Icons) | Icon-System, Statuspunkte, Vendoring |
| [REST-API](Entwickler-REST-API) | Endpunkte, Validierung, Beispiele |
| [WebSocket-Events](Entwickler-WebSocket-Events) | Event-Typen |
| [Einsatz-Feed-Schema](Entwickler-Einsatz-Feed) | Pull-Endpunkte, Felder, ETag |
| [MCP-Erweiterung](Entwickler-MCP-Erweiterung) | OAuth-Server, Tool-Registry, Tests |
| [Zentrale Suche](Entwickler-Suche) | Neue Suchquellen anbinden |
| [Einsatz- & Katastrophenpläne](Entwickler-Katastrophenplaene) | Datenmodell, Freigaben, Prüfzyklus, Offline-PDF |
| [Straßensperren](Entwickler-Strassensperren) | Geometrie und Routing-Provider |
| [Planspiele](Entwickler-Planspiele) | Übungsuhr, Worker, Generator |

---

[Feedback & Support](Feedback-und-Support) · [Repository](https://github.com/BattloXX/Einsatzcockpit) ·
[Issues](https://github.com/BattloXX/Einsatzcockpit/issues) · [Releases](https://github.com/BattloXX/Einsatzcockpit/releases) ·
[Feuerwehr Wolfurt](https://www.feuerwehr-wolfurt.at)
