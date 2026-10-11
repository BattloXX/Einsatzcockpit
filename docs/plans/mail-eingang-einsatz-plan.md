# Plan: E-Mail-Eingang über Resend → Meldungen im Einsatz

> Status: Entwurf zur Freigabe (2026-10-10). Baut auf dem in Arbeit befindlichen Posteingang
> (Branch `feat/resend-posteingang`, Migration 0271) auf und erweitert ihn.

## 1. Ziel

1. **Eine Adresse je Organisation, aus Typ und Name gebildet:** `<typ>.<slug>@<domain>`,
   z. B. `feuerwehr.wolfurt@einsatzcockpit.com`. Sie ist **Absender** (bisher `<slug>@…`) und
   **Standard-Empfangsadresse**.
2. **Mails während oder kurz vor einem Einsatz werden zur Meldung** des Einsatzes:
   Betreff = Titel, Text = Text, Anhänge = Anhänge der Meldung. Was nicht angezeigt werden kann
   (Office-Dateien, ZIP, …), bleibt als **Download** erhalten.
3. Alles, was nicht eindeutig zuordenbar oder nicht vertrauenswürdig ist, landet im
   **Posteingang** (Admin) und kann dort manuell in einen Einsatz übernommen werden.

## 2. Ist-Stand (geprüft)

| Thema | Stand | Beleg |
|---|---|---|
| Absender | `f"{org.slug}@{from_domain}"` über globalen Resend-Schlüssel | `app/services/mail_service.py::_resend_from_addr_for_org` |
| Organisationstyp | `FireDept.bos` ∈ {Feuerwehr, Rotes Kreuz, Polizei, Bauhof, Privat}; `FireDept.slug` eindeutig (z. B. `wolfurt`) | `app/models/master.py:23,32,35` |
| Resend global/je Org | Global: SystemSettings `resend_enabled/_api_key/_from_domain`; je Org: `OrgResendConfig` | `mail_service.get_resend_cfg`, `app/models/org_mail.py` |
| Meldung | `Message(title, detail, status, author_name, column messages)` + `MessageMedia` (Bild/PDF/Video) | `app/models/incident.py:469,527` |
| Meldung anlegen | Logik steckt in der Route `create_message` (Karte, Audit, WebSocket) | `app/routers/ui_incident.py:2283` |
| Anhänge speichern | `media_service.store_upload_for_message(file, message, user, db, org_id)` – braucht einen User, nur Bild/PDF/Video | `app/services/media_service.py:379` |
| Einsatz anlegen | `incident_service.create_incident` – 6 Aufrufer (UI, API, LIS, DIBOS, Serienalarm, Probe) | `app/services/incident_service.py:282` |
| Posteingang | Org-Webhook `/mail/webhook/resend-inbound/{org_token}`, Tabelle `org_mail_eingang`, Admin-Seiten (in Arbeit, Codex) | Branch `feat/resend-posteingang` |
| Resend-Empfang | MX auf die Domain → Webhook `email.received` (nur Metadaten) → Inhalt/Anhänge per `GET /emails/receiving/{id}` und `/attachments`; Svix-Signatur | Resend-Doku |

## 3. Entscheidungen (mit Empfehlung)

