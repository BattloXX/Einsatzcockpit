# Infoscreen-Agent: Releases, Installation und Updates (S6)

## Vertrag und Vertrauensgrenze

Verbindlich ist `docs/installer-protocol.md` im Agent-Repository sowie der
S6/A6-Updatevertrag. Alle Artefakte kommen vom ECP; private Signaturschluessel
bleiben in CI. `key_id` waehlt den serverseitig konfigurierten Ed25519-Schluessel.
Das Manifest signiert exakt diese sieben UTF-8/ASCII-Zeilen mit LF dazwischen,
ohne abschliessendes LF:

```text
ec-infoscreen-agent
version=<version>
channel=<stable|beta>
arch=<amd64|arm64>
sha256=<64 kleine Hex-Zeichen>
size=<positiver Dezimalinteger>
min_version=<version>
```

`signature` ist Standard-Base64 einer 64-Byte-Ed25519-Signatur. `key_id`,
`download_url` und `critical` sind gemaess Vertrag **nicht signiert**. Das weicht
von der aelteren Formulierung in Plan 4.1 ab (dort: signiertes `critical`).
Der Downloadpfad bleibt relativ, ohne Redirects. Authentifizierte Antworten,
auch Fehler und 304, tragen `Cache-Control: no-store`; Paketantworten liefern
`Content-Length` und immer die gesamte Datei, auch bei Range-Anfragen.

## CI-Upload und Ablage

Systemadministration: **Updates & Backups > Agent-Releases**
(`/admin/agent-releases`). Dort ein CI-Upload-Token erzeugen und unmittelbar
in den Secret-Speicher der CI uebernehmen. Der Wert erscheint genau einmal;
in `infoscreen_release_upload_token` steht ausschliesslich sein SHA256-Hash.
Der Scope gilt nur fuer `POST /api/v1/infoscreen-agent/releases` und berechtigt
weder zu Geraete- noch zu Org- oder Installerzugriffen. Widerruf wirkt ab Commit.

Upload als Multipart mit Bearer-CI-Token:

- `metadata`: JSON `{version, channel, arch, sha256, size, min_version, key_id, signature}`
- `package`: `.deb` (maximal 512 MiB)
- `install_sh`: vollstaendige `install.sh` (maximal 1 MiB)
- `signature_install_sh`: Standard-Base64, Ed25519 ueber **die unveraenderten
  Skriptbytes**, kein Hashtext, kein LF angehaengt. Textfeld oder kleine Datei.

Der Server verifiziert beide Signaturen, Groesse/Hash und die ar/control-Daten
`Package=ec-infoscreen-agent`, `Architecture`, `Version`. Er fuehrt keine
Paketprogramme aus und extrahiert keine Archivpfade. Control-Archive (tar,
gz/xz/bz2/zstd) sind auch nach Dekompression auf 2 MiB begrenzt.
Versionen beginnen mit einer Ziffer, maximal 100 ASCII-Zeichen aus
`A-Za-z0-9.+:~-`; die Sortierung und Mindestversion verwenden Debian-Reihenfolge
(einschliesslich Epoch, Revision und Tilde). `(version, channel, arch)` ist eindeutig.
Ungueltige Uploads: 422 mit festem Code, Duplikat: 409, ungueltiger/widerrufener
CI-Bearer: 401, Drossel: 429, zu grosser HTTP-Body: 413.

Ablage: zufaellige, serverseitige Artefaktnamen, Verzeichnis 0700 bei Anlage,
Dateien 0600; temporaer schreiben, fsync, atomar umbenennen, Verzeichnis fsync.
Vor DB-Commit abgebrochene Transaktionen entfernen Dateien. Ein harter
Prozessabbruch zwischen Umbenennen und Commit kann verwaiste Dateien
hinterlassen; diese sind ohne DB-Zeile nicht abrufbar. Sicherungen muessen DB
und Release-Ablage zusammen umfassen. Die Ablage darf kein Static-Mount sein.
Alte Releases werden fuer Ruecknahme/Diagnose aufbewahrt, nicht automatisch geloescht.

## Freigabe, Canary, Pin und Rueckzug

Neue Releases starten immer mit `rollout_percent=0`, `blocked=false`.
Freigeben: 10/50/100 % im Plattform-Tab; 0 % nimmt die Freigabe zurueck.
`blocked=true` sperrt Manifestauswahl und Paketdownload sofort; ein bereits
auf dem Geraet gespeichertes Paket kann der Server nicht zurueckholen.
Entsperren behaelt den gewaehlten Rollout-Prozentsatz bei.

