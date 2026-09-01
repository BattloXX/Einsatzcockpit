# Einsatzcockpit

**Echtzeit-Führung im Einsatz** — das digitale Cockpit für die Einsatzführung.
Multi-User, mandantenfähig, Echtzeit. Für Feuerwehr, BOS und Gemeinden.

🔗 [einsatzcockpit.com](https://einsatzcockpit.com)

[![CI](https://github.com/BattloXX/Einsatzcockpit/actions/workflows/ci.yml/badge.svg)](https://github.com/BattloXX/Einsatzcockpit/actions)
[![Docker Build](https://github.com/BattloXX/Einsatzcockpit/actions/workflows/docker-build.yml/badge.svg)](https://github.com/BattloXX/Einsatzcockpit/pkgs/container/einsatzcockpit)
![Python](https://img.shields.io/badge/python-3.14-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green)
![Version](https://img.shields.io/badge/version-2026.08.17-orange)

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

## Features

| Feature | Beschreibung |
|---------|-------------|
| **Echtzeit-Einsatzführung** | WebSocket-basiertes Kanban-Board für Kräfte, Aufgaben, Fahrzeuge und Meldungen |
| **Großschadenslage & Stab** | Phasen-Kanban, Einsatzstellen, SKKM-Regelkreis, Ressourcen und Lagekarte |
| **Lageführung** | Taktische Zeichen, Fahrzeug- und Objekt-Layer, Multi-User-Editing, Replay und Druck |
| **Atemschutzüberwachung** | Rückzugsdruckberechnung, Zeitmessung und Warnungen |
| **Alarm-Integrationen** | REST-API, LIS/IPR, externer SMS-/E-Mail-Versand, Teams sowie Print & Alarm Gateway |
| **Objekt- & Nachschlagewerke** | Einsatzunterlagen, Alarm-Matching, OCR-Suche, Gefahrgut und Rettungsdatenblätter |
| **Multi-Org-Support** | Mehrere Feuerwehren, gemeinsame Einsätze und strikt isolierte Stammdaten |
| **Archiv & Berichte** | Audit-Log, Zeitreise, Statistik und PDF-Exporte |
| **Wetter & lokale Stationen** | Nowcast, Warnungen, Radar und optionale Meteobridge-Anbindung |
| **UAS / Drohne** | BOS-Drohnendokumentation gemäß RL-UAS LFV Vorarlberg 2024 |
| **PWA & mobile Nutzung** | Offline-Betrieb, Push-Benachrichtigungen und QR-Schnellzugriff |
| **KI-Assistent** | Optionale Auftragsvorschläge, Lagebilder und Priorisierung per Anthropic Claude |

→ [Vollständige Feature-Liste im Wiki](docs/wiki/Home.md#kernfunktionen)

## Tech-Stack

| Schicht | Technologie |
|---------|-------------|
| Backend | **FastAPI** auf Python 3.14, Uvicorn/Gunicorn |
| Daten | **SQLAlchemy 2.x**, Alembic, MariaDB 10.11+ |
| Frontend | Jinja2, **HTMX**, Alpine.js, Tailwind CSS, SortableJS |
| Karten | Leaflet, Leaflet-Geoman, Markercluster |
| Echtzeit & PWA | WebSockets, Service Worker, Web Push |
| Dokumente & Medien | WeasyPrint, pypdf, Pillow, ffmpeg |
| Deployment | NGINX, systemd, Gunicorn auf Port **8092** |

## Quick Start (lokale Entwicklung)

Vorausgesetzt werden Python 3.14, MariaDB 10.11+, Node.js 20+ und optional `ffmpeg`.

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
| [Wiki-Startseite](docs/wiki/Home.md) | Vollständiger Index und Feature-Überblick |
| [Erste Schritte](docs/wiki/Anwender-Erste-Schritte.md) | Login, Oberfläche und Tastaturkürzel |
| [Administration](docs/wiki/Administration-Einstellungen.md) | Organisations- und Systemeinstellungen |
| [Architektur](docs/wiki/Entwickler-Architektur.md) | Schichten, Module, Datenflüsse und Multi-Tenancy |
| [REST-API](docs/wiki/Entwickler-REST-API.md) | Endpunkte, Payloads und externe Alarmierung |
| [Backup & Disaster-Recovery](docs/wiki/Betrieb-Backup-und-Disaster-Recovery.md) | Sicherungen, Restore-Probe, RPO und RTO |
| [Fehlerbehebung](docs/wiki/Installation-Troubleshooting.md) | Häufige Installations- und Betriebsfehler |

Die Markdown-Quellen unter `docs/wiki/` werden automatisch ins GitHub Wiki gespiegelt.

## Tests

```bash
pytest tests/ -v
pytest tests/test_breathing.py tests/test_payload_validation.py -v
pytest --cov=app --cov-report=html tests/
```

Das Projekt besitzt eine umfangreiche Test-Suite; der aktuelle Status ist im [CI-Badge](https://github.com/BattloXX/Einsatzcockpit/actions) sichtbar. Details zu Fixtures, Unit- und Integrationstests: [Tests im Wiki](docs/wiki/Entwickler-Tests.md).

## Sicherheit

- Sichere Sessions, bcrypt-Passwörter, Login-Lockout und verpflichtende Produktions-Secrets
- Double-Submit-CSRF-Schutz und Rate-Limits pro IP beziehungsweise API-Key
- Row-Level-Tenant-Isolation mit zusätzlichen Zugriffskontrollen für gemeinsame Einsätze
- Geschützte Medienauslieferung mit MIME-Prüfung und UUID-Dateinamen

→ [Sicherheitsarchitektur und Betriebsanforderungen](docs/wiki/Entwickler-Sicherheit.md)

## REST-API

```bash
curl https://einsatzleiter.example.at/api/v1/einsatz/active \
  -H "X-API-Key: ec_xxxx"
```

Authentifizierung, Payloads, Validierungsregeln, Rate-Limits und weitere Beispiele: [REST-API im Wiki](docs/wiki/Entwickler-REST-API.md).

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

Aktuell: **2026.08.17** · → [Vollständige Versionshistorie](CHANGELOG.md)

## Lizenz

**GNU Lesser General Public License v2.1 (LGPL-2.1)** — Freiwillige Feuerwehr Wolfurt.
