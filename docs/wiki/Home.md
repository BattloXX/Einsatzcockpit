# Einsatzcockpit

Digitales Einsatzleiter-Werkzeug für österreichische Feuerwehren — Multi-User, Multi-Organisations-fähig, Echtzeit.

**Version:** 3.5.0 · **Python:** 3.14 · **FastAPI** + HTMX + MariaDB

## Was ist das?

Eine Python-Webapp (FastAPI + HTMX + WebSocket), die ein bisheriges Single-File-HTML-Tool ersetzt und um echte Multi-User-Fähigkeit, Atemschutzüberwachung, Mannschaftsregister, Archiv, PDF-Export, vollständige Multi-Tenancy, Großschadenslage-Führung und Drohnen-Dokumentation erweitert.

**Kernfunktionen:**
- Echtzeit-Kanban-Board für mehrere Geräte gleichzeitig (WebSockets)
- Automatische Einsatzanlage aus dem Alarmierungssystem (REST-API, idempotent)
- LIS/IPR-Anbindung an das Leitstellensystem: Einsatz-/Übungseinsatzabgleich, Fahrzeugstatus, Meldungen, automatisches Schließen
- SMS-Einsatzinfo bei Alarm sowie SMS-Empfang mit Weiterleitungsregeln (Teams, Gruppen, Mitglieder, Ad-hoc)
- Print & Alarm Gateway: lokaler Docker-Container für seriellen Leitstellen-Alarm (→ Einsatzanlage) und Netzwerkdruck (Automatik-Druckregeln + manuell)
- Teams-Alarmierung: vollständige Alarm-Karte (Kartenbild, Google-Maps-Link, No-Login-Alarmübersicht) bei jeder Einsatzanlage, optional mit Bot-Zusage/-Absage
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
- Geräteverleih für Großschadenslagen (Artikel, Stücklisten, Barcode-Scan, SMS)
- PWA für Offline-Betrieb, Web-Push-Benachrichtigungen
- QR-Code-Schnellzugriff für zustoßende Einsatzkräfte
- KI-Assistent (Auftragsvorschläge, Lagebild, Auto-Priorisierung) via Anthropic Claude — opt-in
- Datenbank-Backup & Disaster-Recovery: automatisierte Dumps beider DBs + Medien, wöchentlich getestete Restore-Probe, Off-Site-Upload (SFTP/SCP/rsync/FTPS/rclone), DR-Runbook
- Rate-Limiting per IP und API-Key (slowapi)

## Inhaltsverzeichnis

### Installation
| Seite | Beschreibung |
|-------|-------------|
| [Server-Voraussetzungen](Installation-Server-Voraussetzungen) | Debian 12, CloudPanel, Python 3.14, Systempakete |
| [Datenbank-Einrichtung](Installation-Datenbank-Einrichtung) | MariaDB anlegen, User und Zeichensatz |
| [App-Installation](Installation-App-Installation) | git clone, venv, pip, .env, alembic, seed |
| [Systemd-Service](Installation-Systemd-Service) | Dienst einrichten, starten, Logs |
| [NGINX-Reverse-Proxy](Installation-NGINX-Reverse-Proxy) | CloudPanel-Vhost, WebSocket-Upgrade, TLS |
| [Erst-Setup](Installation-Erst-Setup) | Admin-User, API-Key, Stammdaten prüfen |
| [Backups](Installation-Backups) | Datenbank-Dumps, Audit-Log-Sicherung |
| [Backup & Disaster-Recovery](Betrieb-Backup-und-Disaster-Recovery) | Automatische Dumps, getestete Restore-Probe, DR-Runbook (RPO/RTO) |
| [Updates](Installation-Updates) | git pull / In-App ZIP-Update, Migrationen, Neustart |
| [SMS-Gateway](Installation-SMS-Gateway) | Android-Gateway-App einrichten: APK, Geräte-Login-QR, Akku-Optimierung |
| [Print & Alarm Gateway](Installation-Print-Alarm-Gateway) | Lokalen Docker-Container koppeln: Pairing-Code, W&T-Alarmleitung, Netzwerkdrucker |
| [Troubleshooting](Installation-Troubleshooting) | Häufige Fehler und Lösungen |

