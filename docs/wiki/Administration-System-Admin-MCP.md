# System-Admin-MCP

Der System-Admin-MCP stellt sieben direkte Diagnose-Lesewerkzeuge, drei Werkzeuge
fuer Code-/CI-Korrelation, vier Werkzeuge fuer Diagnose-Laeufe und optional fuenf
kontrollierte Reparaturwerkzeuge bereit.
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

Keine allgemeinen Fachdaten-Schreibwerkzeuge oder Codeanalyse. Diagnose-Jobs und eng
begrenzte technische Reparaturen mit ihren Grenzen stehen unten.
Logs stammen ausschliesslich vom angesprochenen Worker und werden nur aggregiert
angezeigt. Ein Thread mit einer laufenden DB-Abfrage kann nach dem Antwortzeitlimit
noch auslaufen; er besitzt seine eigene Session und fuehrt nur Lesepruefungen aus.
Ein bereits laufender Aufruf wird bei Rechteentzug nicht rueckwirkend abgebrochen.
Fuer diese Phase sind keine serverseitigen SSO-Logins vorgesehen.

## Diagnose-Laeufe und Integritaetspruefungen (Phase 3)

Zusaetzlich zu den sieben unmittelbaren Lesewerkzeugen gibt es vier Werkzeuge
fuer persistierte Diagnoseergebnisse. Ausschliesslich der Start legt einen
Diagnoseauftrag an; die Checks veraendern keine Fachdaten. Es gibt noch keine
Diagnose-UI oder automatische Tagesdiagnose. Reparaturaktionen stehen separat unten.

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

## Kontrollierte Reparaturen (Phase 4)

Reparaturen sind nach dem Deployment ausgeschaltet. Einschaltreihenfolge unter
`/admin/system-einstellungen`: **2FA an -> admin_mcp_enabled -> admin_repair_enabled**.
Die vorhandene persoenliche MCP-Freigabe bleibt erforderlich. Den Reparaturschalter
aktiviert nur ein persoenlicher Systemadministrator mit frischer MFA (maximal 5 Minuten).
`admin_repair_low_auto` ist standardmaessig leer und akzeptiert ausschliesslich
kommagetrennte IDs registrierter LOW-Aktionen. Schalter und Liste werden auditiert;
der allgemeine Einstellungseditor kann diese Sicherheitsschalter nicht umgehen.

### Aktionskatalog und Scopes

Alle drei Aktionen sind `einsatzsicher=False`. Sie aendern keine Alarm-/Einsatzinhalte
und keine freigegebenen Einsatzplaene. Der Katalog wird nach Import versiegelt;
SQL-, Shell-, Python- oder frei benennbare Funktionsaufrufe sind keine Werkzeuge.
Scopes sind strikt typisiert: unbekannte Felder, falsche Typen und zu grosse Listen
werden abgelehnt. Jede Ausfuehrung betrifft genau eine angegebene `org_id` und
hoechstens 50 explizite IDs. Die Vorschau zeigt ausschliesslich technische IDs,
feste Ueberspringgruende, Zaehler und Hashes, keine Dateipfade oder Fachdaten.

| Aktion | Risiko | Scope und Wirkung | Rollback / Wiederholung |
| --- | --- | --- | --- |
| `outbox.haengende_claims_freigeben` | MEDIUM | `org_id`, `alarm_ids`, `sms_ids`, `druck_ids`, zusammen 1–50 IDs. Nur abgelaufene Claims ohne Zustellungsindizien; bestehende Jobidentitaet bleibt erhalten. | Status-/Lease-Aenderungen und SMS-Attempt-Abschluss liegen gemeinsam in einer Transaktion. Bei Fehler Rollback; nach Erfolg keine erneute Freigabe desselben Claims. |
| `suche.rueckstand_anstossen` | LOW | `org_id`, `ids` (1–50 bestehende Such-Outbox-IDs). Stellt noch nicht faellige `pending`-/`retry`-Auftraege mit weniger als acht Versuchen faellig. Aktive Leases und terminale Jobs bleiben unberuehrt. Indexworker und Org-Suche muessen aktiviert sein. | Transaktionaler Rollback; erneutes Anstossen faelliger Jobs ist wirkungslos. Der vorhandene Indexworker verarbeitet die Queue. Kein vollstaendiger Neuaufbau oder direkter Eingriff in Quelldaten. |
| `dateien.verwaiste_temp_loeschen` | LOW | `org_id`, `dateien` (1–50 technische Uploadnamen `<32 hex>.pdf` oder `.upload`), optional `max_bytes` (1–104857600, Standard 100 MiB). Nur regulaere referenzlose Dateien unter `OBJEKT_MEDIA_DIR/_mcp_uploads/<org_id>`, aelter als 24 Stunden. | Geloeschte Tempdateien werden nicht wiederhergestellt. Jeder erfolgreiche Schritt speichert einen Zwischenstand samt Audit; bei Abbruch neu simulieren und mit neuem Key fortsetzen. Bereits fehlende Dateien werden uebersprungen. |

