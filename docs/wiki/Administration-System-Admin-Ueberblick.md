# System-Admin: Überblick und Inbetriebnahme

Die Systemadministration verbindet persönliche Anmeldung mit optionaler 2FA,
ein technisches Diagnose-Cockpit und einen separaten Admin-MCP für KI-Clients.
Alle drei Zugänge verwenden gemeinsame Diagnose- und Reparaturdienste. Der
organisationsgebundene MCP bleibt ein eigener Zugang mit eigenen Berechtigungen.

## Gesamtarchitektur

```text
Persönliches Systemadmin-Konto
  |
  +-- Browser-Session --> zentrales MFA-Gate (wenn aktiviert)
  |                         |
  |                         +--> System-Einstellungen / GitHub-Zugang
  |                         +--> /admin/systemdiagnose (HTMX-Karten)
  |                         +--> HIGH-Freigabe (neuer Faktor je Simulation)
  |
  +-- Claude / anderer MCP-Client --> /mcp/admin --> eigener OAuth/PKCE-Provider
                                         |          + globale Freigabe
                                         |          + persönliche Freigabe
                                         |          + Live-Rechte / MFA
                                         v
                         Gemeinsame technische Services
                         +-- system_diagnose / GitHub read-only / Codeanalyse
                         +-- Diagnose-Registry --> diag_run --> Leader-Runner
                         |                        +--> diag_finding
                         +-- Reparatur-Registry --> Dry-Run / Hash / Freigabe
                                                  +--> kontrollierte Ausführung

Leader-Supervision --> Tagesplaner --> eindeutiger DB-Tages-Slot --> diag_run
                       Laufende Einsätze: verschieben / nur leichte Checks
Laufende --> Meldungsclaims je finding_key --> höchstens eine Systemmail je Lauf

Org-MCP /mcp --> eigener OAuth + Org-Scope --> bestehende Fachwerkzeuge
```

Leader-Locks gelten je Host; globale DB-Claims sichern Diagnose und Reparatur
auch zwischen Hosts ab. Worker-Status und Redis sind technische Indikatoren,
keine vollständige externe Verfügbarkeitsüberwachung.

## Einschaltreihenfolge

Die Voreinstellungen bleiben sicher ausgeschaltet. Bestehende Einsätze und
Org-MCP werden durch das Deployment nicht umkonfiguriert.

- [ ] Vollständiges Server-/Datenbank-Backup erstellen und Wiederherstellung prüfen.
- [ ] Migrationen **0325 bis 0329** in Reihenfolge anwenden (`alembic upgrade head` im vorgesehenen Deploymentablauf).
- [ ] Für alle aktiven persönlichen Systemadministratoren [2FA einrichten](Administration-Zwei-Faktor-Authentifizierung), Recovery-Codes getrennt verwahren und Einrichtung bestätigen.
- [ ] Erst danach Admin-2FA systemweit aktivieren und eine neue Anmeldung prüfen.
- [ ] Optional den GitHub-Lesetoken im eigenen Abschnitt der Systemeinstellungen setzen und testen; Repository und Ablauf kontrollieren.
- [ ] Für KI-Zugriff `admin_mcp_enabled` aktivieren und je gewünschtem Konto `admin_mcp_access` freigeben.
- [ ] Verbindung zum Admin-MCP mit einem persönlichen Konto prüfen; OAuth-Token nicht in Logs oder Tickets kopieren.
- [ ] Optional `admin_repair_enabled` nach erfolgreicher 2FA aktivieren; LOW-Automatik nur für ausdrücklich gewünschte feste Aktions-IDs konfigurieren.
- [ ] [Systemdiagnose](Administration-Systemdiagnose) manuell prüfen und erst danach Tagesdiagnose, Empfänger, Uhrzeit, Schweregrad und Wiederholung konfigurieren.

Das Cockpit ist auch bei ausgeschaltetem Admin-MCP erreichbar. GitHub-Token und
Reparaturschalter sind optional. Ohne Token zeigt der GitHub-Abgleich seine
Einschränkung an. Die Tagesdiagnose ist standardmäßig AUS, Empfänger bleiben leer.

## Rollen und Rechte

| Konto / Freigabe | Systemdiagnose-Webseite | Admin-MCP | Reparaturen |
| --- | --- | --- | --- |
| Persönlicher aktiver `system_admin` | Ja, zentrales MFA-Gate bei aktiver 2FA | Nur mit globalem Schalter und persönlichem `admin_mcp_access` | Aktive 2FA und Reparaturschalter; Web unabhängig vom MCP-Opt-in |
| `system_admin` ohne MCP-Freigabe | Ja | Nein | Web nach denselben Reparatur- und MFA-Prüfungen |
| Normaler Admin / `org_admin` | Nein | Nein | Nein |
| Geräte- oder QR-Sitzung | Nein | Nein | Nein |
| Inaktives Konto | Nein | Nein | Nein |
| Organisationsbenutzer | Nein | Nur eigener Org-MCP nach dessen Regeln | Keine globalen Reparaturen |

Die globale 2FA-Pflicht ist optional, für Reparaturen aber zwingend. Frische MFA
(maximal fünf Minuten) ist für kontrollierte Ausführung und sensible
Zugangsänderungen erforderlich. HIGH verlangt zusätzlich einen **neu geprüften**
TOTP- oder Recovery-Code, eine an den Simulationshash gebundene einmalige Freigabe
und deren Verbrauch innerhalb von zehn Minuten. CRITICAL bleibt reine Simulation.

