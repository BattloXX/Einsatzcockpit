# Plan: Mail-Eingang auch über IMAP und Microsoft Graph + Posteingang je Organisation

## Kontext

Eingehende Mails werden heute nur über **Resend** verarbeitet: Webhook → `org_mail_eingang` → Inhalt abrufen → `ordne_zu` (Absenderprüfung, Einsatz-Zuordnung, Meldung + Anhänge, Autodruck).
In den Mail-Einstellungen gibt es bereits die Schalter „IMAP-Abruf vorbereiten“ (`OrgSmtpConfig.imap_*`) und „Mail.Read-Abruf vorbereiten“ (`OrgO365MailConfig.read_enabled`). Beide sind aber nur Absichts-Flags.

**Ziel:**
1. Ist einer der beiden Wege aktiviert, holt das Cockpit Mails aus dem eigenen Postfach (IMAP) bzw. dem Office-365-Postfach (Graph `Mail.Read`). Sie werden **genau wie Resend-Mails** verarbeitet.
2. Jede Organisation bekommt im Verwaltungsmenü einen **Posteingang**. Er zeigt für alle drei Wege, welche Mails angekommen sind und **was mit ihnen passiert ist**.

**Entscheidungen des Users (2026-10-11):**
- Das Postfach bleibt **unverändert**: nur lesen, keine Flags, kein Verschieben (IMAP `BODY.PEEK`, Graph `Mail.Read`).
- Beim ersten Aktivieren werden die Mails der **letzten 24 Stunden** übernommen, danach nur neue.
- Graph liest aus einem **eigenen Feld „Empfangspostfach“**. Ist es leer, gilt die Absenderadresse (`sender_address`).

## Ist-Stand

Quellenneutral sind bereits:
- `ordne_zu` (`app/services/mail_zuordnung_service.py:130`)
- der Warte-Loop (`mail_zuordnung_loop.py`)
- der Autodruck (`print_dispatcher.autoprint_mail_eingang_background`)
- `add_message` und `store_bytes_for_message(..., allow_datei=True)` (`media_service.py:422`)

Resend-gekoppelt sind diese Stellen, sie müssen abstrahiert werden:
1. Spalte `OrgMailEingang.resend_email_id` (String 64), Unique `uq_org_mail_eingang_resend` und `create_inbound_metadata` (`resend_inbound_service.py:43`).
2. `fetch_inbound_message` und die Aktion „erneut-abrufen“ (`ui_org_mail.py:662`).
3. Anhang-Bytes werden nie lokal gespeichert, sondern bei Bedarf über `get_resend_attachment` von Resend geholt. Das betrifft:
   - `mail_zuordnung_service._attachments` (:75)
   - `ui_org_mail.eingang_anhang` (:595)
   - `print_artifact_service._render_mail_eingang` (:691)
4. Die Aufbewahrung (`loesche_alte_eingaenge`) läuft nur über `OrgResendConfig`.
5. `absender` ist der rohe `from`-String. Bei „Name <a@b>“ schlägt die Freigabeprüfung in `_allowed` fehl.
6. `_allowed` im Modus „alle“ braucht `spf`/`dkim`/`dmarc`. IMAP und Graph müssen diese Werte aus `Authentication-Results` lesen.

Zum vorhandenen Posteingang (`/admin/mail/eingang`, `ui_org_mail.py:532ff`):
- Er ist nicht im Menü.
- Er hat kein Admin-Layout und zeigt nur Rohstatus.
- Es gibt weder Quelle noch Verlauf.

## Architektur

```
Resend-Webhook ─┐
IMAP-Poller ────┼─▶ mail_eingang_service.speichere_eingang(quelle, extern_id, normalisierte Mail)
Graph-Poller ───┘        │  (Dedup org+quelle+extern_id, Absender normalisiert, Auth-Ergebnisse,
                         │   Anhänge: Resend = Metadaten, IMAP/Graph = lokal gespeichert)
                         ▼
                 ordne_zu(...)  ─▶  Meldung + Anhänge (über AnhangQuelle) ─▶ Autodruck
                         │
                 Ereignis-Log je Schritt  ─▶  Posteingang-UI (Liste, Verlauf, Kanal-Status)
```