Alarm-Outbox-`sending` wird bewusst nie zurueckgesetzt: ohne Versand-Fence kann
aus fehlendem Abschluss kein ausgebliebener Versand abgeleitet werden. Nur
`pending`/`retry` mit Versuchszahl null, fehlendem Abschluss und veraltetem Lease
koennen wieder `pending` werden (das ist hier der vorhandene Queue-Status).
SMS werden nur aus `claimed`, mit unverbrauchter Versandfrist, weniger als fuenf
Generationen und ausschliesslich eindeutig nicht versandten Attempts freigegeben.
Provider-/Gateway-Zuweisungen, Transportnachrichten, Versandprotokoll-Indizien,
Received-/Attempted-/Accepted-/Finished-Felder oder `may_have_sent` verhindern dies.
Ein geloeschter Claim-Token sperrt den alten Worker; die Job-ID bleibt gleich.
Bei Druck gilt ausschliesslich der Lease-Modus vor Spooler-Uebergabe (`leased`/
`fetching`), unterhalb der bestehenden Attempts-Grenze. `submitting`, Spool-Referenz,
Push-Route und terminale Status werden uebersprungen. Token-Loeschung und erhoehte
Lease-Generation sperren alte Agent-Aufrufe.

Die Dateibereinigung durchsucht keine beliebigen Verzeichnisse und nimmt keine
frei waehlbaren Pfade an. Verzeichnis-Deskriptoren, `O_NOFOLLOW`, regulaere
Dateityppruefung, Inode-/Zeit-/Groessenvergleich und eine erneute DB-Referenzpruefung
unmittelbar vor `unlink` verhindern das Folgen von Symlinks und Pfad-Traversal.
Auch ein noch nicht fertig hochgeladener DB-Upload schuetzt seinen technischen Namen.
Andere Ablagen und weiterhin referenzierte, auch abgelaufene Uploads sind ausgeschlossen.

### Werkzeugablauf und Freigaben

Die vorhandene Diagnose prueft keine verwaisten MCP-Tempdateien. Deshalb wird
die Dateibereinigung nur mit explizitem Scope simuliert; fehlende Dokumentdateien
werden nicht als Anlass fuer eine sachlich unpassende Temp-Bereinigung verwendet.

| Werkzeug | Verwendung |
| --- | --- |
| `admin_reparatur_vorschlagen` | Genau `finding_key` oder `id`. Ordnet `gateway.jobs` und `suche.rueckstand` den passenden Wartungsaktionen zu und legt `vorgeschlagen`-Laeufe an. Ein Vorschlag ist keine bestaetigte Ursache oder Loesung. |
| `admin_reparatur_simulieren` | `run_id` (optional Scope fuer vorgeschlagenen Lauf) oder `aktion_id` + `scope`. Liefert Vorschau, `simulation_hash` und Freigabebedarf. SQL-Schreibsperre und Autoflush AUS sichern die Aktionssimulation. Nur die anschliessende Lauf-/Audit-Persistenz schreibt. |
| `admin_reparatur_ausfuehren` | `run_id`, `simulation_hash`, verpflichtender `idempotency_key` (8–128 Zeichen), `bestaetigt`, optional `approval_token`. Liefert gespeichertes Ergebnis. |
| `admin_reparatur_status` | `run_id`: Zustand, gespeicherte Vorschau, Ergebnis und Verifikation. Zugriff nur auf eigene Laeufe. |
| `admin_reparatur_verifizieren` | `run_id`: lesende Nachpruefung. Optional `folgediagnose=true` reiht ueber den vorhandenen Diagnose-Start den zugeordneten Check erneut ein; dessen bestehende Kontingente gelten. |