Erstinstallation: hoechste freigegebene Debian-Version fuer angefragten Kanal
und Architektur; **freigegeben bedeutet strikt `rollout_percent > 0` und
`blocked=false`**. Kein separater `stable-latest`-Marker. Canary, Geraete-Pin,
Updatesperre und `min_version` gelten erst fuer Updates. Der Installer kann
stable oder beta anfordern und speichert den Kanal anschliessend beim Pairing.

Updates: Kanal und Architektur muessen passen, Release darf nicht gesperrt
sein, Geraet darf nicht update-gesperrt sein. Ein Pin schraenkt auf genau diese
Version ein und umgeht keine Sicherheits-/Rolloutregel. Der stabile Bucket ist
`int(sha256(ASCII(str(device_id) + version)).hexdigest(), 16) % 100`;
zulassen, wenn Bucket `< rollout_percent`. Es wird die hoechste passende
Version oberhalb `current` gewaehlt, deren `min_version <= current` ist.
Dadurch kann der Server ein notwendiges Zwischenrelease anbieten.

`GET /api/v1/infoscreen-agent/update/manifest?arch=...&current=...` mit
Device-Bearer liefert genau die A6-Felder: `update_available`, `manifest|null`,
`update_allowed`, `locked`, `pinned_version`, `maintenance_window`,
`critical_allowed`, `check_interval_h=6`. Bei aktivem Einsatz/GSL/Nachlauf
ist `update_allowed=false`. Nachlauf verwendet den groessten konfigurierten
Nachlauf aktiver Displays der Org (ohne Displays: 10 Minuten).
Das Wartungsfenster wird in Org-Wanduhrzeit geliefert (Default 02:00–04:30,
Zeitzone `FireDept.timezone`, Fallback Europe/Vienna). Fenster und
`critical_allowed` werden vom Agent fuer die Installation ausgewertet;
`update_available` bleibt auch ausserhalb des Fensters ein Angebot.
Kritische Updates umgehen weder Einsatzschutz noch Pin, Rollout oder Sperren.
Die Org muss die Fenster-Ausnahme ausdruecklich aktivieren (Default false).

`download_url` ist `/api/v1/infoscreen-agent/download/artifact/<id>`;
bei Updates kommt `?current=<version>` hinzu. Das ist kein Zugangstoken:
der Device-Bearer bleibt Pflicht. `current` ist derselbe vom Geraet gemeldete
Versionswert wie beim Manifest und erlaubt den Download eines Zwischenreleases
auch vor dem naechsten Heartbeat. Ohne Query gilt die zuletzt bestaetigte
Agent-Version (oder `0`). Der Agent/root-Helfer muss die tatsaechlich lokale
Version und alle Signatur-/Paketregeln erneut pruefen. Download prueft die
aktuelle Sperre, Freigabe, Kanal-, Pin-, Bucket- und Mindestversionsregel erneut.

Nach Release-Policy-Aenderungen erhalten aktive Geraete im betroffenen Kanal
`{"type":"update_available"}` ueber den vorhandenen Mehrworker-Kanal `ec:isa`.
Geraete-Policy, Org-Policy und **Jetzt pruefen** senden denselben Hinweis nach
Commit. Der Agent fragt danach den Snapshot ab; Events enthalten keine Releases
oder Einsatzdaten.

## Installer, Pairing und Status

`GET /infoscreen-agent/install.sh` liefert das zuletzt hochgeladene, nicht
blockierte, aktuell signatur- und hashgepruefte Skript. Oeffentlich, keine Cookies,
`text/x-shellscript`, `Cache-Control: public, max-age=60`, 30/min/IP, sonst 404.
Der harte Installer-Schalter sperrt auch diese Route. Das Dialog-Banner
verschwindet nur bei aktiviertem Schalter, gueltigem Skript und mindestens
einem freigegebenen stable-Release. Beta-only reicht fuer den Standarddialog nicht.

