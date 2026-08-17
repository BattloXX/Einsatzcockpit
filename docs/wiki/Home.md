# Einsatzcockpit

Digitales Einsatzleiter-Werkzeug für österreichische Feuerwehren — Multi-User, Multi-Organisations-fähig, Echtzeit.

**Version:** 2026.08.17 · **Python:** 3.14 · **FastAPI** + HTMX + MariaDB

## Was ist das?

Eine Python-Webapp (FastAPI + HTMX + WebSocket), die ein bisheriges Single-File-HTML-Tool ersetzt und um echte Multi-User-Fähigkeit, Atemschutzüberwachung, Mannschaftsregister, Archiv, PDF-Export, vollständige Multi-Tenancy, Großschadenslage-Führung und Drohnen-Dokumentation erweitert.

### Kernfunktionen
- Echtzeit-Kanban-Board für mehrere Geräte gleichzeitig (WebSockets)
- Automatische Einsatzanlage aus dem Alarmierungssystem (REST-API, idempotent)
- LIS/IPR-Anbindung an das Leitstellensystem: Einsatz-/Übungseinsatzabgleich, Fahrzeugstatus, Meldungen, automatisches Schließen
- SMS-Einsatzinfo bei Alarm sowie SMS-Empfang mit Weiterleitungsregeln (Teams, Gruppen, Mitglieder, Ad-hoc)
- Print & Alarm Gateway: lokaler Docker-Container für seriellen Leitstellen-Alarm (→ Einsatzanlage) und Netzwerkdruck (Automatik-Druckregeln + manuell)
- Teams-Alarmierung: vollständige Alarm-Karte (Kartenbild, Google-Maps-Link, No-Login-Alarmübersicht) bei jeder Einsatzanlage, optional mit Bot-Zusage/-Absage
- WordPress-Berichte: beim Einsatzabschluss automatisch (oder per Button) einen Beitragsentwurf im Wehr-Blog anlegen — idempotent, nie automatisch veröffentlicht
- Gesetzeskonforme Atemschutzüberwachung mit Rückzugsdruckberechnung
- Mannschaftsregister mit Qualifikationen und Ablaufdaten
- Archiv mit vollständigem Audit-Log und PDF-Export
- Multi-Tenancy: mehrere Organisationen, row-level isoliert, gemeinsame Einsätze via Kollaborationsmodell
- Großschadenslage (GSL): Phasen-Kanban, Einsatzstellen, SKKM-Stab, Lagekarte, Ressourcenverwaltung
- SKKM-Lagemeldungs-Regelkreis: Lage → Auftrag → Kontrolle mit Fälligkeits-Timern
- Taktische Lagekarte nach ÖBFV-Richtlinie E-27 (genormte Symbole, Magnetfarben)
- Lageführung: einsatzbezogene Lagekarte mit Auto-Layern (Fahrzeuge, Objekt, Einsatzort), taktischen Zeichen, Multi-User-Editing (Presence/Soft-Locks), Chronologie/Replay, Kartendruck & PDF-Lagebericht
- Wetterdaten-Integration: Nowcast, Vorhersage, Unwetterwarnungen, Radar-Overlay
- UAS/Drohnen-Modul gemäß RL-UAS LFV Vorarlberg 2024 (Flugbuch, Checklisten, PDF, DSGVO)
- Objektverwaltung: Einsatzunterlagen zu BMA-Objekten und Wohnanlagen — Gefahren (mit Links + Gefahrgut-DB-Anreicherung per UN-Nummer), Schlüsselsafe, Melderpläne mit PDF-Zerlegung und **Volltextsuche (OCR)**, Objekt-Lagekarte, Alarm-Matching mit **Objektgefahren-Board-Spalte**, Objektblatt-Druck; pflegbare Kataloge/Auswahllisten und Karten-Symbole (Bild-Upload)
- Alarm-Infoscreen für Wandmonitore: URL-Rotation je Monitor (Matrix), Wetter, Großschadenslage-Sonderansicht, RSVP-Anzeige, dauerhaft kopierbare Monitor-URLs
- Nachschlagewerke: offlinefähige Gefahrgut-Suche (UN-Nummer/Stoffname → ERI-Karte, täglicher BAM/ADR-Sync), Rettungsdatenblätter (on-demand + Cache) und Karten-Overlays (Evakuierungsradius, windbezogene Ausbreitung inkl. Gauß-Modell)
- SSO via Microsoft Entra ID (JIT-Provisioning, Gruppen-Mapping, PKCE/OIDC)
- Mail-Versand je Organisation: eigener SMTP-Server und/oder Office 365 / Microsoft Graph, mit automatischer Fallback-Kette
- Digitales Fahrtenbuch mit QR-/Token-Erfassung, Korrektur- und Storno-Workflow
- Geräteverleih für Großschadenslagen (Artikel, Stücklisten, Barcode-Scan, SMS)
- Organisationsbezogene Datensicherung als Download oder geplanter Push mit tenant-gescoptem Restore
- Lokale Wetterstation mit Push-Ingest, eigener Zeitreihen-Datenbank und Szenario-Analyse
- PWA für Offline-Betrieb, Web-Push-Benachrichtigungen
- QR-Code-Schnellzugriff für zustoßende Einsatzkräfte
- KI-Assistent (Auftragsvorschläge, Lagebild, Auto-Priorisierung) via Anthropic Claude — opt-in
- Datenbank-Backup & Disaster-Recovery: automatisierte Dumps beider DBs + Medien, wöchentlich getestete Restore-Probe, Off-Site-Upload (SFTP/SCP/rsync/FTPS/rclone), DR-Runbook
- Rate-Limiting per IP und API-Key (slowapi)
- Förderstrecken-Planer: Löschwasserförderung über lange Wegstrecke berechnen (Vollbild-Kartenmodus, automatischer Pumpenstandort-Vorschlag), optional mit Einsatz verknüpft (Einsatzort-Marker, eigener Kartenlayer in der Lageführung)