LOW darf nur dann ohne zusaetzliche Bestaetigung ausgefuehrt werden, wenn die
Aktions-ID in `admin_repair_low_auto` steht. Andernfalls gilt wie fuer MEDIUM:
`bestaetigt=true` und exakt der Hash aus der Simulation. HIGH verlangt zusaetzlich
ein einmaliges Web-Token: `/admin/reparatur/freigabe/<run_id>` zeigt die gespeicherte
maskierte Vorschau und verlangt einen **neuen** TOTP-/Recovery-Code. Eine bereits
frische Browser-Sitzung ersetzt diese Pruefung nicht. CSRF-Schutz, fuenf Versuche
pro Minute/IP und persistenter MFA-Lockout gelten auch hier. Die Antwort ist
`no-store`/`no-referrer`; Token ausschliesslich in `approval_token` uebergeben.
In der DB stehen nur Token-Hash, Benutzer, Lauf, Ablauf und der Hash aus
Aktions-ID + Scope + Simulationshash. Ablauf nach maximal zehn Minuten, falscher
Benutzer, anderer Aktionshash und Wiederverwendung werden abgelehnt. CRITICAL ist
ueber MCP ausschliesslich vorschlag-/simulierbar. HIGH-/CRITICAL-Testaktionen
existieren nur in Tests und sind nicht im Produktionskatalog registriert.

### Harte Sperren und Betrieb

Ohne aktive 2FA sind alle fuenf Tools verborgen und jeder direkte Aufruf meldet
`2FA erforderlich`. Der Reparaturschalter muss ebenfalls aktiv sein. Unmittelbar
vor Ausfuehrung werden Live-Benutzer, Rolle, Zugriff, MFA/OAuth-Gueltigkeit,
Schalter und Sitzungsepoche erneut geprueft. MariaDB-Reparaturrequests verwenden
READ COMMITTED, damit alte MVCC-Snapshots keinen Rechteentzug verdecken.
Simulationen gelten maximal 15 Minuten (erneut nach dem Claim geprueft);
veraenderter Datenbestand verlangt eine neue Simulation. Zeilensperren und
bedingte Updates pruefen den Zustand anschliessend innerhalb der Aktion erneut.
Aktive Einsaetze/Grossschadenslagen sperren die Ausfuehrung aller drei Aktionen;
die Erkennung stammt direkt aus dem Diagnose-Runner.

Zusaetzlich zum bestehenden MCP-Limit gelten fuer alle Reparaturtools zusammen
20 Aufrufe pro Minute/Benutzer, in der DB unter Benutzer-Lock gezaehlt. Ein
nullable UNIQUE-Slot pro Aktion und ein bedingter UPDATE verhindern konkurrierende
Ausfuehrung auch zwischen Hosts. Ein ueberlappender Ausfuehrungsversuch wird
abgelehnt; verschiedene Aktionen besitzen getrennte Slots. Gleicher Idempotency-Key
und identischer Scope liefern den gespeicherten Zustand; anderer Scope/Aktion oder
anderer Benutzer werden abgelehnt. Gespeichert wird nur der Hash des Keys.

Jede Stufe und Ablehnung schreibt `admin_repair.*` mit Korrelations-ID und
kontrolliertem Grund, ohne Token, Codes, freie Fehlertexte oder Fachdaten.
Die globalen Tabellen `admin_repair_run`/`admin_approval` werden nicht exportiert.
Laeufe mit einem regulaeren Fehler werden `fehlgeschlagen`; Status-/Queue-Aenderungen
rollen zurueck, bereits geloeschte Tempdateien bleiben im Zwischenstand sichtbar.
Nach hartem Prozess-/Host-Abbruch bleibt ein laufender Mutex absichtlich gesperrt:
kein unsicheres automatisches Uebernehmen nach einer Zeitfrist. Die Administration
muss zuerst das Ende des ausfuehrenden Prozesses und den Bestand pruefen, bevor sie
den Lauf betrieblich abschliesst. Dafuer existiert kein generisches MCP-Schreibwerkzeug.
Bei Antwortzeitlimit kann der begrenzte Ausfuehrungsthread weiterlaufen; deshalb
Status mit demselben Key abfragen/wiederholen und keinen Ersatzlauf mit neuem Key
starten. Es gibt keinen automatischen Reparatur-Loop und keine neue Hintergrundmutation.

## Codekorrelation und CI (Phase 5)

Drei weitere Lesewerkzeuge verwenden dieselbe Sichtbarkeit, Live-Rechtepruefung,
Auditierung, 30 Aufrufe/Minute je Benutzer, Zeitgrenze und 64-KiB-Antwortgrenze.
Sie funktionieren auch bei ausgeschalteter Reparatur und ohne aktive 2FA.