| # | Entscheidung | Empfehlung / Begründung |
|---|---|---|
| E1 | **Adressschema** | `<typ>.<slug>@domain`; `typ` = `bos` kleingeschrieben, Umlaute ersetzt, Leerzeichen → `-` (`rotes-kreuz.wolfurt`). Alte Form `<slug>@domain` bleibt als **Alias** empfangsfähig (bestehende Absender/Antworten). |
| E2 | **Empfangsdomain** | **Entschieden (2026-10-10): `get.einsatzcockpit.com`**, konfigurierbar über SystemSettings `resend_inbound_domain` (Standard dort `get.einsatzcockpit.com`, sonst `resend_from_domain`). Die Adresse `<typ>.<slug>@get.einsatzcockpit.com` ist Reply-To und Empfangsadresse; der Absender kann auf der Versanddomain bleiben. Hintergrund: Ein MX-Eintrag auf Resend für die *Hauptdomain* `einsatzcockpit.com` leitet **jede** Mail dieser Domain an Resend um – vorhandene Postfächer (z. B. info@, Support) würden nicht mehr ankommen. Empfehlung: konfigurierbare **Empfangsdomain** (SystemSettings `resend_inbound_domain`, Standard = `resend_from_domain`). Läuft Mail der Hauptdomain sonst anderswo, eine Subdomain wie `mail.einsatzcockpit.com` verwenden und die Adresse als **Reply-To** setzen. |
| E3 | **Webhook** | **Ein** systemweiter Endpunkt `/mail/webhook/resend-inbound` mit eigenem Signing-Secret (SystemSettings, verschlüsselt); Organisation über die Empfängeradresse (`to`/`received_for`). Der org-spezifische Webhook aus dem Posteingang bleibt für Organisationen mit eigenem Resend-Konto. |
| E4 | **Vertrauen/Absender** | Mails aus dem Internet dürfen **nicht ungeprüft** in einen Einsatz. Org-Einstellung „Automatische Übernahme“: **nur freigegebene Absender** (Standard; Liste von Adressen/Domains, z. B. `@lls.at`, `@rotes-kreuz.at`) · **alle** (nur mit SPF/DKIM/DMARC-Pass) · **aus**. Leere Freigabeliste = alles landet im Posteingang. |
| E5 | **Zuordnung zum Einsatz** | Genau **ein** aktiver Einsatz der Organisation (`primary_org_id`, Status `active`, nicht Übung wenn Mail nicht Übung…): automatisch. **Mehrere** aktive: Posteingang „mehrdeutig“ (Org-Option: „neuesten nehmen“). **Keiner**: Mail wird vorgemerkt und beim Anlegen eines Einsatzes **innerhalb des Rückblickfensters** (Standard 30 min, 5–240) zugeordnet. Nach Einsatzende keine automatische Zuordnung. |
| E6 | **Nicht darstellbare Anhänge** | Neue Medienart `datei` (Download-only): Dateiname bereinigt, `Content-Disposition: attachment`, `nosniff`, ausführbare Typen (exe, js, html, svg, …) als `application/octet-stream`. Größenlimit je Datei/Mail; zu große Anhänge bleiben als Verweis im Posteingang (Abruf bei Klick). |
| E8 | **Weiterleitungen (System-Admin)** | System-Admins legen Regeln an, die Mails an bestimmte Adressen der Empfangsdomain (z. B. `info@get.einsatzcockpit.com`) an andere Adressen weiterleiten. Regeln haben Vorrang vor der Org-Zuordnung (Org-Adressen enthalten immer einen Punkt `<typ>.<slug>`, reservierte Namen wie `info`, `support`, `postmaster`, `abuse` kollidieren nie). Weiterleitung als **neue Mail** über die Resend-Send-API von einer Adresse der eigenen Versanddomain (SPF/DKIM/DMARC bleiben gültig); `Reply-To` = Originalabsender. Modus je Regel: **inline** (Text/HTML + Anhänge) oder **eml** (Originalmail als `message/rfc822`-Anhang, verlustfrei). Der Inhalt wird nicht dauerhaft gespeichert, nur ein Protokoll (Zeit, Regel, Status, Absenderdomain, gekürzter Betreff). |
| E7 | **GSL** | Zunächst nur reguläre Einsätze; Übernahme in die Großschadenslage als Folgeschritt. |

## 4. Architektur

```
Resend (MX) ──email.received──▶ POST /mail/webhook/resend-inbound   (Svix-Signatur, Rate-Limit, 256 KB)
        │  Metadaten
        ▼
 org_mail_eingang (status neu) ──BackgroundTask──▶ Receiving-API: Inhalt + Anhänge (nur api.resend.com)
        │
        ▼
 mail_zuordnung_service.ordne_zu(eingang)
   ├─ Absender erlaubt? (Freigabe / SPF-DKIM-DMARC)
   ├─ aktiver Einsatz eindeutig?  → incident_service.add_message(...) + Anhänge → status "zugeordnet"
   ├─ kein Einsatz               → status "wartend" (Rückblickfenster)
   └─ unklar/unerlaubt           → status "posteingang"
 mail_zuordnung_loop (alle 30 s): wartende Mails ↔ neu angelegte Einsätze (deckt alle 6 create_incident-Aufrufer ab)
```