## Mit Claude verbinden

Im verwendeten Client einen Remote-MCP-Connector mit der URL
`<base>/mcp/admin` konfigurieren. Die öffentliche Basis-URL steht in den
Systemeinstellungen. Danach über den eigenen OAuth-Login mit persönlichem lokalem
Benutzernamen und Passwort anmelden; bei aktiver Admin-2FA einen neuen TOTP- oder
unbenutzten Recovery-Code verwenden. Die konkrete Connector-Oberfläche richtet
sich nach dem Client. Die Admin-Verbindung ist vom Org-Connector `<base>/mcp`
getrennt. Org-Tokens berechtigen nicht zum Admin-Zugriff.

SSO-only-Konten ohne lokales Passwort können diesen Admin-OAuth-Login nicht
verwenden. SSO ersetzt hier keinen zweiten Faktor. Details zu Tokenlaufzeiten,
PKCE, Freigaben und Widerruf: [System-Admin-MCP](Administration-System-Admin-MCP).

## Notfall und Recovery

Zuerst einen unbenutzten Recovery-Code verwenden. Jeder Code ist einmalig;
Recovery-Codes werden nicht per E-Mail zugesandt. Ein anderer berechtigter
Administrator kann den dokumentierten persönlichen Reset durchführen.

Bei Verlust aller Faktoren bleibt der lokale Serverzugang. Im
Anwendungsverzeichnis beziehungsweise Container:

```bash
.venv/bin/python -m app.cli mfa-reset <username>
.venv/bin/python -m app.cli mfa-global-aus
```

`mfa-reset` entfernt die Faktoren und Recovery-Codes des Kontos und widerruft
seine Sessions. Danach neu anmelden und 2FA erneut einrichten.
`mfa-global-aus` deaktiviert die globale Pflicht und widerruft Admin-Sessions.
Nach der Wiederherstellung alle aktiven Systemadministratoren kontrollieren,
2FA wieder aktivieren und Audit prüfen. Reparaturen bleiben ohne aktive 2FA
gesperrt, selbst wenn ihr Schalter noch gesetzt ist. Es gibt keinen anonymen
HTTP-Reset und keinen Mail-Bypass. Die CLI-Aktionen werden auditiert.

Bei GitHub-Tokenverlust den Token beim Anbieter widerrufen und über den eigenen
Abschnitt rotieren. Diagnose und manueller Webzugang funktionieren mit
entsprechend eingeschränktem GitHub-Status weiter. Bei unerwarteten Reparaturfolgen
keine blinden Wiederholungen auslösen: Status, Verifikation und Backup prüfen.

## Sicherheitsmodell und Betriebsgrenzen

- Diagnose ist lesend und ressourcenbegrenzt. Prüfsitzungen besitzen eine SQL-Schreibsperre; die Engine persistiert nur technische Ergebnisse und Laufzustände.
- Diagnose, Befunde, Reparaturmetadaten, Freigaben und Meldungsclaims sind global und vom Org-Export ausgeschlossen. Reparaturaktionen müssen ihre Objekt-IDs ausdrücklich mit Org-ID scopen.
- Freitexte, Personen- und Einsatzinhalte sowie Secrets gehören weder in Cockpit-Ausgaben noch in Meldungen. Audit zeigt im Cockpit ausschließlich technische Aktionsmetadaten.
- Alle Browser-POSTs sind CSRF-geschützt. HTMX aktualisiert die betroffenen Bereiche ohne Vollreload. Zeitangaben werden über die vorhandenen Zeitzonenfilter angezeigt.
- DB-Slot und Benutzerkontingent erlauben einen aktiven Diagnoselauf und sechs Starts/Stunde je Benutzer, gemeinsam für Web und MCP.
- Während Einsätzen werden teure Checks ausgelassen; ein Tageslauf wird bis zu sechs Stunden verschoben und danach leicht ausgeführt. Unvollständige Checks sind keine Entwarnung.
- GitHub wird ausschließlich gelesen. Ein Codex-Auftrag ist Text; Codeänderungen erfolgen über Branch, Tests und Pull Request, nicht im Produktionscontainer.
- Reparaturen besitzen einen festen Katalog, begrenzten Scope, Dry-Run, Simulationshash, Idempotenz und DB-Mutex. Veraltete Simulationen oder geänderte Daten werden abgelehnt.
- Tagesmeldungen sind Sammelmails mit Dedup je Finding-Key. Versandfehler blockieren keinen Lauf; Claims verhindern einen Fehlerversandsturm. Teams und SMS bleiben org-gebundene Dienstmonitor-Kanäle.
- Lokale Logs und Redis-Status sind workerbezogen. Verbindungsprobleme, fehlende GitHub-Daten oder übersprungene Checks müssen bei der Bewertung berücksichtigt werden.

## Detailseiten

- [Systemdiagnose und Tagesdiagnose](Administration-Systemdiagnose)
- [System-Admin-MCP und Reparaturkatalog](Administration-System-Admin-MCP)
- [Zwei-Faktor-Authentifizierung](Administration-Zwei-Faktor-Authentifizierung)
- [Einstellungen](Administration-Einstellungen)
- [Dienstüberwachung](Administration-Dienstueberwachung)
- [Org-MCP](Administration-MCP-Server)
