# Einsatzcockpit

**Echtzeit-Führung im Einsatz** — das digitale Cockpit für die Einsatzführung.
Multi-User, mandantenfähig, Echtzeit. Für Feuerwehr, BOS und Gemeinden.

🔗 [einsatzcockpit.com](https://einsatzcockpit.com)

[![CI](https://github.com/BattloXX/Einsatzcockpit/actions/workflows/ci.yml/badge.svg)](https://github.com/BattloXX/Einsatzcockpit/actions)
[![Docker Build](https://github.com/BattloXX/Einsatzcockpit/actions/workflows/docker-build.yml/badge.svg)](https://github.com/BattloXX/Einsatzcockpit/pkgs/container/einsatzcockpit)
![Python](https://img.shields.io/badge/python-3.14-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green)
![Version](https://img.shields.io/badge/version-2026.10.28-orange)

**Dokumentation:** [GitHub Wiki](https://github.com/BattloXX/Einsatzcockpit/wiki) · [Wiki-Quellen](docs/wiki/Home.md) · [Lizenz](#lizenz)

---

## Überblick

Eine vollwertige Webapp, die Einsatzleitern und Schriftführern eine strukturierte, Echtzeit-fähige Arbeitsumgebung bietet.

**Zielgruppe:** Einsatzleiter, Schriftführer, Atemschutz-Überwacher und UAS-Teams österreichischer Feuerwehren.

**Kern-Prinzipien:**

- Mehrere Geräte (Tablet, PC, Mobilgerät) arbeiten gleichzeitig am selben Einsatz
- Vollständiges Audit-Log — jede Änderung wird protokolliert (Zeitreise-Funktion)
- Multi-Tenancy — mehrere Organisationen auf einer Instanz, row-level isoliert
- Offline-fähige PWA — eingeschränkte Nutzung auch ohne Netzverbindung
- Native Android-App — eigenständiger Client mit echtem Offline-Datenbestand für Objekte & Kontakte, Live-Einsatzstatus und Homescreen-Widgets

## Features

Gegliedert wie Menü und Wiki: zuerst die Einsatzführung, dann die Module in der Reihenfolge, in der man sie im Einsatz braucht.

### Einsatz führen

| Feature | Beschreibung |
|---------|-------------|
| **Echtzeit-Einsatzführung** | WebSocket-basiertes Board für Kräfte, Aufträge, Fahrzeuge und Meldungen; abgeschlossene Einsätze nur noch lesbar |
| **Atemschutzüberwachung** | Rückzugsdruckberechnung, Zeitmessung und Warnungen |
| **Lageführung** | Taktische Zeichen, Fahrzeug- und Objekt-Layer, Multi-User-Editing, Replay und Druck |
| **Zentrale Suche** | Suche über Objekte, Objektdokumente, Einsatz- und Katastrophenpläne, Einsätze, Kontakte, Straßensperren, Wasserstellen und GSL-Daten mit Rechteprüfung |
| **Wetter & lokale Stationen** | Nowcast, Warnungen, Radar und optionale Meteobridge-Anbindung |
| **Archiv & Berichte** | Audit-Log, Zeitreise und PDF-Exporte |

### Module

| Gruppe | Module |
|--------|--------|
| **Im Einsatz** | **Einsatzwissen** (Einsatzpläne, Führungswissen, Fachwissen & Lehrbehelfe, Katastrophenschutz, Sondereinsatzmittel und Nachschlagewerk; bestehende Planarten, Freigaben, Akteure, Prüfzyklus, PDF-Quellen, Karte, Offlinefassung und KI-Import) · **Kontakte** (Einsatztelefonlisten, Verzeichnisse) · **Straßensperren** (Karte, Nachbarfreigaben, optionale Anfahrtsprüfung) · **Förderstrecke** (Wasserförderung planen) |
| **Einsatzbegleitend** | **Fahrtenbuch** · **Atemschutz-Geräteprüfung** · **Drohne / UAS** (Dokumentation gemäß RL-UAS LFV Vorarlberg 2024) |
| **Verwaltung & Planung** | **Objekte** (Einsatzunterlagen, Alarm-Matching, OCR-Suche, externe Objektpflege) · **Probenplan** (Jahresplanung, Appell, Nachbereitung, öffentlicher Kalender) · **Planspiele** (Übungsgenerator für Board und Großschadenslagen) · **Mailing** |
| **Auswertung** | **Statistik** (Kennzahlen und Diagramme zu den Einsätzen; [Statistik-Infoscreen](docs/statistik-infoscreen.md)) |

### Großschadenslage, Alarmierung und Anbindung

| Feature | Beschreibung |
|---------|-------------|
| **Großschadenslage & Stab** | Phasen-Kanban, Einsatzstellen, SKKM-Regelkreis, Ressourcen, Lagekarte und Einheitenmodus für Fahrzeug-Tablets |
| **Alarm-Integrationen** | REST-API, LIS/IPR, DIBOS, SMS, E-Mail, Teams sowie Print & Alarm Gateway und Windows-Druckagent |
| **PWA & mobile Nutzung** | Offline-Betrieb, Push-Benachrichtigungen und QR-Schnellzugriff |
| **Native Android-App** | Sideload-APK mit zuverlässigem Push, Live-Einsatzstatus, Homescreen-Widgets und Offline-Datenbestand für Objekte & Kontakte |
| **MCP-Server** | OAuth-gesicherte KI-Anbindung für Einsätze, Großschadenslagen, Objekte, Kontakte, Einsatz- und Katastrophenpläne, Straßensperren und Fahrtenbuch |
| **KI-Assistent** | Optionale Auftragsvorschläge, Lagebilder und Priorisierung per Anthropic Claude |
| **Multi-Org-Support** | Mehrere Feuerwehren, gemeinsame Einsätze und strikt isolierte Stammdaten |

→ [Alle Funktionen im Wiki](https://github.com/BattloXX/Einsatzcockpit/wiki) · [Native Android-App im Wiki](docs/wiki/Anwender-Native-Android-App.md) · [Android-App-Repository](https://github.com/BattloXX/Einsatzcockpit-Android)

## Tech-Stack

| Schicht | Technologie |
|---------|-------------|
| Backend | **FastAPI** auf Python 3.14, Uvicorn/Gunicorn |
| Daten | **SQLAlchemy 2.x**, Alembic, MariaDB 12.3 |
| Frontend | Jinja2, **HTMX**, Alpine.js, Tailwind CSS, SortableJS |
| Karten | Leaflet, Leaflet-Geoman, Markercluster |
| Echtzeit & PWA | WebSockets, Service Worker, Web Push |
| Dokumente & Medien | WeasyPrint, pypdf, Pillow, ffmpeg |
| Deployment | NGINX, systemd, Gunicorn auf Port **8092** |

## Quick Start (lokale Entwicklung)

Vorausgesetzt werden Python 3.14, MariaDB 12.3 (Docker, CI, Test- und Produktivsystem; ab 10.11 lauffähig, aber nicht mehr getestet), Node.js 22+ und optional `ffmpeg`.

```bash
git clone https://github.com/BattloXX/Einsatzcockpit.git
cd Einsatzcockpit
python3.14 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
npm install && npm run build
cp .env.example .env                    # DATABASE_URL und SECRET_KEY anpassen
alembic upgrade head && python -m app.seed_data
uvicorn app.main:app --reload --port 8092
```

Danach: `http://localhost:8092`. Docker-Datenbank, CSS-Watch-Modus, VAPID und plattformspezifische Hinweise stehen unter [Lokale Entwicklung](docs/wiki/Entwickler-Lokale-Entwicklung.md).

## Installation (Produktion)

Die produktive Installation beginnt mit den [Server-Voraussetzungen](docs/wiki/Installation-Server-Voraussetzungen.md). Die anschließenden Wiki-Seiten führen durch Datenbank, App, systemd, NGINX, Erst-Setup, Backups und Updates.

Für einen containerisierten Betrieb baut GitHub Actions bei jedem Push auf `main`
und jedem Release automatisch ein Image und veröffentlicht es nach
[`ghcr.io/battloxx/einsatzcockpit`](https://github.com/BattloXX/Einsatzcockpit/pkgs/container/einsatzcockpit);
`docker compose pull && docker compose up -d` startet App, MariaDB und Redis ohne
lokalen Build. Details unter [Docker Compose](docs/wiki/Installation-Docker.md).

## Konfiguration

Die vollständige, kommentierte Referenz ist [`.env.example`](.env.example).

> **Vor jedem Produktivstart setzen:** `SECRET_KEY`, `FERNET_KEY`, `DATABASE_URL` und `COOKIE_SECURE=true`; bei mehr als einem Worker zusätzlich `REDIS_URL`. Die Startup-Validierung bricht bei unsicherer Konfiguration ab. Siehe [Installation-Troubleshooting](docs/wiki/Installation-Troubleshooting.md).

Für den optionalen MCP-Server ist eine öffentliche HTTPS-URL als `PUBLIC_BASE_URL` erforderlich. Aktivierung, Limits und Proxy-Vorgaben: [MCP-Server](docs/wiki/Administration-MCP-Server.md).

## Datenbank-Migrationen

```bash
alembic upgrade head                         # ausstehende Migrationen anwenden
alembic current                              # aktuellen Stand anzeigen
alembic revision --autogenerate -m "text"   # Migration erzeugen
alembic downgrade -1                         # eine Revision zurückrollen
```

Pre-Flight-Checks, Reihenfolge, Rollback und bekannte Fallstricke: [Migration-Runbook](docs/MIGRATION_RUNBOOK.md).

## Frontend-Build

```bash
npm install
npm run build       # einmaliger Produktions-Build
npm run dev         # Tailwind Watch-Modus
npm run vendor      # nur bei Bibliotheks-Updates
```

Die generierte Datei `app/static/css/app.css` wird mitcommittet.

## CLI

```bash
python -m app.cli create-admin --username admin --password 'sicheres-passwort'
python -m app.cli create-api-key --label "Alarmierungssystem" --org-id 1
python -m app.cli generate-vapid
```

Weitere Betriebsbefehle stehen in den jeweiligen [Wiki-Kapiteln](docs/wiki/Home.md).

## Dokumentation

Das Wiki ist die kanonische Quelle für Installation, Bedienung, Administration, Entwicklung und Betrieb.

| Einstieg | Inhalt |
|----------|--------|
| [Wiki-Startseite](https://github.com/BattloXX/Einsatzcockpit/wiki) | Einstieg nach Aufgabe, alle Module in Menü-Reihenfolge ([Quelle](docs/wiki/Home.md)) |
| [Erste Schritte](docs/wiki/Anwender-Erste-Schritte.md) | Login, Oberfläche, Menü und Tastaturkürzel |
| [Einsatz starten](docs/wiki/Anwender-Einsatz-starten.md) | Vom Alarm bis zum laufenden Einsatz |
| [Großschadenslage](docs/wiki/Anwender-Grosschadenslage.md) | Stab, Einsatzstellen, Ressourcen und Einheitenansicht |
| [Einsatzwissen – Gesamtdokumentation](docs/einsatzwissen.md) | Bedienung, Dokumentpflege, Freigaben, KI, MCP, Architektur und Betrieb |
| [Einsatzwissen – Phase 7](docs/einsatzwissen-phase7.md) | Dokumentreferenzen, Prüfmetadaten, PDF-Scrollen, MCP und Abschlussprüfung |
| [Einsatzwissen – Phase 1](docs/einsatzwissen-phase1.md) | Bestandsaufnahme, Kategorien, Datenmodell, Kompatibilität und Migration |
| [Einsatzwissen – Phase 6](docs/einsatzwissen-phase6.md) | Kompakte Einsatzansicht, Schnellzugriff, Suche, lokale Kerninhalte und mobile Abnahme |
| [Dokumentenviewer – Phase 2](docs/dokumentviewer-phase2.md) | Gemeinsame PDF-/Bildanzeige, Dokumentfolgen, Seitenauswahl und Integration |
| [Dokumentendruck – Phase 3](docs/dokumentdruck-phase3.md) | Seitenauswahl und Dokumentpakete, lokaler Druck, vorhandene Stationsdrucker und Status |
| [Dokumente im Einsatz – Phase 4](docs/einsatzdokumente-phase4.md) | Unveränderliche Übernahmen, Auftrag und Einheitenfreigaben, Board, Archiv und Backup |
| [Administration](docs/wiki/Administration-Einstellungen.md) | Organisations- und Systemeinstellungen, Modulschalter |
| [Lokaler OSM-Gebaeudebestand](docs/architecture/osm-gebaeude.md) | Import, Overpass-Rueckfall, Statistik und Diagnose |
| [Architektur](docs/wiki/Entwickler-Architektur.md) | Schichten, Module, Datenflüsse und Multi-Tenancy |
| [REST-API](docs/wiki/Entwickler-REST-API.md) | Endpunkte, Payloads und externe Alarmierung |
| [MCP-Server](docs/wiki/Administration-MCP-Server.md) | KI-Anwendungen, OAuth, Aktivierung und Betrieb |
| [Einsatz-Feed](docs/wiki/Administration-Einsatz-Feed.md) | Lesende Pull-Schnittstelle für externe Systeme |
| [Backup & Disaster-Recovery](docs/wiki/Betrieb-Backup-und-Disaster-Recovery.md) | Sicherungen, Restore-Probe, RPO und RTO |
| [Fehlerbehebung](docs/wiki/Installation-Troubleshooting.md) | Häufige Installations- und Betriebsfehler |

Die Markdown-Quellen unter `docs/wiki/` werden automatisch ins GitHub Wiki gespiegelt.

## Tests

```bash
pytest -q -n auto
pytest tests/test_breathing.py tests/test_payload_validation.py -v
pytest --cov=app --cov-report=html tests/
```

Das Projekt besitzt eine umfangreiche Test-Suite; der aktuelle Status ist im [CI-Badge](https://github.com/BattloXX/Einsatzcockpit/actions) sichtbar. Details zu Fixtures, Unit- und Integrationstests: [Tests im Wiki](docs/wiki/Entwickler-Tests.md).

## Sicherheit

- Sichere Sessions, bcrypt-Passwörter, Login-Lockout und verpflichtende Produktions-Secrets
- Double-Submit-CSRF-Schutz und Rate-Limits pro IP beziehungsweise API-Key
- Row-Level-Tenant-Isolation mit zusätzlichen Zugriffskontrollen für gemeinsame Einsätze
- Geschützte Medienauslieferung mit MIME-Prüfung und UUID-Dateinamen
- MCP mit öffentlichen PKCE-Clients, gehashten Tokens und Live-Prüfung von Rollen und Modulstatus

→ [Sicherheitsarchitektur und Betriebsanforderungen](docs/wiki/Entwickler-Sicherheit.md)

## REST-API

```bash
curl https://einsatzleiter.example.at/api/v1/einsatz/active \
  -H "X-API-Key: ec_xxxx"
```

Authentifizierung, Payloads, Validierungsregeln, Rate-Limits und weitere Beispiele: [REST-API im Wiki](docs/wiki/Entwickler-REST-API.md).

Der MCP-Endpunkt ist getrennt von der REST-API und wird unter `/mcp` per OAuth verbunden: [MCP-Server im Wiki](docs/wiki/Administration-MCP-Server.md).

## Rollen und Organisationen

Rollen sind kombinierbar; System-, Organisations- und Fachrollen begrenzen Aktionen und Sichtbarkeit. Die vollständige Matrix steht unter [Benutzer und Rollen](docs/wiki/Administration-Benutzer-und-Rollen.md).

Mehrere Feuerwehren können auf einer Instanz betrieben werden und explizit an Einsätzen zusammenarbeiten. Stammdaten bleiben tenant-isoliert; Details unter [Organisationen verwalten](docs/wiki/Administration-Organisations-verwalten.md) und [Architektur](docs/wiki/Entwickler-Architektur.md).

## KI-Assistent

Der optionale Assistent liefert Auftragsvorschläge, Lage-Hinweise, Lagebilder, Einsatzbericht-Entwürfe und GSL-Priorisierung. Er ist standardmäßig deaktiviert und kann mit eigenem Anthropic-API-Key pro Organisation aktiviert werden; Konfiguration und Bedienung beschreibt [Einstellungen](docs/wiki/Administration-Einstellungen.md).

## In-App-Update

Systemadministratoren können ein geprüftes Release-ZIP einspielen; die App schützt vor Zip-Slip, migriert die Datenbank und lädt Gunicorn graceful neu. Der vollständige Ablauf steht unter [Updates](docs/wiki/Installation-Updates.md).

## Betrieb

Produktivbetrieb umfasst neben Updates insbesondere automatisierte Datenbank- und Mediensicherungen, regelmäßige Restore-Proben, Off-Site-Kopien und die Kontrolle von Service- und Anwendungslogs. Das [Betriebs-Runbook](docs/wiki/Betrieb-Backup-und-Disaster-Recovery.md) beschreibt die vorgesehenen Abläufe.

## Autoren

| Name | Rolle |
|------|-------|
| **Johannes Battlogg** ([@BattloXX](https://github.com/BattloXX)) | Lead-Entwicklung, Konzept & Design |
| **Roman Reiter** | Fachberatung Einsatzleitung & Atemschutz |

## Version

Aktuell: **2026.10.28** · → [Vollständige Versionshistorie](CHANGELOG.md)

## Lizenz

**GNU Lesser General Public License v2.1 (LGPL-2.1)** — Johannes Battlogg.