## Pakete (je ein Codex-Auftrag, gestapelt, getrennte Commits)

### P1 – Datenmodell, Quellen-Abstraktion, Ereignis-Log (Migration 0276)

**Migration `org_mail_eingang`:**
- Neue Spalte `quelle` String(10), NOT NULL, Default `resend`.
- `resend_email_id` wird zu `extern_id` String(255).
- Der Unique-Key wird zu `(org_id, quelle, extern_id)`.
- Neue Spalte `anhang_speicher` (NULL = Resend-lazy, sonst `lokal`).
- Neue Spalte `groesse_bytes`.

**Neue Tabelle `org_mail_eingang_ereignis`:**
- Spalten: id, org_id, eingang_id FK CASCADE, ts, typ, text, user_id.
- TenantScoped.
- Index (eingang_id, ts).

**`OrgSmtpConfig`:**
- Neue Felder: `imap_ordner` (Default `INBOX`), `imap_uidvalidity`, `imap_letzte_uid`, `imap_letzter_abruf_at`, `imap_letzter_fehler`, `imap_aktiv_seit`.

**`OrgO365MailConfig`:**
- Neue Felder: `read_postfach` (NULL = sender_address), `read_seit`, `read_letzter_abruf_at`, `read_letzter_fehler`, `read_aktiv_seit`.

**`OrgSettings`:**
- Neues Feld `mail_eingang_aufbewahrung_tage` (Default 90). Es wird aus `OrgResendConfig.inbound_retention_days` übernommen, wenn dort ein Wert vorhanden ist.

**Neuer Service `app/services/mail_eingang_service.py`:**
- `speichere_eingang(db, org_id, *, quelle, extern_id, ...) -> OrgMailEingang | None`: Dedup, `absender` per `email.utils.parseaddr` als nackte, kleingeschriebene Adresse, Ereignis `empfangen`.
- `ereignis(db, row, typ, text, user_id=None)`.
- `parse_authentication_results(header_werte) -> (spf, dkim, dmarc)`. Es zählt nur der **oberste** `Authentication-Results`-Header, denn den setzt der empfangende Server; tiefere Header können vom Absender gefälscht sein.
- `normalisiere_mime(raw_bytes)`: stdlib `email`, Policy `default`. Liefert Betreff, Text, HTML, Message-ID, To/Cc, Datum, Header und Anhänge; Inline-Bilder und `message/rfc822` werden als Datei-Anhang behandelt.

**Resend-Pfad umstellen (Verhalten unverändert):**
- `create_inbound_metadata` und `fetch_inbound_message` verwenden `extern_id` und `quelle="resend"`.
- `absender` wird normalisiert.
- Ereignisse werden geschrieben.

**Ereignisse in `ordne_zu` und `_assign`:**

| Typ | Wann |
|---|---|
| `abgerufen` | Inhalt geholt |
| `abruf_fehler` | Abruf fehlgeschlagen |
| `absender_abgelehnt` | Absender nicht freigegeben, mit Grund |
| `mehrdeutig` | Zuordnung nicht eindeutig |
| `wartend` | „wartet bis HH:MM“ |
| `zugeordnet` | Einsatz-Nr., Meldung-ID, auto/manuell |
| `anhang_fehler` | Anhang konnte nicht gespeichert werden |
| `rueckblick_abgelaufen` | im Loop |
| `gedruckt` | im Autodruck |
| `limit_ueberschritten` | Stundenlimit erreicht |

Dazu kommen die UI-Aktionen `gelesen`, `archiviert`, `geloescht` (im Audit), `absender_freigegeben`, `uebernommen` und `erneut_verarbeitet`.

**Aufbewahrung:**
- `loesche_alte_eingaenge` läuft über alle Orgs mit Einträgen.
- Grundlage ist `OrgSettings.mail_eingang_aufbewahrung_tage`.
- Lokale Anhangdateien werden mitgelöscht.

