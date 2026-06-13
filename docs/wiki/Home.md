# Einsatzleiter-Hilfswerkzeug

Digitales Einsatzleiter-Werkzeug für österreichische Feuerwehren — Multi-User, Multi-Organisations-fähig, Echtzeit.

**Version:** 2.2.0 · **Python:** 3.14 · **FastAPI** + HTMX + MariaDB

## Was ist das?

Eine Python-Webapp (FastAPI + HTMX + WebSocket), die ein bisheriges Single-File-HTML-Tool ersetzt und um echte Multi-User-Fähigkeit, Atemschutzüberwachung, Mannschaftsregister, Archiv, PDF-Export und vollständige Multi-Tenancy erweitert.

**Kernfunktionen:**
- Echtzeit-Kanban-Board für mehrere Geräte gleichzeitig (WebSockets)
- Automatische Einsatzanlage aus dem Alarmierungssystem (REST-API, idempotent)
- Gesetzeskonforme Atemschutzüberwachung mit Rückzugsdruckberechnung
- Mannschaftsregister mit Qualifikationen und Ablaufdaten
- Archiv mit vollständigem Audit-Log und PDF-Export
- Multi-Tenancy: mehrere Organisationen, row-level isoliert, gemeinsame Einsätze via Kollaborationsmodell
- PWA für Offline-Betrieb, Web-Push-Benachrichtigungen
- QR-Code-Schnellzugriff für zustoßende Einsatzkräfte
- KI-Assistent (Auftragsvorschläge, Lagebild) via Anthropic Claude — opt-in
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
| [Updates](Installation-Updates) | git pull / In-App ZIP-Update, Migrationen, Neustart |
| [SMS-Gateway](Installation-SMS-Gateway) | CoNiuGo-Modem-Container einrichten, Token anlegen |
| [Troubleshooting](Installation-Troubleshooting) | Häufige Fehler und Lösungen |

### Anwender
| Seite | Beschreibung |
|-------|-------------|
| [Erste Schritte](Anwender-Erste-Schritte) | Login, Übersicht, Tastatur-Shortcuts |
| [Einsatz starten](Anwender-Einsatz-starten) | Manuell vs. Automatik über Alarmierungssystem |
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
| [Lagekarte.info](Anwender-Lagekarte) | Adresse & Koordinaten, Live-Fahrzeuge auf lagekarte.info |
| [Großschadenslage](Anwender-Grosschadenslage) | Phasen-Kanban, Einsatzstellen, Abschnitte, Stab-Journal, KI-Priorisierung |
| [Lagekarte der Großschadenslage](Anwender-Grosschadenslage-Karte) | Interaktive Karte, Abschnitt-Polygone zeichnen, Pin-Modus, Fahrzeuge |

### Administration
| Seite | Beschreibung |
|-------|-------------|
| [Benutzer und Rollen](Administration-Benutzer-und-Rollen) | User anlegen, Rollen zuweisen, Lockout |
| [Stammdaten pflegen](Administration-Stammdaten-pflegen) | Fahrzeuge, Alarmtypen, Auftragsvorschläge |
| [Einstellungen](Administration-Einstellungen) | Org-Stammdaten, Logo, Auto-Schließen, Konfig-Backup |
| [Organisationen verwalten](Administration-Organisations-verwalten) | Multi-Org: anlegen, Seed-Profile, Einladungen, System-Konsole |
| [API-Keys verwalten](Administration-API-Keys-verwalten) | Anlegen, Rotieren, Sperren |
| [Audit-Log und Zeitreise](Administration-Audit-Log-und-Zeitreise) | Historie nachvollziehen, Stand rekonstruieren |
| [Statistik-Dashboard](Administration-Statistik-Dashboard) | Kennzahlen interpretieren |

### Entwickler
| Seite | Beschreibung |
|-------|-------------|
| [Architektur](Entwickler-Architektur) | Module, Schichten, Datenfluss, Multi-Tenancy |
| [Datenmodell](Entwickler-Datenmodell) | Tabellen, Beziehungen, Multi-Tenancy-Schema |
| [REST-API](Entwickler-REST-API) | Endpoints, Payload-Validierung, Rate-Limiting, curl-Beispiele |
| [WebSocket-Events](Entwickler-WebSocket-Events) | Event-Typen, Pub/Sub |
| [Lokale Entwicklung](Entwickler-Lokale-Entwicklung) | uvicorn, Docker-Compose für DB, CSS-Build |
| [Tests](Entwickler-Tests) | pytest, Fixtures, Multi-Tenancy-Tests, CI |
| [Beitragen](Entwickler-Beitragen) | Branch-Strategie, PRs, Commits |

---

**Repository:** https://github.com/BattloXX/Einsatzleiter-Hilfswerkzeug  
**Feuerwehr Wolfurt:** https://www.feuerwehr-wolfurt.at  
**Migration-Runbook:** [docs/MIGRATION_RUNBOOK.md](../MIGRATION_RUNBOOK.md)