| Werkzeug | Verwendung und Ausgabe |
| --- | --- |
| `admin_code_analyse` | `quelle=logs` (Default) oder `befund` mit `finding_key`; `level` optional (Default ERROR), `limit` 1–100. Gruppiert nach Datei, Funktion und Exception-Typ, mit Anzahl, erstem/letztem Auftreten, verantwortlichen Modulen, Deploy-SHA und Version. Bekannte Aenderungen seit Deploy sind markiert. |
| `admin_ci_status` | Optional `sha`; sonst Deploy-SHA, bei unbekanntem Deploy main-HEAD. Zeigt main-Abstand in beiden Richtungen, Commit-Anzahl, maximal 50 Dateinamen/Status, CI-/Docker-Build-Laeufe und zugehoerige PR-Nummern/URLs. CI-Jobs: Lint (ruff), Type-Check (mypy), Tests (pytest), Migration (MariaDB), Board ohne Full-Reload (Playwright), ausserdem JS-Tests (Outbox). |
| `admin_codex_auftrag_erstellen` | Genau `finding_key` oder `fehlerstelle={"datei":"app/services/...py","funktion":"...","exception_typ":"ValueError"}` aus der lokalen Analyse. Erzeugt ausschliesslich Markdown-Text mit Evidenz, Zeitraum, Anzahl, Deploy, main-Abstand, Hypothese, Testidee und Abnahmekriterien. Reproduktion bleibt ohne Beleg `unbekannt`. Optionaler `titel` erlaubt nur feste technische Woerter; andere Woerter werden maskiert. |

Der Auftrag enthaelt immer: **Aenderungen nur ueber Branch, Tests und Pull Request,
nie direkt im Produktionscontainer.** Es werden keine Dateien, Issues, PRs oder
Reparaturlaeufe angelegt. Nur das vorhandene Audit und die internen
Rate-Limit-Zaehler in Redis sowie begrenzte GitHub-Zugangsmetadaten werden geschrieben.

### GitHub-Token einrichten

1. **System > Einstellungen > GitHub-Zugang (Codeanalyse und CI)** oeffnen.
   Nur persoenliche Systemadministratoren duerfen den Zugang verwalten.
2. GitHub: **Settings > Developer settings > Personal access tokens > Fine-grained
   tokens > Generate new token**. Name `ecp-admin-mcp-readonly`, Ablauf **90 Tage**.
3. Resource owner und Repository gemaess `GITHUB_REPOSITORY` waehlen (die Oberflaeche
   zeigt die konfigurierte Auswahl). **Only select repositories**, nur dieses Repo.
4. Repository permissions: **Contents = Read-only**, **Actions = Read-only**,
   **Pull requests = Read-only**, **Metadata = Read-only** (automatisch).
   Alles andere **No access**. Organisationen muessen Tokens eventuell freigeben
   (Status **pending**).
5. Token kopieren (nur einmal sichtbar), im Passwortfeld einfuegen, speichern und
   **Verbindung testen**. Repo-Erreichbarkeit, Berechtigungen und Rate-Limit pruefen.
   Bei aktiver Admin-2FA braucht Speichern/Ersetzen/Loeschen frische MFA.
6. Vor Ablauf rotieren: neues Token speichern/ersetzen, Verbindung testen, danach
   altes Token in GitHub widerrufen. Niemals in Chats, MCP-Auftraegen oder im Repo teilen.

Die DB speichert das Token Fernet-verschluesselt; die Oberflaeche zeigt nur
**gesetzt**, nie den Klartext. Keine Migration erforderlich. `GITHUB_READ_TOKEN`
bleibt als Fallback fuer Installationen ohne DB-Token verfuegbar; DB hat Vorrang.
Loeschen entfernt ausschliesslich das DB-Token und dessen Metadaten. Ein vorhandenes
Umgebungs-Token wird danach wieder verwendet. Dessen Rotation braucht einen
Container-Neustart. `GITHUB_REPOSITORY` und `GITHUB_API_TIMEOUT_S` bleiben Konfiguration.

GitHub liefert bei fine-grained Tokens das Ablaufdatum im Antwort-Header.
Ohne Header bleibt es unbekannt oder kann manuell hinterlegt werden (Zukunft,
maximal 400 Tage; Datumeingabe UTC). Automatische Metadaten werden nur bei
Aenderungen und hoechstens stuendlich aktualisiert; ein expliziter Verbindungstest
aktualisiert seinen Teststatus sofort. Die Anzeige verwendet die Org-Zeitzone.