## Inhaltsverzeichnis

### Installation

**Welcher Weg passt zu mir?** CloudPanel bleibt der verwaltete Standardweg. Ohne
Hosting-Panel eignet sich die manuelle Debian/Ubuntu-Installation; für einen
containerisierten Betrieb steht Docker Compose bereit. Die Entscheidungshilfe steht
unter [Server-Voraussetzungen](Installation-Server-Voraussetzungen.md).

| Seite | Beschreibung |
|-------|-------------|
| [Server-Voraussetzungen](Installation-Server-Voraussetzungen.md) | Hardware, Ports, Systempakete und Wahl des Installationswegs |
| [Datenbank-Einrichtung](Installation-Datenbank-Einrichtung.md) | MariaDB anlegen, User und Zeichensatz |
| [App-Installation](Installation-App-Installation.md) | git clone, venv, pip, .env, alembic, seed |
| [Debian/Ubuntu manuell](Installation-Debian-Manuell.md) | Installation ohne CloudPanel mit systemd, NGINX und Certbot |
| [Docker Compose](Installation-Docker.md) | App, MariaDB und Redis containerisiert betreiben |
| [Systemd-Service](Installation-Systemd-Service.md) | Dienst einrichten, starten, Logs |
| [NGINX-Reverse-Proxy](Installation-NGINX-Reverse-Proxy.md) | CloudPanel-Vhost, WebSocket-Upgrade, TLS |
| [Erst-Setup](Installation-Erst-Setup.md) | Admin-User, API-Key, Stammdaten prüfen |
| [Backups](Installation-Backups.md) | Datenbank-Dumps, Audit-Log-Sicherung |
| [Backup & Disaster-Recovery](Betrieb-Backup-und-Disaster-Recovery.md) | Automatische Dumps, getestete Restore-Probe, DR-Runbook (RPO/RTO) |
| [Updates](Installation-Updates.md) | git pull / In-App ZIP-Update, Migrationen, Neustart |
| [SMS-Gateway](Installation-SMS-Gateway.md) | Android-Gateway-App einrichten: APK, Geräte-Login-QR, Akku-Optimierung |
| [Print & Alarm Gateway](Installation-Print-Alarm-Gateway.md) | Lokalen Docker-Container koppeln: Pairing-Code, W&T-Alarmleitung, Netzwerkdrucker |
| [Troubleshooting](Installation-Troubleshooting.md) | Häufige Fehler und Lösungen |

