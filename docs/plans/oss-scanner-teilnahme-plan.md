# Plan: Teilnahme von Einsatzcockpit (ECP) am Anthropic OSS Scanner

Stand 2026-10-11, **noch nicht umgesetzt**.

## Was der OSS Scanner ist (geprüft im Repo anthropics/oss-scanner)

- Anthropic scannt öffentliche Open-Source-Repos auf Sicherheitslücken. Die Anmeldung erfolgt per
  Pull Request in `anthropics/oss-scanner`, der genau ein Verzeichnis `projects/<name>/` anlegt.
- Ablauf beim Scanner:
  - Der Scanner baut das Projekt **online** in einer isolierten VM aus einem von uns gelieferten
    `Dockerfile`.
  - Danach analysiert er es **ohne Internet**.
  - Die Funde gehen per Mail an `primary_contact` und optional an `auto_ccs`, mit Reproducer und
    Patch-Vorschlag.
  - Die Berichte sind modellgeneriert und nicht menschlich geprüft. Es gibt keine 90-Tage-Frist,
    und Anthropic veröffentlicht nichts.
- Pflichtangaben:
  - `project.yaml` mit `repo` und `primary_contact`
  - `Dockerfile`, entweder in unserem Repo (empfohlen, per Feld `dockerfile:`) oder neben
    `project.yaml`
- Optional, aber empfohlen: `threat_model.md`.
- Die Mailadressen in `project.yaml` sind **öffentlich**. Optional ist ein PGP-Schlüssel möglich,
  dann gehen die Berichte nur an `primary_contact`, ohne CCs.
- Werkzeuge vor dem PR: `tools/validate.py` und `tools/check <name>` (Build wie beim Scanner, dann
  Shell ohne Netz). Voraussetzungen: Docker, git, Python 3 mit PyYAML. Es gelten die
  OSS-Scanner-Terms.
- Abmelden: `disabled: true` in `project.yaml` oder das Verzeichnis entfernen.

## Eignung

- `BattloXX/Einsatzcockpit` ist öffentlich und steht unter LGPL-2.1. Das passt.
- Es gibt kein `SECURITY.md` und keine öffentliche Sicherheitsadresse. Beides muss vorher angelegt
  werden.
- Weitere öffentliche Repos (`Einsatzcockpit-gateway`, `Einsatzcockpit-Android`,
  `Einsatzleiter-SMS-Gateway`) sind ein optionaler zweiter Schritt, je Repo ein eigenes
  `projects/<name>/`.

## Schritte

### 1. Vorbereitung im Einsatzcockpit-Repo (ein PR)

1. **Sicherheitskontakt**: Ein Alias wie `security@einsatzcockpit.com` (öffentlich sichtbar). Mit
   der neuen Funktion „Mail-Weiterleitungen“ (M6) lässt er sich als Regel auf der Empfangsdomain
   abbilden, z. B. `security@get.einsatzcockpit.com` → private Adresse(n). Bei Bedarf einen
   PGP-Schlüssel erzeugen.
2. **`SECURITY.md`**: Meldeweg, unterstützte Versionen (aktueller Stand von `main` bzw. Release),
   Hinweis auf GitHub Private Vulnerability Reporting (in den Repo-Einstellungen aktivieren),
   Reaktionszeiten.
3. **`.oss-scanner/Dockerfile`** (Scan-Image, nicht das Produktions-Image):
   - Basis `python:3.14-slim` mit den Systempaketen wie im Produktions-`Dockerfile` (Pango, Cairo,
     ffmpeg, poppler, tesseract-deu, libmariadb-dev)
   - Node 20 für `npm ci && npm run build` (Tailwind) und `npm run test:js`
   - `pip install -e ".[dev]"`, ohne Playwright-Browser-Download (E2E bleibt außen vor)
   - Testumgebung mit SQLite wie in `tests/conftest.py`, alle Netz-Loops aus
     (`NACHSCHLAGEWERK_SYNC_ENABLED=false` usw.); Dummy-`SECRET_KEY` nur für Tests
   - Abschluss-Check im Build: `ruff check app/` und ein Smoke-Lauf `pytest -q -x tests/<kleine
     Auswahl>`. Die ganze Suite läuft im Image ohne Netz, damit der Scanner sie nutzen kann.
   - Vorher klären, warum lokal ~540 Tests umgebungsbedingt scheitern, in der CI aber nicht. Das
     Scan-Image muss die CI-Bedingungen nachbilden. Ziel: Suite im Container ohne Netz grün.
     Optional MariaDB im Image, falls Migrationstests gewünscht.