### Anwender
| Seite | Beschreibung |
|-------|-------------|
| [Erste Schritte](Anwender-Erste-Schritte) | Login, Übersicht, Tastatur-Shortcuts |
| [Einsatz starten](Anwender-Einsatz-starten) | Manuell vs. Automatik über Alarmierungssystem oder LIS/Leitstelle |
| [Kanban-Board bedienen](Anwender-Kanban-Board-bedienen) | Spalten, Karten, Drag&Drop, Status-Ampel |
| [Aufträge und Meldungen](Anwender-Auftraege-und-Meldungen) | Anlegen, Zuteilen, Erledigen, Sprachdiktat |
| [Personen erfassen](Anwender-Personen-erfassen) | 4-Stufen-Wizard |
| [Atemschutzüberwachung](Anwender-Atemschutzueberwachung) | Trupp, Drücke, Warnungen, Rückzug |
| [Mannschaftsregister](Anwender-Mannschaftsregister) | Mitglieder, Qualifikationen |
| [Archiv und PDF-Export](Anwender-Archiv-und-PDF-Export) | Abschließen, Bericht drucken |
| [Übungsmodus](Anwender-Uebungsmodus) | Was ist anders, Statistik-Ausschluss |
| [QR-Code Schnellzugriff](Anwender-QR-Code-Schnellzugriff) | Zweites Gerät per Scan einbinden |
| [Mobile Nutzung / PWA](Anwender-Mobile-Nutzung-PWA) | Installieren, Offline-Verhalten |
| [Push-Benachrichtigungen](Anwender-Push-Benachrichtigungen) | Aktivieren auf Handy und PC |
| [Lageführung](Anwender-Lagefuehrung) | Einsatzbezogene Lagekarte: Auto-Layer, taktische Zeichen, Multi-User, Chronologie/Replay, Druck & PDF-Lagebericht |
| [Lagekarte.info](Anwender-Lagekarte) | Adresse & Koordinaten, Live-Fahrzeuge auf lagekarte.info |
| [Wetter-Integration](Anwender-Wetter) | Nowcast, Vorhersage, Unwetterwarnungen, Radar-Overlay |
| [Großschadenslage](Anwender-Grosschadenslage) | Phasen-Kanban, SKKM-Stab, Regelkreis, Ressourcen, GSL-Einheiten |
| [Lagekarte der Großschadenslage](Anwender-Grosschadenslage-Karte) | Interaktive Karte, Polygone, Pin-Modus, Druck & Print-Center |
| [Taktische Lagekarte (ÖBFV E-27)](Anwender-Taktische-Lagekarte) | Normkonforme Symbole, Magnetfarben, taktische Legende |
| [Übergreifende Meldungen](Anwender-Uebergreifende-Meldungen) | Lageweite Cross-Marker mit Status-Workflow, Medien & Karte |
| [GSL-Ressourcenverwaltung](Anwender-GSL-Ressourcenverwaltung) | Einheiten anlegen, disponieren, Mehrfach-Disposition, Fremdorg |
| [Geräteverleih](Anwender-Geraeteverleih) | Ausgabe & Rücknahme von Material in der GSL, Barcode-Scan |
| [Drohne / UAS](Anwender-Drohne-UAS) | BOS-Drohneneinsatz: starten, Flugbuch, Checklisten, Notfall, Medien, PDF |
| [Fahrtenbuch](Anwender-Fahrtenbuch) | Fahrt erfassen: Fahrzeug, Maschinist, km/BH, Seilwinde, Token/QR-Zugang |
| [Objekte](Anwender-Objekte) | Objektdaten pflegen, PDF-Unterlagen klassifizieren, Einsatzansicht, Objektblatt-Druck |
| [Nachschlagewerke](Anwender-Nachschlagewerke) | Gefahrgut nach UN-Nummer/Stoffname, Rettungsdatenblätter, Evakuierungsradius & Ausbreitung — offlinefähig |
| [Förderstrecken-Planer](Anwender-Foerderstrecken-Planer) | Löschwasserförderung lange Wegstrecke: Q-Berechnung, Druckprofil mit Hochpunkt-Prüfung, Maschinisten-Sollwerte, Material, PDF & Zettel-Link |