- **Adress-Service** `app/services/org_mail_adresse.py`: `standard_adresse(org, domain)`, `alias_adressen(org)`,
  `org_aus_adresse(db, adresse)` (genau eine Org, sonst `None`), Normalisierung (Umlaute, Sonderzeichen).
  Wird vom Versand (`_resend_from_addr_for_org`), der Admin-Anzeige und dem Empfang genutzt.
- **Meldungs-Service:** Logik aus der Route `create_message` in `incident_service.add_message(db, incident, title, detail, status, author_name, user_id|None)` extrahieren (Karte, Audit `message.created`, WebSocket `message_created`); Route und Mail-Eingang nutzen sie.
- **Anhänge ohne User:** `store_upload_for_message` um `user=None` und Byte-Quelle erweitern (`store_bytes_for_message`), Art `datei` ergänzen; bestehende Mime-/Größenprüfung und Kontingent (`_reserve`) bleiben.
- **Inhalt → Meldung:** Titel = Betreff (Steuerzeichen entfernt, max. 500; leer → „E-Mail von <Absender>“). Text = Plaintext; nur-HTML wird sicher in Text umgewandelt (kein HTML in `detail`), erste Zeile „Von: <Absender> · <Zeit>“, Länge begrenzt; `author_name` = „E-Mail (<absender>)“. Antwort-/Signatur-Ketten werden nicht gekürzt (nur hartes Limit).

## 5. Datenmodell (Migration 0272, MariaDB-tauglich)

- `org_mail_eingang`: + `incident_id` (null, FK SET NULL), `message_id_ref` (null), `zugeordnet_at`, `zuordnung` (`auto|manuell|wartend|mehrdeutig|abgelehnt`), `zuordnung_grund` (kurz).
- `message_media`: `kind` darf `datei` sein (String(16) genügt; Prüfungen/Serve-Route anpassen).
- `org_settings`: `mail_eingang_modus` (`aus|freigegeben|alle`, Standard `aus`), `mail_eingang_rueckblick_min` (30), `mail_eingang_mehrere` (`posteingang|neuester`), `mail_eingang_absender` (Text, je Zeile Adresse/Domain), `mail_eingang_max_pro_stunde` (Standard 30).
- SystemSettings (kein Schema): `resend_inbound_domain`, `resend_inbound_webhook_secret` (verschlüsselt).

## 6. Sicherheit

- Svix-Signatur und Zeitstempeltoleranz; unbekannte Empfängeradresse → 200 ohne Speicherung (kein Orakel für Adressen).
- **Missbrauchsschutz:** Rate-Limit je Organisation (Mails/Stunde), Größenlimits (Body, Anhänge, Anzahl), Absenderfreigabe, SPF/DKIM/DMARC; Spoofing-Anzeige im Posteingang.
- Mailinhalt ist unvertrauenswürdig: Text-only in Meldungen, HTML nur im sandboxed Iframe (Posteingang), Dateinamen bereinigt, Download-Typen erzwungen, kein Inhalt in Logs/Audit.
- SSRF: Aufrufe nur `https://api.resend.com`; Anhang-Downloads nur von den in der API-Antwort gelieferten HTTPS-Hosts (Whitelist).
- Mandanten: Zuordnung ausschließlich innerhalb der aus der Adresse ermittelten Organisation; Cross-Org-Tests.
- Übung: Mails werden **nicht** in Übungseinsätze gelegt (Option), außer die Übung ist der einzige aktive Einsatz und der Org-Schalter erlaubt es.

## 7. Phasen und PRs (Codex, ein Integrationsstand je Phase)