4. **`.oss-scanner/threat_model.md`** (wichtigster Hebel für brauchbare Funde):
   - **Was ECP ist**: mandantenfähige Einsatzleit-Software für Feuerwehren. FastAPI, HTMX/Alpine,
     WebSockets. Eine Org = ein Mandant.
   - **Vertrauensgrenzen und Eingänge**:
     - Login/Sessions/PIN-Login
     - öffentliche Token-, QR- und PIN-Routen (GK-/QR-Zugang `/gk`, Bürger-Meldungen,
       Objektpflege, Freigabelinks)
     - Webhooks (Resend-Inbound mit Svix-Signatur, LIS/DIBOS, BMA)
     - Remote-MCP-Server mit Bearer-Token
     - Geräte-Tokens (Tablet/Android, Einheitenmodus)
     - Druck-Gateway, SMS-Gateway
     - Datei-Uploads (Bilder/PDF/Video/`datei`)
     - PDF-Rendering mit WeasyPrint (SSRF über Bilder/URLs)
     - eingehende Mails (unvertrauenswürdiges HTML und Anhänge)
     - Mail-Weiterleitung (Relay-Missbrauch)
   - **Schutzziele**:
     - **Mandantentrennung** (`app/core/tenant.py` filtert nur SELECTs, siehe CLAUDE.md)
     - Rollenmodell (system_admin, admin, incident_leader, recorder, readonly, Geräte)
     - Integrität der Alarmierung/Disposition
     - Vertraulichkeit von Personendaten (Telefonnummern, Patientendaten)
     - Geheimnisse (API-Keys verschlüsselt in der DB)
   - **Schweregrad-Rubrik** (Vorschlag):
     - **Kritisch**: unauthentisierter Zugriff auf Daten anderer Orgs, RCE, Auth-Bypass, Übernahme
       von Alarmierung/SMS-Versand
     - **Hoch**: Cross-Org-Zugriff mit gültigem Login, Rechteausweitung zwischen Rollen, Stored
       XSS in Lage/Board, SSRF in interne Netze, offenes Mail-Relay
     - **Mittel**: authentisierter Zugriff auf Daten innerhalb der eigenen Org über
       Rollengrenzen, CSRF mit Wirkung, Reflected XSS
     - **Niedrig**: Info-Leaks ohne Personendaten, fehlende Header
   - **Nicht im Scope**:
     - Deployment/Infrastruktur (Reverse Proxy, MariaDB-Konfiguration)
     - DoS durch Masse
     - Selbst-XSS
     - Testsystem-Daten
     - Abhängigkeiten ohne erreichbaren Pfad
   - **Wünsche an Berichte**: Reproducer als pytest-Test im Stil von
     `tests/test_public_tenant_isolation.py`, Patch als minimaler Diff.
5. Secret-Hygiene vor dem Einschreiben: Repo und Historie auf Schlüssel und Tokens prüfen
   (z. B. gitleaks). `test.db` und `app_storage` dürfen nicht versioniert sein (aktuell untracked).

### 2. Lokal prüfen

- Fork bzw. Klon von `anthropics/oss-scanner`, `projects/einsatzcockpit/project.yaml` anlegen:
  ```yaml
  repo: https://github.com/BattloXX/Einsatzcockpit
  primary_contact: security@einsatzcockpit.com   # öffentlich!
  homepage: https://einsatzcockpit.com
  dockerfile: .oss-scanner/Dockerfile
  threat_model: .oss-scanner/threat_model.md
  ```
- `tools/validate.py` und `tools/check einsatzcockpit` laufen lassen. In der Shell ohne Netz
  müssen ruff, pytest und `npm run test:js` laufen. Sicherheitshinweis aus dem Scanner-README:
  `tools/check` baut mit Netz und installiert Claude Code ins Image. Nur auf diesem
  Entwicklungsrechner ausführen.

### 3. Anmeldung

- Pull Request an `anthropics/oss-scanner` mit nur `projects/einsatzcockpit/project.yaml`.
  Dockerfile und Threat-Model bleiben im eigenen Repo, damit spätere Änderungen keinen Scanner-PR
  brauchen.
- Nach dem Merge Build-Fehlermails an `primary_contact` beachten.

### 4. Umgang mit Berichten

- Eingang über den Alias, Triage innerhalb weniger Tage. Funde als **private** GitHub Security
  Advisory erfassen, nicht als öffentliches Issue.
- Fix nach unserem Ablauf: Reproducer als Test, Umsetzung (Codex), Prüfung, CI, Release.
  Danach Advisory veröffentlichen bzw. CHANGELOG-Hinweis.
- Da die Berichte nicht menschlich geprüft sind, jeden Fund erst selbst reproduzieren.
  Falschmeldungen kurz dokumentieren und bei Bedarf das Threat-Model schärfen.
- Abmelden oder pausieren jederzeit per `disabled: true`.

## Offene Entscheidungen (vor Umsetzung)

1. Öffentliche Kontaktadresse (Alias, Domain) und ob PGP verwendet wird. Mit PGP sind keine CCs
   möglich.
2. Nur Einsatzcockpit oder gleich auch Gateway, Android und SMS-Gateway?
3. Schweregrad-Rubrik wie vorgeschlagen übernehmen oder anpassen.
4. Annahme der OSS-Scanner-Terms (https://red.anthropic.com/oss-scanner/terms) durch den
   Repo-Inhaber.