**Tests:**
- Migrationstest 0276: Upgrade mit Bestandsdaten, Unique-Key.
- `speichere_eingang` (Dedup je Quelle, Absender „Name <a@b>“ → `a@b`).
- `parse_authentication_results` (oberster Header, fehlender Header, gefälschter tieferer Header).
- Die Resend-Tests laufen unverändert grün; dafür nur die Spaltennamen anpassen.

### P2 – Anhang-Abstraktion und lokaler Speicher

- Neues Modul `app/services/mail_eingang_anhang.py`:
  - `speichere_lokal(row, anhaenge)` legt die Dateien unter `MEDIA_STORAGE_DIR/mail_eingang/<org_id>/<eingang_id>/<n>` ab. Limits: `MAX_ATTACHMENT_BYTES` je Datei, 35 MB je Mail. Zu große Anhänge werden nur als Metadaten mit `zu_gross: true` und einem Ereignis vermerkt. Die `anhang_json`-Einträge haben die Form `{id: "l<n>", filename, content_type, size, lokal: true}`.
  - `async lade_anhang(db, row, anhang_id) -> (bytes, filename, content_type) | None` verzweigt nach Speicherart: `lokal` liest die Datei, sonst gilt der bisherige Resend-Weg (`get_resend_attachment` + Download).
- Diese drei Stellen nutzen künftig `lade_anhang`:
  - `mail_zuordnung_service._attachments`
  - `ui_org_mail.eingang_anhang`
  - `print_artifact_service._render_mail_eingang`; dabei fällt auch die fehlende Paginierung dort weg.
- Für alle Zugriffe gelten weiter `safe_attachment_filename` und `attachment_content_type`.
- **Tests:**
  - lokal speichern und laden
  - Limits und `zu_gross`
  - Pfad-Traversal im Dateinamen
  - Resend-Pfad unverändert
  - Zuordnung mit lokalem Anhang erzeugt `MessageMedia`

### P3 – IMAP-Abruf

- Neues Modul `app/services/mail_imap_poller.py`. Muster: `lis_loop` mit Tick 15 s, Intervall je Org `max(60, …)` und isolierten Fehlern je Org.
  - Org-Paare laden: `imap_enabled`, Host gesetzt, `user`/`password_enc` vorhanden (eigene Prüfung, nicht `_org_smtp_cfg`) und `mail_eingang_modus != "aus"`.
  - `imaplib.IMAP4_SSL` bzw. `IMAP4` + STARTTLS in `asyncio.to_thread`, mit Socket-Timeout. Ordner `imap_ordner` mit `EXAMINE` (read-only) öffnen.
  - **Startpunkt:**
    - Erster Lauf, oder `imap_uidvalidity` hat sich geändert: `SEARCH SINCE <heute-1>`, dann nach `INTERNALDATE` ≥ jetzt−24 h filtern. `imap_letzte_uid` wird auf die höchste UID gesetzt.
    - Danach `UID SEARCH UID <letzte+1>:*`.
  - Je Lauf höchstens N = 50 Mails. Zuerst `RFC822.SIZE` prüfen, bei > 35 MB nur die Header holen und das Ereignis `zu_gross` schreiben.
  - Die Mail wird mit `BODY.PEEK[]` geholt, damit sich nichts am Postfach ändert.
  - `extern_id` = `"{uidvalidity}:{uid}"`.
  - Danach `normalisiere_mime` → `speichere_eingang(quelle="imap")` → lokale Anhänge, `abgerufen_at`, Auth-Werte → `ordne_zu` → Autodruck.
  - Das Stundenlimit (`mail_eingang_max_pro_stunde`) beendet die Runde. Der Rest kommt in der nächsten Runde dran; es geht nichts verloren, weil die UID-Marke nur bis zur letzten verarbeiteten Mail vorrückt.
  - Status wird in `imap_letzter_abruf_at` und `imap_letzter_fehler` gesetzt. Passwörter oder Hosts mit Zugangsdaten landen nie im Log.