### Anwender
| Seite | Beschreibung |
|-------|-------------|
| [Erste Schritte](Anwender-Erste-Schritte.md) | Login, Übersicht, Tastatur-Shortcuts |
| [Einsatz starten](Anwender-Einsatz-starten.md) | Manuell vs. Automatik über Alarmierungssystem oder LIS/Leitstelle |
| [Kanban-Board bedienen](Anwender-Kanban-Board-bedienen.md) | Spalten, Karten, Drag&Drop, Status-Ampel |
| [Aufträge und Meldungen](Anwender-Auftraege-und-Meldungen.md) | Anlegen, Zuteilen, Erledigen, Sprachdiktat |
| [Personen erfassen](Anwender-Personen-erfassen.md) | 4-Stufen-Wizard |
| [Atemschutzüberwachung](Anwender-Atemschutzueberwachung.md) | Trupp, Drücke, Warnungen, Rückzug |
| [Mannschaftsregister](Anwender-Mannschaftsregister.md) | Mitglieder, Qualifikationen |
| [Archiv und PDF-Export](Anwender-Archiv-und-PDF-Export.md) | Abschließen, Bericht drucken |
| [Übungsmodus](Anwender-Uebungsmodus.md) | Was ist anders, Statistik-Ausschluss |
| [QR-Code Schnellzugriff](Anwender-QR-Code-Schnellzugriff.md) | Zweites Gerät per Scan einbinden |
| [Mobile Nutzung / PWA](Anwender-Mobile-Nutzung-PWA.md) | Installieren, Offline-Verhalten |
| [Push-Benachrichtigungen](Anwender-Push-Benachrichtigungen.md) | Aktivieren auf Handy und PC |
| [Lageführung](Anwender-Lagefuehrung.md) | Einsatzbezogene Lagekarte: Auto-Layer, taktische Zeichen, Multi-User, Chronologie/Replay, Druck & PDF-Lagebericht |
| [Lagekarte.info](Anwender-Lagekarte.md) | Adresse & Koordinaten, Live-Fahrzeuge auf lagekarte.info |
| [Wetter-Integration](Anwender-Wetter.md) | Nowcast, Vorhersage, Unwetterwarnungen, Radar-Overlay |
| [Großschadenslage](Anwender-Grosschadenslage.md) | Phasen-Kanban, SKKM-Stab, Regelkreis, Ressourcen, GSL-Einheiten |
| [Lagekarte der Großschadenslage](Anwender-Grosschadenslage-Karte.md) | Interaktive Karte, Polygone, Pin-Modus, Druck & Print-Center |
| [Taktische Lagekarte (ÖBFV E-27)](Anwender-Taktische-Lagekarte.md) | Normkonforme Symbole, Magnetfarben, taktische Legende |
| [Übergreifende Meldungen](Anwender-Uebergreifende-Meldungen.md) | Lageweite Cross-Marker mit Status-Workflow, Medien & Karte |
| [GSL-Ressourcenverwaltung](Anwender-GSL-Ressourcenverwaltung.md) | Einheiten anlegen, disponieren, Mehrfach-Disposition, Fremdorg |
| [Geräteverleih](Anwender-Geraeteverleih.md) | Ausgabe & Rücknahme von Material in der GSL, Barcode-Scan |
| [Drohne / UAS](Anwender-Drohne-UAS.md) | BOS-Drohneneinsatz: starten, Flugbuch, Checklisten, Notfall, Medien, PDF |
| [Fahrtenbuch](Anwender-Fahrtenbuch.md) | Fahrt erfassen: Fahrzeug, Maschinist, km/BH, Seilwinde, Token/QR-Zugang |
| [Objekte](Anwender-Objekte.md) | Objektdaten pflegen, PDF-Unterlagen klassifizieren, Einsatzansicht, Objektblatt-Druck |
| [Nachschlagewerke](Anwender-Nachschlagewerke.md) | Gefahrgut nach UN-Nummer/Stoffname, Rettungsdatenblätter, Evakuierungsradius & Ausbreitung — offlinefähig |
| [Förderstrecken-Planer](Anwender-Foerderstrecken-Planer.md) | Löschwasserförderung lange Wegstrecke: Q-Berechnung, Druckprofil mit Hochpunkt-Prüfung, Maschinisten-Sollwerte, Material, PDF & Zettel-Link |