### Administration
| Seite | Beschreibung |
|-------|-------------|
| [Benutzer und Rollen](Administration-Benutzer-und-Rollen) | User anlegen, Rollen zuweisen, Lockout |
| [Stammdaten pflegen](Administration-Stammdaten-pflegen) | Fahrzeuge, Alarmtypen, Auftragsvorschläge |
| [Einstellungen](Administration-Einstellungen) | Org-Stammdaten, Logo, Auto-Schließen, Wetter-Opt-out |
| [Organisationen verwalten](Administration-Organisations-verwalten) | Multi-Org: anlegen, Seed-Profile, Einladungen, System-Konsole |
| [API-Keys verwalten](Administration-API-Keys-verwalten) | Anlegen, Rotieren, Sperren |
| [Audit-Log und Zeitreise](Administration-Audit-Log-und-Zeitreise) | Historie nachvollziehen, Stand rekonstruieren |
| [Statistik-Dashboard](Administration-Statistik-Dashboard) | Kennzahlen interpretieren |
| [Geräteverleih (Admin)](Administration-Geraeteverleih) | Artikel und Stücklisten pflegen, Verleih-Übersicht |
| [Drohne / UAS](Administration-Drohne-UAS) | Modul aktivieren, Geräteregister, Wartungsbuch, Pilotenregister, Compliance |
| [Single Sign-On (Entra ID)](Administration-Single-Sign-On) | Microsoft-365-Login einrichten, Gruppen-Mapping, JIT-Provisioning |
| [Mail-Versand (SMTP / Office 365)](Administration-Mail-Versand) | Eigenen SMTP-Server und/oder Office 365 je Org einrichten, Fallback-Kette, Azure-App-Registrierung für Mail.Send |
| [Lokale Wetterstation](Administration-Wetterstation) | Davis/Meteobridge-Anbindung: Station anlegen, Push-Token, Meteobridge-URL, Datenbankarchitektur |
| [Fahrtenbuch](Administration-Fahrtenbuch) | Fahrzeuge konfigurieren, Zwecke/Zielorte, Token/QR, Schadensmeldung, Fahrten-Verwaltung |
| [LIS/IPR-Anbindung](Administration-LIS-Anbindung) | Leitstellensystem konfigurieren, Einsatz-/Fahrzeugabgleich, Diagnose-Aufzeichnung |
| [SMS-Einsatzinfo & Empfang](Administration-SMS-Einsatzinfo) | Alarm-SMS-Verteiler, manueller Versand, Weiterleitungsregeln für eingehende SMS |
| [Teams-Alarmierung](Administration-Teams-Alarmierung) | Webhook-Basis-Modus einrichten, optionale Bot-Erweiterung für Zusage/Absage |
| [Datensicherung (Org, Self-Service)](Administration-Org-Datensicherung) | Eigene Org-Daten als Archiv herunterladen oder geplant an ein eigenes Ziel senden (SFTP/FTP/rclone); Restore in neue Org (Sysadmin) |
| [Objektverwaltung](Administration-Objektverwaltung) | Modul aktivieren (System+Org), Rolle Objektverwalter, Kataloge, Alarm-Matching, Alarm-Infoscreen, KI-Klassifizierung |
| [Nachschlagewerke](Administration-Nachschlagewerke) | Modul aktivieren (System+Org), Gefahrgut-Datenquelle (BAM/ADR), Rettungskarten-URL, Offline-Funktion |
| [Print & Alarm Gateway](Administration-Print-Alarm-Gateway) | Modul aktivieren (System+Org), Gateways koppeln, Drucker & Discovery, Druckregeln (Automatikdruck), manueller Druck |
| [Förderstrecken-Planer](Administration-Foerderstrecken-Planer) | Modul aktivieren (System+Org), Pumpen/Schläuche mit Kennlinien (Vorlagen TS 1600/TS 1200), Kalibrierung über Übungsmessungen, PDF & Maschinisten-Token |

### Entwickler
| Seite | Beschreibung |
|-------|-------------|
| [Architektur](Entwickler-Architektur) | Module, Schichten, Datenfluss, Multi-Tenancy |
| [Datenmodell](Entwickler-Datenmodell) | Tabellen, Beziehungen, Multi-Tenancy-Schema |
| [REST-API](Entwickler-REST-API) | Endpoints, Payload-Validierung, Rate-Limiting, curl-Beispiele |
| [WebSocket-Events](Entwickler-WebSocket-Events) | Event-Typen, Pub/Sub |
| [Lokale Entwicklung](Entwickler-Lokale-Entwicklung) | uvicorn, Docker-Compose für DB, CSS-Build |
| [Tests](Entwickler-Tests) | pytest, Fixtures, Multi-Tenancy-Tests, CI |
| [Beitragen](Entwickler-Beitragen) | Branch-Strategie, PRs, Commits, Feature-Flag-Pattern |

### Feedback & Support
| Seite | Beschreibung |
|-------|-------------|
| [Fehler melden / Wünsche / Diskussion](Feedback-und-Support) | Bug Reports, Feature Requests und Diskussionen auf GitHub |

---

**Repository:** https://github.com/BattloXX/Einsatzcockpit  
**Issues & Feedback:** https://github.com/BattloXX/Einsatzcockpit/issues  
**Feuerwehr Wolfurt:** https://www.feuerwehr-wolfurt.at  
**Migration-Runbook:** [docs/MIGRATION_RUNBOOK.md](../MIGRATION_RUNBOOK.md)
