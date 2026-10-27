# System-Admin-MCP

Der System-Admin-MCP stellt sieben direkte Lesewerkzeuge und vier Werkzeuge fuer Diagnose-Laeufe bereit.
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

Keine Fachdaten-Schreibwerkzeuge, Reparaturen oder Codeanalyse. Diagnose-Jobs und deren Grenzen stehen unten.
Logs stammen ausschliesslich vom angesprochenen Worker und werden nur aggregiert
angezeigt. Ein Thread mit einer laufenden DB-Abfrage kann nach dem Antwortzeitlimit
noch auslaufen; er besitzt seine eigene Session und fuehrt nur Lesepruefungen aus.
Ein bereits laufender Aufruf wird bei Rechteentzug nicht rueckwirkend abgebrochen.
Fuer diese Phase sind keine serverseitigen SSO-Logins vorgesehen.

## Diagnose-Laeufe und Integritaetspruefungen (Phase 3)

Zusaetzlich zu den sieben unmittelbaren Lesewerkzeugen gibt es vier Werkzeuge
fuer persistierte Diagnoseergebnisse. Ausschliesslich der Start legt einen
Diagnoseauftrag an; die Checks veraendern keine Fachdaten. Es gibt keine
Reparaturaktionen und noch keine Diagnose-UI oder automatische Tagesdiagnose.

| Werkzeug | Parameter und Verhalten |
| --- | --- |
| `admin_diagnose_starten` | `checks`: optionale Liste bekannter Check-IDs; `umfang`: `schnell` (Standard, leichte Checks) oder `voll` (alle Checks). Explizite Check-Auswahl hat Vorrang. Liefert `run_id` und `korrelations_id`. |
| `admin_diagnose_status` | Optional `run_id`, sonst letzter globaler Lauf. Status, Dauer in Sekunden, Fortschritt, Schweregradzaehler und uebersprungene Checks. |
| `admin_fehler_liste` | Optional `modul`, `schweregrad` (`info`, `warn`, `fail`, `kritisch`), `status` (`offen`, `behoben`, `unterdrueckt`), `seit` (ISO-Zeitpunkt), `limit` (1–100, Standard 50), `cursor`. Sortierung: kritisch zuerst, dann letztes Auftreten absteigend, ID absteigend. Fuer Folgeseiten dieselben Filter und den gelieferten Cursor verwenden. |
| `admin_fehler_details` | Genau eines von `finding_key` oder `id`. Maskierte Evidenz, begrenzte betroffene IDs, gekennzeichnete Hypothese, Empfehlung sowie erstes/letztes Auftreten und Anzahl der Beobachtungen. |

Alle vier Werkzeuge verwenden dasselbe Antwortschema, dieselbe Live-Rechtepruefung,
dasselbe Audit und dieselbe 64-KiB-Grenze wie die bisherigen Werkzeuge. Zusaetzlich
gilt fuer Starts: hoechstens ein aktiver Lauf systemweit und sechs Starts pro
rollierender Stunde/Benutzer. Ein eindeutiger DB-Slot sichert das aktive Limit auch
zwischen Hosts. Der Stundenzaehler liegt ebenfalls in der Datenbank.

### Check-Liste

| ID | Modul | Kosten | Datenbasis |
| --- | --- | --- | --- |
| `db.zustand` | datenbank | leicht | Verbindung, Code-Head/Alembic-Stand, Pool-Auslastung; MariaDB: Prozess-IDs mit Laufzeit >30 Sekunden und wartende InnoDB-Transaktionen, ohne SQL-Texte |
| `worker.zustand` | worker | leicht | Abgelaufene Worker, alte Heartbeats, fehlender Leader, mehrere Leader auf demselben Host, fehlerhafte/ueberfaellige Loops |
| `redis.zustand` | redis | leicht | Publisher/Subscriber, Reconnects und Fehlerzaehler aus lokalem Status und aktiven Worker-Heartbeats |
| `gateway.jobs` | gateway | leicht | SMS/Druck/Alarm-Outbox-Fristen, viele Attempts, fehlende Quittierungen, Presence, abgelaufene Transportnachrichten, parallele offene SMS-Claims als Indikator, W&T-Dienstmonitor |
| `einsatz.integritaet` | einsatz | mittel | Verwaiste Referenzen, ungueltige aktuelle Auftrags-/Dispositionszustaende, widerspruechliche Abschlussmarkierungen, offene Auftraege in geschlossenen Einsaetzen, Disposition zwischen unterschiedlichen Lagen |
| `objekte.dokumente` | objekte | schwer | Fehlende Dokument-/Seitendateien, fertige Dokumente ohne Seiten, fehlender Seitentext, verwaiste Dokumentbeziehungen; leere Pflichtnamen produktiver Objekte nur als Anzahl |
| `katplan.integritaet` | katplan | mittel | Verwaiste Beziehungen/Ziele, fehlende Quelle oder Zielseite, ungueltige Seitenbereiche, Freigabe ohne Pruefdatum |
| `kontakte.integritaet` | kontakte | mittel | Benutzer-/Mitglied-/Kontaktbeziehungen und gleiche normalisierte Kontakt-E-Mail als Dublettenhypothese (nur begrenzte ID-Paare) |
| `suche.rueckstand` | suche | mittel | Alte/oft wiederholte Such-Outbox-Eintraege, Indexzeitpunkt vor gespeichertem Quellzeitpunkt, verwaiste Indexbeziehungen |