`GET /api/v1/infoscreen-agent/download/latest?arch=amd64&channel=stable`
verlangt einen ungebrauchten, nicht abgelaufenen Pairing-Bearer und ist auf
10/min/IP begrenzt. Paketdownload hat eine eigene 10/min/IP-Drossel.
`peek_pairing_code` prueft den Org-Nachweis ohne Seiteneffekte. Weder Manifest
noch Paketdownload verbrauchen den Code; erst `/pair` loest ihn atomar ein.
Abgelaufene, widerrufene und verbrauchte Codes: 401; kein passendes Release: 404.
Plattformweite Release-Artefakte enthalten keine Org-Daten. Release- und
CI-Token-Tabellen sind explizit vom Org-Export ausgeschlossen. IDs/Org-Angaben aus
Queries oder Bodies veraendern niemals den durch Code/Device-Token bewiesenen Tenant.

`POST /pair` akzeptiert zusaetzlich `name` (1–100 Zeichen, keine Steuerzeichen)
und `channel` (stable/beta). `POST /unpair` widerruft mit Compare-and-Swap
atomar das authentifizierte Token, entfernt Codes/Presence, auditiert und
trennt WebSockets workeruebergreifend. Antwort `200 {}`, Replay: 401.

`POST /update/result`: `version`, `status=installing|ok|failed|rolled_back`,
`error_code=null` oder genau einer aus `download_failed`, `hash_mismatch`,
`signature_invalid`, `arch_mismatch`, `package_mismatch`, `downgrade_blocked`,
`install_failed`, `health_failed`, `rollback_failed`, `power_loss_recovered`.
Unbekannte Felder/Codes/Status: 422. Der Geraetestatus wird gespeichert;
nur `ok` bestaetigt `agent_version=version`, fehlgeschlagene Zielversionen
ueberschreiben keine installierte Version. Audit/Device-Event enthalten nur Codes.

## Settings und Rotation

- `INFOSCREEN_AGENT_RELEASE_STORAGE_DIR`: Default `app_storage/infoscreen_agent_releases`.
- `INFOSCREEN_AGENT_RELEASE_PUBKEYS`: JSON-Objekt `{key_id: PEM_PUBLIC_KEY}`;
  leer bedeutet Upload gesperrt. Ausschliesslich oeffentliche Ed25519-PEMs.
- `INFOSCREEN_AGENT_INSTALLER_ENABLED`: Default false; harter Notausschalter
  fuer Installer und Dialog-Freigabe. Pakete/Updates bleiben getrennt nutzbar.
- `INFOSCREEN_AGENT_KEY_FINGERPRINT`: expliziter Fingerprint fuer den Einzeiler;
  wenn leer, `sha256:<hex>` ueber DER des ersten konfigurierten Public Keys.

Rotation: neuen oeffentlichen Schluessel neben dem alten konfigurieren, die
Geraete vorab mit dem neuen Trust-Key ausstatten (Agent-Verantwortung), dann
neue Releases damit signieren. Fuer Erstinstallationen muss der Fingerprint
zum **im zuletzt ausgelieferten Skript eingebetteten** Schluessel passen:
explizit setzen oder den aktiven Schluessel als ersten Eintrag konfigurieren.
Alte Keys erst nach Migration aller Geraete entfernen. Nicht mehr vertraute
Skripts werden nicht ausgeliefert. Private Keys, CI-Bearer, Device-Bearer und
Pairing-Codes gehoeren weder in Anwendungs-/Accesslogs noch in Artefaktpfade.
CI muss ausschliesslich das gepruefte Quellskript ohne eingebaute Secrets signieren.

Migration **0358**, `down_revision=0357`: Update-Diagnose, Org-Fenster,
Release-Skript/Key/Critical und Upload-Tokens. Keine neuen Fremdschluessel.
MariaDB erweitert Versionsspalten auf 100 Zeichen; SQLite benoetigt dafuer
keinen Table-Rebuild, weil VARCHAR-Laengen dort nicht erzwungen werden.
Deployment, Schluesselbereitstellung und Hardware-Abnahme bleiben beim Betrieb.

## Abgleich mit dem aelteren Plan

Der explizite S6/A6-Vertrag hat Vorrang: `critical` bleibt unsigniert, das
Wartungsfenster wird vom Agent ausgewertet und `update_allowed` beschreibt
Org-Aktivitaet. Die relative Download-URL enthaelt kein separates signiertes,
kurzlebiges Zugangstoken (anders als der Vorschlag in Plan 4.2); Bearer und
jeweils aktuelle DB-Freigabe sind bei jedem Download Pflicht. Erstinstallationen
sind zusaetzlich an die 15-minuetige Lebenszeit des ungebrauchten Codes gebunden.
Der bereitgestellte Plan endet mit Abschnitt 7; ein Abschnitt 9 ist darin nicht enthalten.
