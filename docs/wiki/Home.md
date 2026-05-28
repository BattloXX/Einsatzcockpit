# FWWO Einsatzleiter-Hilfswerkzeug

Digitales Einsatzleiter-Werkzeug der **Freiwilligen Feuerwehr Wolfurt** (Vorarlberg).

## Was ist das?

Eine Python-Webapp (FastAPI + HTMX), die das bisherige Single-File-HTML-Tool ablöst und um echte Multi-User-Fähigkeit, Atemschutzüberwachung, Mannschaftsregister, Archiv und PDF-Export erweitert.

**Kernfunktionen:**
- Echtzeit-Kanban-Board für mehrere Geräte gleichzeitig (WebSockets)
- Automatische Einsatzanlage aus dem Alarmierungssystem (REST-API)
- Gesetzeskonforme Atemschutzüberwachung mit Rückzugsdruckberechnung
- Mannschaftsregister mit Qualifikationen und Ablaufdaten
- Archiv mit vollständigem Audit-Log und PDF-Export
- PWA für Offline-Betrieb, Web-Push-Benachrichtigungen
- QR-Code-Schnellzugriff für zustoßende Einsatzkräfte

## Inhaltsverzeichnis

### Installation
| Seite | Beschreibung |
|-------|-------------|
| [Server-Voraussetzungen](Installation-Server-Voraussetzungen) | Debian 12, CloudPanel, Python, Systempakete |
| [Datenbank-Einrichtung](Installation-Datenbank-Einrichtung) | MariaDB anlegen, User und Zeichensatz |
| [App-Installation](Installation-App-Installation) | git clone, venv, pip, .env, alembic, seed |
| [Systemd-Service](Installation-Systemd-Service) | Dienst einrichten, starten, Logs |
| [NGINX-Reverse-Proxy](Installation-NGINX-Reverse-Proxy) | CloudPanel-Vhost, WebSocket-Upgrade, TLS |
| [Erst-Setup](Installation-Erst-Setup) | Admin-User, API-Key, Stammdaten prüfen |
| [Backups](Installation-Backups) | Datenbank-Dumps, Audit-Log-Sicherung |
| [Updates](Installation-Updates) | git pull, Migrationen, Neustart |
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

### Administration
| Seite | Beschreibung |
|-------|-------------|
| [Benutzer und Rollen](Administration-Benutzer-und-Rollen) | User anlegen, Rollen zuweisen |
| [Stammdaten pflegen](Administration-Stammdaten-pflegen) | Fahrzeuge, Wehren, Auftragsvorschläge |
| [API-Keys verwalten](Administration-API-Keys-verwalten) | Anlegen, Rotieren, Sperren |
| [Lagekarte-Tokens](Administration-Lagekarte-Tokens) | GeoJSON-Feed-Tokens für lagekarte.info verwalten |
| [Audit-Log und Zeitreise](Administration-Audit-Log-und-Zeitreise) | Historie nachvollziehen, Stand rekonstruieren |
| [Statistik-Dashboard](Administration-Statistik-Dashboard) | Kennzahlen interpretieren |

### Entwickler
| Seite | Beschreibung |
|-------|-------------|
| [Architektur](Entwickler-Architektur) | Module, Schichten, Datenfluss |
| [Datenmodell](Entwickler-Datenmodell) | Tabellen, Beziehungen |
| [REST-API](Entwickler-REST-API) | Endpoints, Payload, curl-Beispiele |
| [WebSocket-Events](Entwickler-WebSocket-Events) | Event-Typen, Pub/Sub |
| [Lokale Entwicklung](Entwickler-Lokale-Entwicklung) | uvicorn, Docker-Compose für DB |
| [Tests](Entwickler-Tests) | pytest, Fixtures, CI |
| [Beitragen](Entwickler-Beitragen) | Branch-Strategie, PRs, Commits |

---

**Repository:** https://github.com/BattloXX/FWWO-Einsatzleiter-Hilfswerkzeug  
**Feuerwehr Wolfurt:** https://www.feuerwehr-wolfurt.at
