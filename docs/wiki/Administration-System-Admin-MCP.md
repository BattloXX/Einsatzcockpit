# System-Admin-MCP

Der System-Admin-MCP stellt sieben ausschliesslich lesende Diagnosewerkzeuge bereit.
Er ist vom organisationsgebundenen MCP getrennt und funktioniert auch fuer
Systemadministratoren ohne Organisation. Einsaetze, Kontakte, Mitglieder, Objekte,
Jobinhalte und Zugangsdaten werden nicht ausgegeben.

## Aktivieren

1. Unter `/admin/system-einstellungen` den Abschnitt unter der Admin-2FA oeffnen.
2. Den globalen Schalter **Admin-MCP systemweit** aktivieren (Default: AUS).
3. Den Zugang fuer jedes gewuenschte persoenliche Systemadministrator-Konto
   explizit freigeben. Bei aktiver Admin-2FA muss die MFA-Pruefung des handelnden
   Administrators hoechstens fuenf Minuten alt sein: vorher `/mfa/verify` aufrufen.

Normale Admins, Organisationsadministratoren, inaktive Konten und Geraetekonten
sind ausgeschlossen. Jede Zugangsaenderung widerruft alle Admin-MCP-Tokens des
betroffenen Benutzers, ohne seine Browser-Sitzungen oder Org-MCP-Tokens zu aendern.
Globales Ausschalten widerruft alle Admin-MCP-Tokens.

## Mit Claude verbinden

In Claudes Konfiguration fuer einen benutzerdefinierten Remote-MCP-Connector die
auf der Einstellungsseite angezeigte URL eintragen: `<public_base>/mcp/admin`.
Die genaue Bezeichnung der Connector-Einstellung haengt vom verwendeten Client ab.
Die OAuth-Anmeldung erfolgt auf `/mcp/admin/anmelden` mit Benutzername und lokalem
Passwort. Bei aktiver Admin-2FA zusaetzlich einen frischen TOTP- oder unbenutzten
Recovery-Code eingeben. TOTP- und Recovery-Codes koennen nur einmal verwendet werden.

SSO-only-Konten ohne lokales Passwort werden in Phase 2 nicht unterstuetzt.
Es gibt keinen SSO-/Cookie-Bypass. Dafuer ein persoenliches Systemadministrator-Konto
mit lokalem Passwort und expliziter Freigabe verwenden; bei SSO-Zwang bleibt dieser
beruecksichtigt. Die Login-Seite erklaert diese Grenze, Fehler verraten keine Kontodetails.

## Werkzeuge

| Werkzeug | Ausgabe |
| --- | --- |
| `admin_system_uebersicht` | Version, Commit, Build und Gesamtstatus der Teilchecks |
| `admin_dienste_pruefen` | Aggregierte gespeicherte Dienstmonitor-Zustaende aller Organisationen |
| `admin_worker_pruefen` | Anzahl aktiver/abgelaufener Worker, Leader und fehlerhafter Loops |
| `admin_redis_pruefen` | Lokaler Konfigurations- und Verbindungszustand, ohne Netzwerkprobe |
| `admin_datenbank_pruefen` | Verbindung, Pool-Zaehler und Alembic-Stand |
| `admin_gateway_pruefen` | Gateway-/SMS-Presence, W&T-Dienstmonitor und offene/ueberfaellige Druck-/SMS-Jobs |
| `admin_logs_analysieren` | Lokale, gefilterte und deduplizierte Log-Gruppen mit Level, Signatur und Anzahl |

Logs: `level` ist DEBUG, INFO, WARNING (Default), ERROR oder CRITICAL;
`limit` liegt zwischen 1 und 200 (Default 100); `suchtext` ist optional und
hoechstens 200 Zeichen lang. Die Suche erfolgt im lokalen Ringpuffer.
Freie Meldungstexte werden vor der Gruppierung maskiert und anschliessend nur als
HMAC-Signatur ausgegeben. Damit bleiben auch beliebige Namen/Einsatzdetails in
Logtexten verborgen. Gleichartige Meldungen haben nach Maskierung und Entfernen
des Log-Zeitpraefixes dieselbe Signatur; der Signaturschluessel wird nie ausgegeben.

Alle Antworten enthalten UTC-Zeitpunkt, Status (`ok`, `warn`, `fail`, `unbekannt`),
Modul, Schweregrad, Evidenz, ausdruecklich gekennzeichnete Ursachenhypothese,
Empfehlung und eine Korrelations-ID. Fehlende Dienstmonitor-Daten werden als
`nicht verfuegbar` ausgewiesen. Dienstmonitorwerte sind gespeicherte Zustaende,
keine neu gestarteten Pruefungen. W&T wird nur anhand dieser Datenbasis beurteilt.

## Sicherheitsmodell

- Eigener OAuth-Provider, eigene Tabellen, Scope `mcp-admin` und Resource-URL
  `<public_base>/mcp/admin`. Org-MCP-Tokens sind nicht austauschbar.
- Oeffentliche PKCE-S256-Clients; Codes und Tokens werden ausschliesslich gehasht
  gespeichert. Die Registrierung erzeugt kein Client-Geheimnis.
- Access-Tokens gelten hoechstens 15 Minuten, eine Refresh-Familie absolut
  hoechstens acht Stunden. Rotation widerruft vorherige Tokens dieser Familie.
- Jeder Refresh, jedes Auflisten und jeder Tool-Aufruf prueft live Aktivitaet,
  Systemadministratorrolle, persoenliche Freigabe, globalen Schalter und
  `session_epoch`. Bei aktiver 2FA ist zusaetzlich ein MFA-Zeitpunkt innerhalb
  der letzten acht Stunden erforderlich. Rechteentzug und Epochenwechsel wirken sofort.
- Login: CSRF-Schutz, fuenf Versuche je Minute/IP sowie bestehender Passwort-
  und MFA-Lockout. Keine spezifischen Ablehnungsgruende fuer einzelne Konten.
- Tools: 30 Aufrufe je Minute/Benutzer, 20 Sekunden Antwortzeitlimit, maximal
  64 KiB Antwort. Mit Redis gilt das Kontingent workeruebergreifend, ohne Redis
  je Worker; faellt der konfigurierte Limit-Speicher aus, wird der Aufruf verweigert.
- Audit: `admin_mcp.login`, `.login_failed`, `.denied`, `.tool`,
  `.global_changed`, `.access_changed`; Toolname, maskierte Argumente und
  Korrelations-ID, keine Antwortinhalte, Passwoerter oder MFA-Codes.

## Grenzen

Keine Schreibwerkzeuge, Diagnose-Jobs, Reparaturen oder Codeanalyse.
Logs stammen ausschliesslich vom angesprochenen Worker und werden nur aggregiert
angezeigt. Ein Thread mit einer laufenden DB-Abfrage kann nach dem Antwortzeitlimit
noch auslaufen; er besitzt seine eigene Session und fuehrt nur Lesepruefungen aus.
Ein bereits laufender Aufruf wird bei Rechteentzug nicht rueckwirkend abgebrochen.
Fuer diese Phase sind keine serverseitigen SSO-Logins vorgesehen.