| Phase | PR | Inhalt | Abhängigkeit |
|---|---|---|---|
| 0 | M0 | Posteingang (laufender Codex-Auftrag) prüfen, committen, mergen | – |
| A | M1 | Adress-Service + Umstellung Absender auf `<typ>.<slug>@…` (mit Alias, Test aller Mailpfade, Admin-Anzeige „Ihre Adresse“, Reply-To) | M0 |
| A | M2 | Systemweiter Webhook + Domain-/Secret-Einstellungen (SystemSettings-UI), Org-Auflösung aus Empfängeradresse, Anleitung MX/Resend | M1 |
| B | M3 | `add_message`-Service extrahieren; Anhänge ohne User; Medienart `datei` (Speichern, Anzeigen, Download) | M0 |
| B | M4 | `mail_zuordnung_service` (Absenderregeln, Einsatz-Auswahl, Meldung + Anhänge), Org-Einstellungen + UI, Posteingang-Status/Aktion „In Einsatz übernehmen“, Absender freigeben | M2, M3 |
| B | M5 | `mail_zuordnung_loop` (Rückblickfenster), Rate-Limit, Retention, Live-Anzeige (WebSocket), Doku, CHANGELOG, Berechtigungsdoku | M4 |

| C | M6 | **Weiterleitungen** für System-Admins (Regeln `Adresse → Ziele`, Modus inline/eml, Schleifen-/Missbrauchsschutz, Protokoll, Wiederholung bei Fehlern, Admin-UI unter System) | M2 |
| D | M7 | **Druckregel „E-Mail eingegangen“** (Trigger `mail_eingang`, Dokumenttyp `mail_eingang`): Mail (Absender, Betreff, Zeit, Text) und druckbare Anhänge (PDF, Bilder) werden gedruckt; nicht druckbare Anhänge nur als Liste auf dem Deckblatt | M4 |

Phasenende: volle Suite + MariaDB-Zyklus, Playwright-Smoke (Mail → Meldung mit Anhang), Cross-Org-/Rollen-/Webhook-Tests, danach Merge.

## 8. Tests (Pflichtszenarien)