- `imap_aktiv_seit` wird beim Einschalten gesetzt. Das löst den einmaligen Rückblick über 24 h aus.
- Globaler Schalter `settings.MAIL_IMAP_POLL_ENABLED` (Default true). Den Loop in `main._start_background_loops` eintragen, damit er unter dem Leader-Lock läuft.
- **Tests** mit einem Fake-`imaplib`:
  - Erstlauf mit 24-h-Fenster
  - Folgelauf nur mit neuen UIDs
  - Wechsel der UIDVALIDITY
  - Dedup bei Wiederholung
  - Stundenlimit lässt die Marke stehen
  - Verbindungsfehler setzt `imap_letzter_fehler`
  - übergroße Mail
  - `EXAMINE` und `BODY.PEEK`: keine schreibenden Befehle
  - Loop-Registrierung

### P4 – Graph-Abruf (`Mail.Read`)

- Neues Modul `app/services/mail_graph_poller.py`. Es nutzt `o365_mail_service._acquire_token` (Token-Cache; bei 401/403 Token erneuern und einmal wiederholen, wie in `send_via_graph`).
  - Postfach = `read_postfach or sender_address`, geprüft wie eine E-Mail-Adresse.
  - `GET /users/{postfach}/mailFolders/inbox/messages?$filter=receivedDateTime ge {seit}&$orderby=receivedDateTime asc&$top=25&$select=id,internetMessageId,receivedDateTime,subject,from,toRecipients,ccRecipients,hasAttachments`. Paginierung über `@odata.nextLink`, höchstens 50 Mails je Lauf.
  - Inhalt per `GET /messages/{id}/$value` (MIME). Damit gelten Body, Anhänge und `Authentication-Results` gleich wie bei IMAP, über `normalisiere_mime`.
  - `extern_id` = Graph-Message-ID (bis 255 Zeichen).
  - **Startpunkt:** `read_seit` = `read_aktiv_seit` − 24 h beim ersten Lauf. Danach gilt `receivedDateTime` der letzten verarbeiteten Mail; Mails mit gleichem Zeitstempel fängt der Dedup ab.
  - Fehler werden in `read_letzter_fehler` festgehalten. Bei 403 lautet der Hinweis: „Application-Permission Mail.Read fehlt oder Zugriff per ApplicationAccessPolicy gesperrt“.
- Globaler Schalter `settings.MAIL_GRAPH_POLL_ENABLED` und `O365_MAIL_ENABLED`.
- **Tests** mit gepatchtem `httpx.AsyncClient`:
  - Erstlauf über 24 h
  - Paginierung
  - Dedup
  - 401 → neues Token
  - 403 → Fehlertext
  - MIME mit Anhang erzeugt lokale Anhänge
  - Loop-Registrierung

### P5 – Posteingang-Oberfläche und Einstellungen

**Menü:** In `app/templates/admin/_layout.html` unter „Schnittstellen“ (nach „Mail-Versand“) kommt der Eintrag **„📥 Posteingang“** mit Badge für offene Mails (`neu`, `posteingang`, `wartend`). Er ist sichtbar für `org_admin` und `admin`; System-Admins wählen die Org wie bisher per `org_id`.

**Liste (`/admin/mail/eingang`):**
- Auf `admin/_layout.html` umstellen.
- Kopfzeile mit **Kanal-Status** für alle drei Wege: Resend, IMAP und Office 365, jeweils aktiv/aus, letzter Abruf, letzter Fehler, Empfangsadresse bzw. Postfach.
- **Filter:** Quelle, Ergebnis, Zeitraum und Suche über Betreff/Absender. Die Statuswerte bekommen deutsche Labels.
- **Spalten:** Empfangen, Quelle-Chip, Absender, Betreff, Anhänge (Anzahl), **Ergebnis** in Klartext:

| Ergebnis | Klartext |
|---|---|
| zugeordnet | „In Einsatz F14 übernommen (automatisch)“ mit Link |
| wartend | „Wartet auf Einsatz bis 14:35“ |
| abgelehnt | „Posteingang – Absender nicht freigegeben“ |
| mehrdeutig | „Mehrere Einsätze aktiv“ |
| abruf_fehler | „Abruf fehlgeschlagen“ |
| archiviert | „Archiviert“ |

- Mobil (≤ 760 px) als Kartenliste.
- Aktualisierung alle 30 s per HTMX wie bisher.