Status: `nicht_gesetzt`, `nicht_getestet`, `ok`, `laeuft_bald_ab` (bis 30 Tage),
`abgelaufen`, `ungueltig` (401 oder Entschluesselungsfehler), `keine_berechtigung`
(403/404; Auswahl, Rechte und Organisationsfreigabe pruefen). Netz-/Rate-Limit-Probleme
melden `eingeschraenkt`. Bis 7 Tage erscheint eine Warnung, ab Ablauf ein Fehler.
Nach Wechsel des Fernet-Schluessels ein neues Token hinterlegen; ein unlesbares
DB-Token wird nicht still durch die Umgebungsvariable ersetzt.

Die MCP-Antworten enthalten `github_zugang` auf oberster Ebene mit Status,
Handlungshinweis und Hilfe. Lokale Ergebnisse bleiben bei Tokenproblemen benutzbar.
`deploy.version` meldet bekannte Tokenprobleme als stabilen Warnbefund `github.token`.

Ohne Token erfolgen unauthentifizierte GET-Anfragen fuer oeffentliche Daten mit
niedrigem GitHub-Rate-Limit. GitHub-Ergebnisse melden `eingeschraenkt`; private
Repos koennen `nicht_gefunden` liefern. 401/403/404/429, Timeout, Netzfehler und
Redirects liefern feste, geheimnisfreie Codes. Lokale Analyse und Auftragstext
bleiben moeglich. Kein automatischer Retry. Antworten sind pro Prozess fuer
60 Sekunden zwischengespeichert (maximal 128 Eintraege, nach Repo/Token getrennt).
Das gemeinsame GitHub-Budget betraegt **30 Netzwerkabrufe/Minute** in Redis;
Jobs-Unterabfragen zaehlen mit. Ohne konfiguriertes/erreichbares Redis werden
GitHub-Aufrufe geschlossen als `gedrosselt` abgelehnt, damit kein Prozess das
Gesamtkontingent unkoordiniert umgehen kann. Lokale Tools bleiben verwendbar.

### Sicherheitsmodell und Grenzen

- Feste API-Adresse `https://api.github.com`, nur explizite GET-Endpunkte,
  keine Redirects, keine konfigurierbaren Hosts/Proxies. Repo und SHA sind validiert.
  Keine Contents-/Raw-Endpunkte; vom Compare-Endpunkt mitgelieferte Diff-Felder
  werden verworfen. Keine lokalen Quelltexte werden an GitHub uebertragen.
- Hoechstens 512 KiB pro API-Antwort; gemeinsames Zeitbudget von maximal 8 Sekunden
  pro Client, zusaetzlich die bestehende harte Diagnose-Prozess-/MCP-Antwortfrist.
  Bei vielen Laeufen/Dateien oder Zeitueberschreitung ist das Ergebnis begrenzt.
  Ein positiver Dateitreffer gilt als geaendert; ein fehlender Treffer in einem
  unvollstaendigen Vergleich bleibt unbekannt (`null`).
- Nur existierende Python-Dateien unter dem lokalen `app/`-Baum; keine Traversals,
  Windows-Pfade oder Symlinks. Absolute Stacktrace-Pfade werden nur fuer den
  tatsaechlichen lokalen App-Baum akzeptiert. Funktionsnamen werden gegen den
  lokalen Python-AST geprueft. Quelltext wird ausschliesslich lokal verarbeitet.
- Keine freien Log-/Exception-Meldungen, Personen-/Einsatznamen, PR-Titel,
  Commit-Meldungen oder Autoren. Stattdessen technische Stellen, Zeilennummern,
  gepruefte eingebaute Exception-Typen (sonst `unbekannt`) und Zaehler. Befunde
  werden ueber eine feste Check-zu-Modul-Tabelle zugeordnet; das ist eine
  Zustaendigkeitshypothese und kein Beweis fuer eine fehlerhafte Datei.
- Das GitHub-Token ist aus Settings-Repr ausgeschlossen. Exakte Tokenwerte werden
  vor generischer Maskierung entfernt; Transportfehler und Antworten geben keine
  fremden Fehlertexte aus. Audit erhaelt keine freien Eingaben oder Evidenz.
- Der leichte Check `deploy.version` laeuft auch bei aktiven Einsaetzen. Unbekannte
  Deploy-SHA und nachweislicher Rueckstand zu main erzeugen Warnungen. GitHub-Ausfall
  erzeugt keinen Befund, sondern einen unvollstaendigen Check mit
  `unbekannt`-Hinweis; vorhandene Befunde werden dadurch nicht als behoben markiert.
  Netzwerkzugriff findet im bestehenden hart begrenzten Check-Prozess statt.