Neue Check-Module werden mit `@register_check` dekoriert und im Diagnosepaket
importiert. Anschliessend wird der deklarative Katalog versiegelt; er speichert
keinen Laufzeit- oder Benutzerzustand.

Der Runner laeuft als beaufsichtigter Leader-Loop. Ein bedingtes UPDATE von
`queued` auf `running` verhindert doppelte Ausfuehrung zwischen Workern/Hosts.
Jeder Check bekommt einen eigenen abbrechbaren Prozess und eine eigene
SELECT-Session mit Autoflush AUS, SQL-Schreibsperre und abschliessendem Rollback.
Lokaler Redis-/Pool-Status wird vor dem Prozessstart aufgenommen, damit die
Diagnose den angesprochenen Worker und dessen Anwendungspool beurteilt.

Ein Check hat maximal zehn Sekunden inklusive Prozessstart und ein Budget von
2.000 Ergebniszeilen. Dateipruefungen lesen Seiten zu je 100 Zeilen mit
Keyset-Pagination. Pro Befund werden hoechstens 50 betroffene IDs bzw. ID-Paare
gespeichert, pro Check hoechstens 200 Befunde. Zwischen Checks liegt eine kurze
Pause. Der Lauf hat ein Gesamtbudget von 120 Sekunden. SQLite-SELECTs erhalten
einen VM-Abbruchhandler, MariaDB-SELECTs ein serverseitiges Statement-Zeitlimit.
Der Prozess kann auch bei blockierten Dateisystemzugriffen beendet werden.

### Verhalten waehrend Einsaetzen und Befund-Lebenszyklus

Vor jedem Check wird global auf aktive Einsaetze und aktive Grossschadenslagen
geprueft. Lagefuehrung besitzt keinen eigenen Aktivstatus und haengt an der Lage.
Waehrend eines Einsatzes werden nur leichte Checks ausgefuehrt. Mittlere und
schwere Checks stehen im Fortschritt unter `uebersprungen` mit dem Grund
`uebersprungen_wegen_einsatz`; sie sind kein Fehler. Nach Einsatzende kann ein
weiterer Lauf angefordert werden; es wird kein automatischer Folgeauftrag erzeugt.

`finding_key` ist SHA-256 aus Check-ID und normalisiertem Kernschluessel ohne
Zeitpunkt oder Zufall. Wiederholte Befunde aktualisieren dieselbe Zeile und deren
`last_seen_at`/`occurrences`. Ein vollstaendig erfolgreicher Check markiert seine
nicht mehr auftretenden offenen Befunde als `behoben`. Ausnahmen, Timeout,
Zeilenbudget, Ausgabebudget und uebersprungene Checks bewirken keine Behebung.
`unterdrueckt` bleibt erhalten; diese Phase bietet kein Werkzeug zum Unterdruecken.
Check-Fehler erzeugen einen generischen Befund `check_fehlgeschlagen` und stoppen
die anderen Checks nicht. Ein Lauf kann deshalb `done` sein und trotzdem einen
fehlgeschlagenen Teilcheck enthalten.

Laeufe ohne Fortschritt seit mehr als zehn Minuten werden als `failed` freigegeben.
Die stuendliche Bereinigung im Leader-Loop entfernt bis zu 500 seit mehr als
30 Tagen behobene Befunde und bis zu 500 abgeschlossene alte Laeufe je Durchgang.
Laeufe mit noch aufzubewahrenden Befunden bleiben als deren FK-Ziel erhalten.
Die globalen Tabellen `diag_run`/`diag_finding` werden nicht in Org-Backups exportiert.
Befunde enthalten feste Diagnoseformulierungen, Zaehler und IDs, keine Namen,
Adressen, Telefonnummern, Einsatztexte, Query-Texte oder Dokumentpfade.

### Bewusste Grenzen der vorhandenen Datenbasis

- Historische Zustandsuebergaenge werden nicht aus freien Logs rekonstruiert.
  Geprueft werden die klar definierten aktuellen Zustaende und Beziehungen.
- Mehrfach-Disposition ist ausdruecklich erlaubt. Sie gilt daher nicht pauschal
  als doppelt aktiver Auftrag. Mehrere offene SMS-Claims sind ein Indikator,
  kein Beweis fuer mehrfachen Versand.
- W&T besitzt keine eigene persistente Versandqueue/Presence-Lease. Die Diagnose
  verwendet vorhandene Dienstmonitorwerte und Transportnachrichten.
- Offline-Client-Outbox: kein serverseitiges Modell; geprueft wird die persistente
  Such-Outbox. Ein vollstaendiger Neuvergleich aller Suchquellen ist nicht enthalten.
- Pflichtadressen werden nicht unterstellt. Dokumentpruefungen beurteilen
  Dateiverweise/Existenz, Seiten und Text-Index; sie validieren keine kompletten PDF-Inhalte.
- MariaDB-Prozess-/Transaktionsinformationen sind auf SQLite `nicht verfuegbar`.
  Fehlende MariaDB-Leserechte ergeben einen fehlgeschlagenen Teilcheck ohne Rohfehlertext.