**Detail:**
- Kopf mit Quelle, Ergebnis-Box und **Verlauf als Zeitleiste** aus `org_mail_eingang_ereignis`.
- Text bzw. HTML im bestehenden Sandbox-iframe. Anhänge kommen über `lade_anhang`; übergroße sind als „nicht übernommen“ markiert.
- Bestehende Aktionen. „Erneut abrufen“ heißt künftig **„Erneut verarbeiten“**: Resend holt den Inhalt neu, IMAP und Graph führen nur `ordne_zu` erneut aus, weil der Inhalt lokal liegt.
- Alle POST-Formulare mit `_csrf`.

**Einstellungen (`settings_org_mail.html`):**
- Die Hinweise „(Vorbereitung)“ und „nicht implementiert“ entfallen.
- Neue Felder:
  - IMAP: `imap_ordner`
  - Graph: `read_postfach`
- **„Verbindung testen“** für beide Wege, als eigener POST mit Login und `EXAMINE` bzw. Lesen einer Nachricht ohne Speichern; das Ergebnis erscheint als Toast.
- Die Aufbewahrungsdauer wird zentral in den Posteingang-Einstellungen gepflegt.
- Beim Einschalten werden `*_aktiv_seit` gesetzt und die Marken zurückgesetzt.

**Tests:**
- Menüeintrag sichtbar
- Liste filtert nach Quelle
- Ergebnistexte
- Detail zeigt den Verlauf
- „Erneut verarbeiten“ je Quelle
- Verbindungstest mit Fake
- Cross-Org: Org A sieht keine Mails von Org B, inklusive Anhang-Download
- Smart-Quote-Check auf Templates

### P5b – Adress-Übersicht oben auf der Mail-Seite der Organisation (`/admin/mail`)

Wunsch des Users (2026-10-11): Ganz oben auf `settings_org_mail.html` steht, **welche Adressen das System dieser Org zum Senden und Empfangen bereitstellt** und welche tatsächlich verwendet werden.

**Neue Service-Funktion** `mail_adressen_uebersicht(db, org) -> dict` in `app/services/org_mail_adresse.py`. Sie wird auch für den Kanal-Status im Posteingang (P5) wiederverwendet.

- **Systemadressen (Einsatzcockpit):**
  - Absender: `absender_adresse(org, from_domain)`. Wird nur angezeigt, wenn globales Resend aktiv ist (`get_resend_cfg`); sonst „nicht verfügbar“.
  - Empfang: `empfangs_adresse(org, inbound_domain(db))` plus Alias `<slug>@<inbound_domain>`, mit Status: Empfang aktiv/aus (`mail_eingang_modus`) und Hinweis, dass sie als Reply-To gesetzt wird, wenn der Empfang aktiv ist.
- **Eigene Wege der Org:**
  - Office 365: Absender `sender_address`, Empfangspostfach `read_postfach or sender_address`, Lesen aktiv/aus.
  - Eigenes Resend: `from_addr`, Webhook-Empfang aktiv/aus.
  - Eigener SMTP: `from_addr`, IMAP-Postfach (`user`@`imap_host`, Ordner), Abruf aktiv/aus.
- **Tatsächlich verwendet:**
  - Versand: der erste wirksame Weg in der Reihenfolge von `mail_service.deliver` (O365 → Resend der Org → Resend global → SMTP der Org → SMTP global → „kein Versand, nur Log“), samt Absenderadresse; die übrigen konfigurierten Wege erscheinen als „Fallback“. Die Logik wird aus `deliver` als reine Funktion herausgezogen (z. B. `versandweg(db, org_id)`), damit Anzeige und Versand nicht auseinanderlaufen.
  - Empfang: alle aktiven Eingangswege (Resend-System, Resend-Org, IMAP, O365).

**Darstellung:**
- Karte „Ihre Mail-Adressen“ über allen Formularen, jede Adresse mit „Kopieren“-Button.
- Chips: „wird verwendet“, „Fallback“, „aus“, „nicht konfiguriert“.
- Mobil einspaltig.
- Der bisherige Einzeiler „Ihre Empfangsadresse …“ (Zeile ~64) geht in der Karte auf.