### Administration
| Seite | Beschreibung |
|-------|-------------|
| [Benutzer und Rollen](Administration-Benutzer-und-Rollen.md) | User anlegen, Rollen zuweisen, Lockout |
| [Stammdaten pflegen](Administration-Stammdaten-pflegen.md) | Fahrzeuge, Alarmtypen, Auftragsvorschläge |
| [Einstellungen](Administration-Einstellungen.md) | Org-Stammdaten, Logo, Auto-Schließen, Wetter-Opt-out |
| [Organisationen verwalten](Administration-Organisations-verwalten.md) | Multi-Org: anlegen, Seed-Profile, Einladungen, System-Konsole |
| [API-Keys verwalten](Administration-API-Keys-verwalten.md) | Anlegen, Rotieren, Sperren |
| [Audit-Log und Zeitreise](Administration-Audit-Log-und-Zeitreise.md) | Historie nachvollziehen, Stand rekonstruieren |
| [Statistik-Dashboard](Administration-Statistik-Dashboard.md) | Kennzahlen interpretieren |
| [Geräteverleih (Admin)](Administration-Geraeteverleih.md) | Artikel und Stücklisten pflegen, Verleih-Übersicht |
| [Drohne / UAS](Administration-Drohne-UAS.md) | Modul aktivieren, Geräteregister, Wartungsbuch, Pilotenregister, Compliance |
| [Single Sign-On (Entra ID)](Administration-Single-Sign-On.md) | Microsoft-365-Login einrichten, Gruppen-Mapping, JIT-Provisioning |
| [Mail-Versand (SMTP / Office 365)](Administration-Mail-Versand.md) | Eigenen SMTP-Server und/oder Office 365 je Org einrichten, Fallback-Kette, Azure-App-Registrierung für Mail.Send |
| [Lokale Wetterstation](Administration-Wetterstation.md) | Davis/Meteobridge-Anbindung: Station anlegen, Push-Token, Meteobridge-URL, Datenbankarchitektur |
| [Fahrtenbuch](Administration-Fahrtenbuch.md) | Fahrzeuge konfigurieren, Zwecke/Zielorte, Token/QR, Schadensmeldung, Fahrten-Verwaltung |
| [LIS/IPR-Anbindung](Administration-LIS-Anbindung.md) | Leitstellensystem konfigurieren, Einsatz-/Fahrzeugabgleich, Diagnose-Aufzeichnung |
| [SMS-Einsatzinfo & Empfang](Administration-SMS-Einsatzinfo.md) | Alarm-SMS-Verteiler, manueller Versand, Weiterleitungsregeln für eingehende SMS |
| [Teams-Alarmierung](Administration-Teams-Alarmierung.md) | Webhook-Basis-Modus einrichten, optionale Bot-Erweiterung für Zusage/Absage |
| [WordPress-Berichte](Administration-WordPress-Berichte.md) | Beim Einsatzabschluss automatisch einen Beitragsentwurf im Wehr-Blog anlegen, Alarmarten- und Fahrzeug-Zuordnung |
| [Push mit Firebase Cloud Messaging](Administration-Push-FCM.md) | Globale FCM-Konfiguration für Push-Nachrichten an die native Android-App |
| [Datensicherung (Org, Self-Service)](Administration-Org-Datensicherung.md) | Eigene Org-Daten als Archiv herunterladen oder geplant an ein eigenes Ziel senden (SFTP/FTP/rclone); Restore in neue Org (Sysadmin) |
| [Objektverwaltung](Administration-Objektverwaltung.md) | Modul aktivieren (System+Org), Rolle Objektverwalter, Kataloge, Alarm-Matching, Alarm-Infoscreen, KI-Klassifizierung |
| [Nachschlagewerke](Administration-Nachschlagewerke.md) | Modul aktivieren (System+Org), Gefahrgut-Datenquelle (BAM/ADR), Rettungskarten-URL, Offline-Funktion |
| [Print & Alarm Gateway](Administration-Print-Alarm-Gateway.md) | Modul aktivieren (System+Org), Gateways koppeln, Drucker & Discovery, Druckregeln (Automatikdruck), manueller Druck |
| [Förderstrecken-Planer](Administration-Foerderstrecken-Planer.md) | Modul aktivieren (System+Org), Pumpen/Schläuche mit Kennlinien (Vorlagen TS 1600/TS 1200), Kalibrierung über Übungsmessungen, PDF & Maschinisten-Token |

### Entwickler
| Seite | Beschreibung |
|-------|-------------|
| [Architektur](Entwickler-Architektur.md) | Module, Schichten, Datenfluss, Multi-Tenancy |
| [Sicherheit](Entwickler-Sicherheit.md) | Authentifizierung, CSRF, Tenant-Isolation, Medien und Rate-Limiting |
| [Datenmodell](Entwickler-Datenmodell.md) | Tabellen, Beziehungen, Multi-Tenancy-Schema |
| [REST-API](Entwickler-REST-API.md) | Endpoints, Payload-Validierung, Rate-Limiting, curl-Beispiele |
| [WebSocket-Events](Entwickler-WebSocket-Events.md) | Event-Typen, Pub/Sub |
| [Lokale Entwicklung](Entwickler-Lokale-Entwicklung.md) | uvicorn, Docker-Compose für DB, CSS-Build |
| [Tests](Entwickler-Tests.md) | pytest, Fixtures, Multi-Tenancy-Tests, CI |
| [Beitragen](Entwickler-Beitragen.md) | Branch-Strategie, PRs, Commits, Feature-Flag-Pattern |

### Feedback & Support
| Seite | Beschreibung |
|-------|-------------|
| [Fehler melden / Wünsche / Diskussion](Feedback-und-Support.md) | Bug Reports, Feature Requests und Diskussionen auf GitHub |

---

**Repository:** https://github.com/BattloXX/Einsatzcockpit  
**Issues & Feedback:** https://github.com/BattloXX/Einsatzcockpit/issues  
**Feuerwehr Wolfurt:** https://www.feuerwehr-wolfurt.at  
**Migration-Runbook:** [docs/MIGRATION_RUNBOOK.md](../MIGRATION_RUNBOOK.md)