1. Adresse: `feuerwehr.wolfurt`, `rotes-kreuz.xyz`, Umlaute/Sonderzeichen, Alias `<slug>@`, Slug-Kollision zwischen Typen, unbekannte Adresse.
2. Mail an die Adresse bei **einem** aktiven Einsatz → Meldung (Titel/Text), Anhänge Bild+PDF als Medien, DOCX/ZIP als Download.
3. Kein aktiver Einsatz → „wartend“; Einsatz wird innerhalb 30 min angelegt → Zuordnung; nach 31 min → bleibt im Posteingang.
4. Zwei aktive Einsätze → „mehrdeutig“; Option „neuester“ wirkt; manuelle Übernahme.
5. Nicht freigegebener Absender / SPF-Fail → Posteingang, nie in den Einsatz; Freigabe per Aktion.
6. Doppelte Zustellung (Svix-Retry) → genau eine Meldung; Abruffehler → Retry; Hintergrundlauf zweimal → keine Doppelmeldung.
7. Cross-Org: Mail an Org A erscheint nie in Org B; Einsatz einer anderen Org wird nie gewählt.
8. Sicherheit: Rate-Limit, Größenlimits, bösartige Dateinamen/Typen (`.html`, `.svg`, `..\`), HTML im Text, Header-Injection im Betreff, Übungseinsatz.
9. Regression: Mail-Versand (SMTP/O365/Resend, Absender/Reply-To), Meldung manuell anlegen, Medien-Upload.

### Weiterleitungen (M6) im Detail

- **Modell** `mail_weiterleitung` (systemweit, nicht mandantengebunden): `adresse` (Lokalteil oder volle Adresse der Empfangsdomain, eindeutig), `ziele` (1-10 gültige Adressen, JSON), `modus` (`inline|eml`), `aktiv`, `beschreibung`, `max_pro_stunde` (Standard 60), Zähler/`letzte_weiterleitung_at`; `mail_weiterleitung_log` (Regel, Zeit, Status `gesendet|fehler|uebersprungen`, Absenderdomain, gekürzter Betreff, Fehlerkürzel, Versuche). Optional Catch-all-Regel `*` (nur mit Bestätigungshinweis, Standard aus).
- **Ablauf:** Webhook → vor der Org-Zuordnung Regelsuche über `received_for`/`to` → Inhalt (und Roh-Mail bei `eml`) über die Receiving-API holen → Versand über die vorhandene Resend-Versandstrecke (`resend_mail_service`) → Protokoll. Fehlgeschlagene Versuche werden bis zu 3-mal mit Wartezeit wiederholt (Hintergrundlauf), danach `fehler` sichtbar im Admin.
- **Schutz:** Schleifenerkennung (Header `X-Einsatzcockpit-Weitergeleitet`, Ziel darf keine eigene Empfangsadresse mit Regel sein, Absender = Ziel wird übersprungen), `Auto-Submitted`/`Precedence: bulk` und Bounce-Absender (`mailer-daemon`, leere Rückpfade) nicht weiterleiten, Rate-Limit je Regel, Größenlimit (Gesamtgröße ≤ 35 MB, sonst nur Hinweis-Mail mit Link in den Posteingang entfällt – Protokoll `uebersprungen`), Header-Injection-Schutz bei Betreff/Namen.
- **UI** (nur `system_admin`): Liste der Regeln mit Status/Zählern, Anlegen/Bearbeiten/Löschen, „Testweiterleitung“ (Testmail an die Ziele), Protokollansicht (letzte 200 Einträge, Aufbewahrung 30 Tage).
- **Tests:** Regelzuordnung (Vorrang, Groß-/Kleinschreibung, reservierte Namen, keine Kollision mit Org-Adressen), inline/eml-Versand (Resend gemockt: Absender, Reply-To, Anhänge), Schleifen- und Bounce-Schutz, Rate-Limit, Wiederholung, Rechte (nur `system_admin`), Cross-Org (Weiterleitung legt keine Org-Zeile an), Datenschutz (kein Inhalt im Protokoll), Validierung der Zieladressen.

## 9. Offene Punkte / Risiken

- E2 ist entschieden (`get.einsatzcockpit.com`, konfigurierbar).
- Resend-Plan/Kontingent für Empfang und Aufbewahrung der Mails beim Anbieter prüfen (Anhänge deshalb beim Eingang übernehmen).
- Echte Tests mit einer realen Domain/MX sind nur außerhalb der Testumgebung möglich.
- Spam und Mail-Bomben: Schutz durch Freigabeliste + Limits; Betrieb sollte die Eingangsstatistik beobachten.


### Druckregel „E-Mail eingegangen“ (M7) im Detail
- Neuer Trigger `mail_eingang` (+ Labels in `gateway.py`: `TRIGGER_LABELS`, `TRIGGER_DOCUMENT_TYPES`, `RULE_DOCUMENT_LABELS`, `DOCUMENT_TYPE_LABELS`), Dispatcher-Zweig in `print_dispatcher._jobs_for_rule`, Admin-UI `druckregeln.html`; Standard inaktiv. Muster: `gsl_einheit_angelegt`/`autoprint_verleih_background`.
- Hook nach Commit, sobald eine Mail angenommen ist (Status zugeordnet oder Posteingang; nicht bei abgelehnt/Spam). `artifact_ref = "<org_mail_eingang_id>"`, Dedup per `idempotency_key` je Mail/Drucker.
- Optionale Regelfilter: nur zugeordnete Mails (Einsatz), nur freigegebene Absender, Anhänge mitdrucken ja/nein, max. Seitenzahl.
- Renderer: Deckblatt A4 (Org-Logo, Absender, Betreff, Zeit, Einsatz, Anhangsliste mit Größe/Typ), Mailtext als sicherer Text (HTML→Text, kein Remote-Content), danach PDF-Anhänge unverändert angehängt und Bilder je eine Seite; andere Typen nur aufgelistet („nicht druckbar – im Cockpit abrufbar“). Anhänge werden zum Druckzeitpunkt geladen (Resend-Download-URL ggf. neu abrufen), kein Inhalt im PrintJob.
- Limits: Anhangsgröße/Seitenanzahl, Fehler bei einzelnem Anhang bricht den Druck nicht ab (Hinweis auf Deckblatt). Übungseinsatz: kein Autodruck.
- Tests: Regel inaktiv → kein Job; aktiv → genau ein Job (idempotent); Cross-Org; nicht druckbarer Anhang nur gelistet; Rendering ohne Remote-Zugriff.