**Tests:**
- Übersicht bei nur globalem Resend: Systemadressen, Versand über Resend global.
- Mit O365 aktiv: Versand über O365, Resend als Fallback.
- Ohne jede Konfiguration: „kein Versand“.
- `versandweg` stimmt mit `deliver` überein; die vorhandenen deliver-Tests laufen weiter grün.
- Seite rendert die Karte.

### P6 – Doku

- `docs/wiki/Administration-Mail*.md` bzw. die vorhandene Mail-Eingang-Seite: IMAP und Graph einrichten, Azure-Permission `Mail.Read` plus Empfehlung ApplicationAccessPolicy, Rückblick 24 h, Postfach bleibt unverändert, Posteingang lesen.
- Den Abschnitt „Umsetzungsstand“ im Plan `docs/plans/mail-eingang-einsatz-plan.md` ergänzen.

## Ablauf und Regeln für Codex

- Jeder Auftrag mit `--model gpt-5.6-terra --write`, kein `ruff format`/`--fix`, nur gezielte Testdateien, keine volle Suite.
- Die Testliste jedes Pakets wird im Diff abgehakt.
- Ein Worktree `feat/mail-eingang-imap-graph`, P1 bis P6 als getrennte Commits.
- Zwei PRs:
  - **A** = P1 + P2 (Umbau ohne neues Verhalten)
  - **B** = P3 bis P6 inklusive P5b
- Merge erst nach grüner CI (pytest gegen MariaDB inklusive Migration 0276).

## Kritische Dateien

| Bereich | Dateien |
|---|---|
| Modelle | `app/models/org_mail.py`, `app/models/master.py` (OrgSettings) |
| Migration | `alembic/versions/0276_mail_eingang_quellen.py` |
| Services | `resend_inbound_service.py`, `mail_zuordnung_service.py`, `mail_zuordnung_loop.py`, `print_artifact_service.py`, `print_dispatcher.py`, `o365_mail_service.py`; neu: `mail_eingang_service.py`, `mail_eingang_anhang.py`, `mail_imap_poller.py`, `mail_graph_poller.py` |
| Router | `app/routers/mail_inbound_webhook.py`, `app/routers/ui_org_mail.py`, `app/main.py` (Loops), `app/config.py` (Schalter) |
| Templates | `admin/_layout.html`, `admin/mail_eingang*.html`, `admin/_mail_eingang_content.html`, `admin/settings_org_mail.html` |

## Verifikation

1. `ruff check app/`, `mypy app/`, gezielte pytest-Dateien je Paket. Vor dem Merge die volle Suite lokal plus CI gegen MariaDB.
2. IMAP lokal end-to-end:
   - Einen Fake-IMAP-Server (z. B. `aiosmtpd` plus Test-IMAP, oder `imaplib` gemockt im Integrationstest) mit einer Mail inklusive PDF-Anhang aufsetzen.
   - Einen aktiven Einsatz und einen freigegebenen Absender anlegen.
   - Erwartung: eine Meldung mit PDF im Einsatz, Ereignisse `empfangen → abgerufen → zugeordnet` im Posteingang.
3. Graph: der Integrationstest mit gemockten Graph-Antworten (Liste, `$value`-MIME) leistet dasselbe.
4. Browser (Playwright, 1280 und 390 px):
   - Menü → Posteingang
   - Kanal-Status
   - Filter „IMAP“
   - Detail mit Zeitleiste
   - Anhang-Download
   - „Erneut verarbeiten“
5. Nach dem Deploy auf test, mit echten Postfächern:
   - IMAP und O365 aktivieren
   - „Verbindung testen“
   - Testmail schicken und Ergebnis im Posteingang prüfen
   - Prüfen, dass die Mail im Postfach ungelesen bleibt

## Umsetzungsstand

- P1–P6 umgesetzt: Eingangsspeicherung, Zuordnung und Darstellung, Resend-, IMAP- und Graph-Abruf,
  Einstellungen mit Verbindungstests sowie Adress-Übersicht und Dokumentation.
- Offen: Live-Test mit einem echten IMAP-Postfach und einem O365-Tenant.
