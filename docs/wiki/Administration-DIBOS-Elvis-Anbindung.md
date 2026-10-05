# DIBOS-/Elvis-Anbindung (EventHub)

← [Zurück zur Startseite](Home)

Einsatzcockpit kann sich an den **DIBOS EventHub** der Landeswarnzentrale Vorarlberg anbinden — die Schnittstelle, die auch der Desktop-Client **Elvis** verwendet. Ein Hintergrund-Dienst fragt alle paar Sekunden die aktiven Einsätze der Organisation ab und kann daraus Einsätze **anlegen**, bestehende Einsätze **anreichern**, die Wache **mitführen**, **Zu-/Absagen** der Mannschaft übernehmen und Einsätze **automatisch schließen**.

**Zweck:** Der Einsatz steht im Cockpit, sobald die Leitstelle ihn disponiert hat — inklusive Einsatzort, Einsatzcode, BMA-Nummer, Meldungsprotokoll und Rückmeldungen — ohne manuelles Übertragen. Gedacht als Ersatz für die [LIS/IPR-Anbindung](Administration-LIS-Anbindung); bis dahin können beide parallel laufen.

> **Rein lesend.** Einsatzcockpit schreibt nie etwas an DIBOS zurück: keine Alarmierung, keine Statusmeldungen, keine Rückmeldungen. Es werden ausschließlich Leseabfragen gestellt.

---

## Inhalt

1. [Überblick und Architektur](#überblick-und-architektur)
2. [Voraussetzungen](#voraussetzungen)
3. [Einrichtung](#einrichtung)
4. [Betriebsmodi (die drei Schalter)](#betriebsmodi-die-drei-schalter)
5. [Was bei jedem Poll passiert](#was-bei-jedem-poll-passiert)
6. [Einsatz anlegen und Zuordnung](#einsatz-anlegen-und-zuordnung)
7. [Anreicherung im Detail](#anreicherung-im-detail)
8. [Wachenstatus](#wachenstatus)
9. [Zu-/Absagen der Mannschaft](#zu-absagen-der-mannschaft)
10. [Automatisches Schließen](#automatisches-schließen)
11. [Benachrichtigungen und Live-Aktualisierung](#benachrichtigungen-und-live-aktualisierung)
12. [Einsatz-Infos-Seite](#einsatz-infos-seite)
13. [Diagnose-Aufzeichnung (nur System-Admin)](#diagnose-aufzeichnung-nur-system-admin)
14. [Dienstüberwachung](#dienstüberwachung)
15. [Datenschutz und Sicherheit](#datenschutz-und-sicherheit)
16. [Fehlerbehebung](#fehlerbehebung)
17. [Technische Referenz](#technische-referenz)

---

## Überblick und Architektur

```
 Landeswarnzentrale                       Einsatzcockpit
┌──────────────────────┐   HTTPS/JSON   ┌────────────────────────────────────────────┐
│ DIBOS EventHub       │◄───────────────│ dibos_poll_loop (Takt DIBOS_POLL_INTERVAL_S)│
│  Main/GetCurrent...  │  SOAP-Umschlag │   pro aktivierter Org, ein Client je Org:   │
│  Main/GetPublicEvents│  + Basic-Auth  │   1) GetCurrentEvents                       │
│  Main/GetCurrentUnits│                │   2) neue Leitstellennummer? → Fast Path:   │
│  Elvis/GetElvis...   │                │      sofort anlegen + alarmieren            │
└──────────────────────┘                │   3) parallel, nur bei Bedarf:              │
                                        │      GetCurrentUnits  (Einsatz aktiv)       │
                                        │      GetPublicEvents  (Einsatz fehlt)       │
                                        │   4) Anreicherung, falls Daten geändert     │
                                        │        │                                    │
                                        │        ▼                                    │
                                        │  dibos_enrich.enrich_and_broadcast          │
                                        │   anlegen → commit → SMS/Teams/Push         │
                                        │   → Meldungen · Wache · Objekt · Zu-/Absagen│
                                        │   → schließen                               │
                                        │        │                                    │
                                        │        ▼                                    │
                                        │  Broadcast (WebSocket) · Hintergrund-Tasks: │
                                        │  Autodruck · Objekt-Einsatzinfo · WordPress │
                                        └────────────────────────────────────────────┘
```

Zwei Betriebsarten mit klar getrennten Aufgaben:

| Betriebsart | Auslöser | Zweck |
|---|---|---|
| **Leichter Poll** (`dibos_loop.py`) | Läuft dauerhaft im Hintergrund, global im festen Takt `DIBOS_POLL_INTERVAL_S` | Produktivbetrieb: Einsätze erkennen, anlegen, anreichern, schließen. **Einzige** Stelle, die in die DB schreibt. Schreibt keine Rohdaten auf Platte |
| **Voll-Aufzeichnung (Trace)** (`dibos_capture.py`) | Automatisch bei einem eigenen Einsatz (`auto_trace_on_event`) oder manuell durch System-Admin | Reine Diagnose: zeichnet alle Rohantworten auf, ändert **nichts** in der DB |

Der leichte Poll läuft auch während eines Traces unverändert weiter. Bis Oktober 2026 pausierte er während eines Traces und überließ dem Trace die Anreicherung. Ein zweiter Einsatz wurde dann nur im langsameren Trace-Takt erkannt (im Mitschnitt ~20 s), und die Anlage wartete auf alle fünf Abfragen des Zyklus. `GetCurrentEvents` wird in dieser Zeit bewusst doppelt abgefragt (Antwort ~6 KB).

Verwandt: Die **BMA-Nummer** aus DIBOS wird für das Objekt-Matching genutzt (siehe [Objektverwaltung](Administration-Objektverwaltung)). Der separate BMA-Webplattform-Import (Kontakte) ist ein eigenes Modul und nicht Teil dieser Seite.

---

## Voraussetzungen

| Anforderung | Details |
|---|---|
| Zugangsdaten der Landeswarnzentrale | **Zwei getrennte Konten** (siehe unten): ein Gateway-Konto und ein Servicekonto der Organisation |
| Rolle `org_admin` oder `admin` | Konfiguration unter **Admin → DIBOS / Elvis-Anbindung** (`/admin/dibos`) |
| Rolle `system_admin` | Nur für die Diagnose-Aufzeichnung (enthält personenbezogene Daten) |
| `FERNET_KEY` gesetzt | Beide Passwörter werden verschlüsselt gespeichert |
| Alembic-Migrationen | `0163` (Konfiguration), `0177` (Anreicherung), `0178` (syBOS-ID, Zu-/Absagen), `0182` (Einsatzanlage), `0199` (Wachenstatus) — `alembic upgrade head` |
| Ausgehende HTTPS-Verbindung | Vom App-Server zur Basis-URL, Standard `https://dibos.lwz-vorarlberg.at/Z_EventHub` |

### Die zwei Konten

Die Schnittstelle authentifiziert auf **zwei unabhängigen Ebenen**:

| Ebene | Wofür | Feld in Einsatzcockpit |
|---|---|---|
| **Gateway-Konto** (HTTP Basic) | Vom Betreiber vergeben, z. B. Benutzer `elvis` | Gateway-Benutzer / Gateway-Passwort |
| **Servicekonto** (WS-Security `UsernameToken` im SOAP-Header) | Konto der Organisation, z. B. `service.<orgslug>.all` | Service-Benutzer / Service-Passwort |

---

## Einrichtung

### Schritt 1 — Verbindung konfigurieren

Unter **Admin → DIBOS / Elvis-Anbindung** (`/admin/dibos`) für die eigene Organisation (System-Admins können oben die Organisation wählen):

| Feld | Standard | Bedeutung |
|---|---|---|
| Aktiviert | aus | Schaltet die Anbindung für diese Org ein/aus |
| Basis-URL | `https://dibos.lwz-vorarlberg.at/Z_EventHub` | Endpunkt des EventHub; abschließender `/` wird entfernt |
| Host | `einsatzcockpit` | Kennung, die als Parameter bei `GetElvisNotification` mitgesendet wird |
| Agentur (`ag`) | `FW` | Agentur-Filter für `GetPublicEvents` (regionale Einsätze) |
| Wache-UNID | leer | Kennung der eigenen Wache für den [Wachenstatus](#wachenstatus). Leer = alle zum Einsatz gemeldeten Wachen werden übernommen |
| Poll-Intervall (Sek.) | 5 (Minimum 5) | **Nur für die Voll-Aufzeichnung** und die [Dienstüberwachung](#dienstüberwachung). Der reguläre Hintergrund-Poll läuft global über `DIBOS_POLL_INTERVAL_S` und bestimmt allein, wie schnell ein Einsatz erkannt wird |
| Auto-Trace bei Einsatz | ein | Startet bei einem eigenen aktiven Einsatz automatisch eine Voll-Aufzeichnung |
| Auto-Trace-Dauer (Min.) | 120 (Minimum 5) | Höchstdauer dieser automatischen Aufzeichnung. Sie endet früher, wenn 10 min lang kein eigener Einsatz mehr aktiv ist |
| Einsätze anreichern | aus | Siehe [Betriebsmodi](#betriebsmodi-die-drei-schalter) |
| Einsätze anlegen | aus | Siehe [Betriebsmodi](#betriebsmodi-die-drei-schalter) |
| Gateway-/Service-Benutzer und -Passwort | — | Passwörter werden Fernet-verschlüsselt gespeichert und nur ersetzt, wenn im Formular neu eingegeben |

Die Anbindung gilt erst als **vollständig konfiguriert**, wenn Basis-URL, Gateway-Benutzer + -Passwort sowie Service-Benutzer + -Passwort gesetzt sind. Ist das nicht der Fall, überspringt der Hintergrund-Dienst die Organisation.

Mit **Verbindung testen** wird `GetCurrentEvents` aufgerufen, ohne etwas zu speichern. Rückmeldung: „Verbindung erfolgreich (N eigene Einsätze aktuell)" oder die Fehlermeldung (`Anmeldung fehlgeschlagen` bei anhaltendem 401, sonst `Verbindung fehlgeschlagen`).

### Schritt 2 — Globalen Hintergrund-Dienst konfigurieren (.env)

```dotenv
DIBOS_TRACE_ENABLED=true     # Globaler Kill-Switch für den gesamten DIBOS-Hintergrund-Poll
DIBOS_POLL_INTERVAL_S=5      # Poll-Intervall in Sekunden, gilt für alle Orgs
```

Der Loop läuft serverweit; einzelne Organisationen werden über den Schalter **Aktiviert** ein-/ausgeschlossen. Ein Fehler bei einer Organisation (z. B. falsches Passwort) blockiert nie den Zyklus für andere Organisationen.

- **Erster Poll sofort** nach dem Serverstart, danach im **festen Takt** (Start-zu-Start): Die Arbeitszeit eines Zyklus wird vom Intervall abgezogen und verlängert das Erkennungsfenster nicht. Dauert ein Zyklus länger als das Intervall, folgt der nächste ohne Pause.
- Da `GetCurrentEvents` ohne Einsatz nur `[]` liefert und `GetPublicEvents` im Leerlauf nicht mehr abgefragt wird, ist ein kurzes Intervall (5 s) für die Leitstelle unkritisch.

### Schritt 3 — Betriebsmodus wählen und testen

Empfohlene Reihenfolge bei der Erstanbindung:

1. Verbindung testen.
2. **Einsätze anreichern** aktivieren, solange die LIS-Anbindung noch Einsätze anlegt — beobachten, ob Einsatzort, BMA-Nummer und Meldungen korrekt ankommen (Seite [Einsatz-Infos](#einsatz-infos-seite)).
3. Erst danach **Einsätze anlegen** aktivieren, sobald die Zuordnung zuverlässig funktioniert und LIS abgeschaltet werden soll.

---

## Betriebsmodi (die drei Schalter)

Der Hintergrund-Poll wird für eine Organisation nur aktiv, wenn **mindestens einer** der drei Schalter gesetzt ist. Sie sind voneinander unabhängig:

| Schalter | Was zusätzlich abgefragt wird | Was Einsatzcockpit tut |
|---|---|---|
| **Auto-Trace bei Einsatz** | — | Sobald `GetCurrentEvents` nicht leer ist, startet eine Voll-Aufzeichnung (Rohdaten auf Platte, Live-Ansicht für System-Admin). Standardmäßig **an** |
| **Einsätze anreichern** | `GetCurrentUnits` (solange ein eigener Einsatz aktiv ist), `GetPublicEvents` (nur zum Schließen, siehe [Automatisches Schließen](#automatisches-schließen)) | Reichert bereits bestehende, **aktive** Einsätze an; pflegt Wachenstatus, Zu-/Absagen, Objekt-Match; schließt beendete Einsätze |
| **Einsätze anlegen** | `GetPublicEvents` (nur zum Schließen) | Legt für ein Event ohne zuordenbaren Einsatz selbst einen neuen an (inkl. Alarmierung/Benachrichtigung) und führt danach dieselbe Anreicherung wie oben durch (ohne Wachenstatus, siehe unten) |

> **Hinweis zu Auto-Trace:** Der Schalter ist standardmäßig an. Eine Voll-Aufzeichnung schreibt Rohdaten mit Anruferdaten auf den Server (7 Tage Aufbewahrung, siehe [Datenschutz](#datenschutz-und-sicherheit)); in 2 h Einsatz kommen rund 190 MB zusammen. Orgs, die nur anreichern/anlegen wollen, sollten **Auto-Trace ausschalten** — der leichte Poll braucht keine Aufzeichnung. Ein automatischer Trace endet 10 min nach dem letzten eigenen Einsatz bzw. nach der eingestellten Dauer. Läuft das Event danach noch, startet der Loop beim nächsten Poll erneut eine Aufzeichnung.

**Wachenstatus:** `GetCurrentUnits` wird nur mit **Einsätze anreichern** abgefragt und ausgewertet. Mit ausschließlich *Einsätze anlegen* wird der Wachenstatus nicht gepflegt.

---

## Was bei jedem Poll passiert

Pro aktivierter, vollständig konfigurierter Organisation (leichter Poll). Der Ablauf ist darauf ausgelegt, dass ein **neuer Einsatz so früh wie möglich angelegt und alarmiert** wird:

1. **`GetCurrentEvents`** — eigene aktive Einsätze. Schlägt das fehl, wird der Fehler für die [Dienstüberwachung](#dienstüberwachung) protokolliert, der Client der Org verworfen (im nächsten Poll neu aufgebaut) und der Zyklus für diese Org beendet.
2. **Fast Path:** Enthält die Antwort eine Leitstellennummer, die im vorherigen Poll nicht dabei war, und ist *anreichern* oder *anlegen* aktiv, läuft die Anreicherung (`enrich_and_broadcast`) **sofort** — nur mit den Daten aus `GetCurrentEvents`, ohne auf weitere Abfragen zu warten. Damit wird der Einsatz angelegt und alarmiert.
3. **Folgeabfragen, parallel** (Fehler werden je Abfrage geloggt, der Zyklus läuft weiter):
   - `GetCurrentUnits` — nur mit *anreichern* **und** wenn mindestens ein eigener Einsatz aktiv ist.
   - `GetPublicEvents?qty=15&ag=<ag>` — nur, wenn ein **aktiver Einsatz** der Org eine Leitstellennummer hat, die **nicht** (mehr) in `GetCurrentEvents` steht. Beim ersten Verschwinden sofort, danach höchstens alle 60 s.
4. **Anreicherung** mit `GetCurrentEvents` + Units + PublicEvents — **übersprungen**, wenn
   - sich gegenüber dem letzten erfolgreichen Durchlauf nichts geändert hat (Fingerabdruck über alle drei Antworten), oder
   - der Fast Path in diesem Zyklus schon lief und keine Folgeabfrage etwas geholt hat.
   
   Schlägt eine Anreicherung fehl (Rollback), wird der Datenstand nicht als verarbeitet gemerkt und im nächsten Poll erneut versucht.
5. Ist `events` nicht leer, *Auto-Trace* aktiv und noch keine Aufzeichnung aktiv: Voll-Aufzeichnung starten.

Die Anreicherung läuft in einem Worker-Thread mit eigener DB-Session. Ein Fehler dort wird geloggt und löst einen Rollback aus, bricht aber nie den Poll ab.

**Größenordnung (Mitschnitt Einsatz f26008764, 04.10.2026, 2 h):** `GetCurrentEvents` ~6 KB mit Einsatz bzw. `[]` ohne, `GetCurrentUnits` ~32 KB, `GetCurrentRadios` ~25 KB, `GetElvisNotification` `[]`, **`GetPublicEvents` ~490 KB** (88 % des gesamten Datenvolumens — 15 landesweite Events inkl. vollständigem Meldungsprotokoll). Nur 40 von 108 `GetCurrentEvents`-Antworten mit Einsatz enthielten überhaupt eine Änderung.

---

## Einsatz anlegen und Zuordnung

Gilt je Event aus `GetCurrentEvents`. Der Schlüssel ist die **Leitstellennummer** (`eventNumber`, z. B. `f26006436`), gespeichert in `Incident.lis_operation_number` — dieselbe Kennung wie bei LIS und der API-Variante `Leitstellennummer` (siehe [REST-API](Entwickler-REST-API)). Dadurch entstehen keine Dubletten, wenn derselbe Einsatz über mehrere Wege eintrifft.

```
Event aus GetCurrentEvents
        │
        ▼
Aktiver Einsatz mit dieser Leitstellennummer?  ──ja──►  anreichern
        │ nein
        ▼
„Einsätze anlegen" aktiv?  ──nein──►  Event wird ignoriert
        │ ja
        ▼
Event ohne eventNumber?  ──ja──►  ignoriert (kein verlässliches Matching möglich)
        │ nein
        ▼
find_matching_incident (gleiche Logik wie LIS)
  1) Einsatz mit dieser Leitstellennummer (auch bereits geschlossen)
  2) sonst: Alarmstichwort + Adresse im Zeitfenster (3 h), nur aktive Einsätze
        │ Treffer → Leitstellennummer ergänzen, anreichern
        │ kein Treffer
        ▼
Neuen Einsatz anlegen
```

### Felder eines neu angelegten Einsatzes

| Einsatz-Feld | Quelle |
|---|---|
| Alarmstichwort (Alarmtyp-Code) | `tycod` über dieselbe Zuordnung wie bei LIS (`map_stichwort`, erkennt auch Präfixe wie `t_t3`); **Fallback `T1`**, wenn leer oder unbekannt |
| Startzeit | `created` (naive Zeitstempel gelten als Org-Lokalzeit und werden nach UTC umgerechnet) |
| Adresse | `locationStreet`, `locationStreetNo`, `locationCity` |
| Koordinaten | `locationLatitude` / `locationLongitude` |
| Meldungstext | `eventComment`, sonst `diagnose` |
| Grund | `tycodDescription` |
| Übung | Schlüsselwortprüfung in `tycodDescription` + `diagnose` (`schulung`, `übung`, `uebung`, `training`, `probe`) — DIBOS hat kein eigenes Übungs-Flag |
| Leitstellennummer | `eventNumber` |

**Race-Schutz:** Legen zwei Wege gleichzeitig denselben Einsatz an (z. B. DIBOS und LIS-Sync oder Alarm-Webhook mit derselben Leitstellennummer), verhindert der Unique-Constraint `uq_incident_org_lis_operation_number` die Doppelanlage; der zweite Weg übernimmt den bereits angelegten Einsatz.

**Bereits beendete Events:** Ist ein Event bei der Anlage schon abgeschlossen (`closed` gesetzt), wird der Einsatz zur Dokumentation angelegt und **sofort geschlossen — ohne Alarmierung**.

---

## Anreicherung im Detail

Für jeden gefundenen **aktiven** Einsatz (Status `active`) werden folgende Daten übernommen. Grundregel: **Bereits vorhandene Werte werden nie überschrieben** (z. B. vom LIS-Sync, vom Alarm-Webhook oder von manuellen Korrekturen). Ausnahme sind die reinen `dibos_*`-Felder, die nur von hier kommen.

| Daten | DIBOS-Feld | Ziel | Regel |
|---|---|---|---|
| Straße, Hausnr., Ort | `locationStreet`, `locationStreetNo`, `locationCity` | Adressfelder des Einsatzes | Nur leere Felder werden ergänzt |
| Koordinaten | `locationLatitude`, `locationLongitude` | `lat`/`lng` | Nur wenn beide noch leer sind |
| Anrufer | `callerList[]` (`callerName`, `callerNumber`) | Melder Name/Telefon | Erster Anrufer **mit Rufnummer**; nur leere Felder |
| Einsatzcode | `tycod` | `dibos_tycod` | Wird bei Änderung aktualisiert |
| Diagnose | `diagnose` | `dibos_diagnose` | Wird bei Änderung aktualisiert |
| BMA-Nummer | `bmaNo` | `dibos_bma_no` | Wird bei Änderung aktualisiert |
| Einsatzkommentar | `eventComment` | `dibos_event_comment` | Wird bei Änderung aktualisiert |
| Meldungsprotokoll | `comments[]` | Meldungs-Karten | Siehe unten |
| Wachenstatus | `GetCurrentUnits` | Wachenstatus | Siehe [Wachenstatus](#wachenstatus) |
| Zu-/Absagen | `personResponseList[]` | Teilnahme | Siehe [Zu-/Absagen](#zu-absagen-der-mannschaft) |

### Meldungsprotokoll

Jeder **nicht interne** Kommentar (`isInternal = false`) wird als Meldungs-Karte in der Spalte **Meldungen** angelegt:

- Titel: `DIBOS: Meldung`, Text: Kommentartext, Autor: `creationPerson` (Fallback `DIBOS`)
- Interne Kommentare (die `###`-/`**`-Systemzeilen mit Einheitenvorschlag, LOI-Suche, Dispose-Meldungen) werden bewusst **nicht** übernommen — sie sind für das Journal zu technisch
- **Deduplizierung** über die Tabelle `lis_synced_object` mit `obj_type = "dibos_comment"` und der Kommentar-`id`; ein Savepoint macht den Import je Kommentar atomar und race-sicher
- Die Karte entsteht nur einmal — nachträgliche Textänderungen in DIBOS werden nicht nachgezogen

### Objekt-Matching über die BMA-Nummer

Liefert DIBOS eine `bmaNo`, wird versucht, den Einsatz mit einem Objekt zu verknüpfen (Stufe 1 des Objekt-Matchings, siehe [Objektverwaltung](Administration-Objektverwaltung)). Das passiert nur, wenn

- die Objektverwaltung für die Organisation aktiv ist **und**
- für den Einsatz noch **kein bestätigter Objekt-Link** existiert (egal von welcher Quelle).

Entsteht ein neuer Link, wird anschließend die Objekt-Einsatzinfo an die Kontakte des Objekts versandt (sofern dort konfiguriert).

### Was bewusst nicht übernommen wird

- **Fahrzeugstatus (S1–S8):** schreibt für LIS-Orgs bereits `lis_sync` aus einer autoritativen Quelle; ein zweiter Schreiber würde widersprüchliche Zeitstempel riskieren. DIBOS wird für Fahrzeuge nur gelesen/aufgezeichnet (`statusTimes` im Live-Snapshot), aber nicht in die Einsatz-Fahrzeuge gespiegelt.
- **Funkgeräte** (`GetCurrentRadios`) und **`GetElvisNotification`**: werden nur in der Diagnose-Aufzeichnung abgerufen, nicht weiterverarbeitet.

---

## Wachenstatus

Mit **Einsätze anreichern** wird aus `GetCurrentUnits` der Status der eigenen Wache je Einsatz geführt (Statusanzeige „Wachenname: Status" im Kopfbereich des Einsatzes). Berücksichtigt werden Einträge mit `unitType = "wache"`, deren `eventNumber` zum Einsatz passt und — falls konfiguriert — deren `unid` der **Wache-UNID** entspricht.

| DIBOS-Status (`currentStatusText`) | Wachenstatus | Einsatz-Zeitstempel (aus `currentStatusTime`) |
|---|---|---|
| `AL` | alarmiert | — |
| `UEB` | übernommen | `taken_over_at` |
| `S2` | einsatzbereit | `ready_again_at` |
| `S4` | ausgefahren | `departed_at` |
| `S5` | am Einsatzort | `on_scene_at` |

- Es werden nur die im echten DIBOS-Katalog **bestätigten** Statuswerte übernommen; unbekannte Werte werden übersprungen (Debug-Log), nicht geraten.
- Ändert sich der Status nicht, passiert nichts (idempotent). Jede Änderung erscheint im Einsatz-Verlauf als `wache.status_set`, mit dem DIBOS-Zeitstempel.
- Anders als bei Fahrzeugen gibt es für die Wache keinen anderen Schreiber (LIS kennt kein Pendant) — daher wird sie hier gepflegt.

---

## Zu-/Absagen der Mannschaft

DIBOS liefert je Einsatz eine `personResponseList` mit den Rückmeldungen der alarmierten Personen. Einsatzcockpit schreibt sie strukturiert in die **Teilnahme** des Einsatzes und speist damit das Zu-/Absage-Widget im Board.

**Status-Zuordnung:**

| DIBOS-Status | Teilnahme-Status |
|---|---|
| `Zugesagt` | zugesagt |
| `Abgesagt` | abgesagt |
| `<N> Min` (z. B. `10 Min`) | zugesagt (die Person kommt, nur später — die genaue Zeit geht dabei verloren) |
| alles andere | **übersprungen** (keine falsche Zu-/Absage durch Raten) |

**Personenzuordnung:** Über `idSybos` wird das Mitglied mit gleicher **syBOS-ID** gesucht (die ID wird über den Mitglieder-Excel-Import hinterlegt, siehe [Stammdaten pflegen](Administration-Stammdaten-pflegen)). Ohne Treffer — z. B. weil die Dienststelle nicht an syBOS angebunden ist — entsteht ein Freitext-Eintrag mit dem Namen aus DIBOS.

**Versionsanker:** `id` ist der stabile Schlüssel je Person und Einsatz (`Teilnahme.dibos_response_id`), `changeDate` die Version. Eine Rückmeldung wird nur übernommen, wenn sie **neuer** ist als der gespeicherte Stand — so überschreibt DIBOS keine neuere Rückmeldung einer anderen Quelle (z. B. [Teams-Bot](Administration-Teams-Alarmierung)). Eine bereits für dasselbe Mitglied angelegte Zeile (z. B. aus Teams) wird aktualisiert statt dupliziert.

Quelle der Rückmeldung wird als `dibos` vermerkt. Nach Änderungen sendet Einsatzcockpit das WebSocket-Event `rsvp:changed`, sodass nur das Widget neu lädt.

---

## Automatisches Schließen

Einsätze, die in DIBOS beendet wurden, werden in Einsatzcockpit automatisch geschlossen. Grundlage ist `GetPublicEvents`: ist dort ein Event mit gesetztem `closed` und passender Leitstellennummer vorhanden, wird der aktive Einsatz geschlossen (wie beim manuellen Abschluss, inkl. Widerruf von QR-/Lagekarte-Tokens).

**Wann `GetPublicEvents` abgefragt wird:** Ein beendetes Event verschwindet aus `GetCurrentEvents`. Im Mitschnitt geschah das im selben Poll-Zyklus, in dem es in `GetPublicEvents` mit `closed` auftauchte. Deshalb wird `GetPublicEvents` nur abgefragt, wenn ein **aktiver Einsatz der Org** eine Leitstellennummer hat, die **nicht** in der aktuellen `GetCurrentEvents`-Antwort steht:

| Situation | Abfrage |
|---|---|
| Kein aktiver Einsatz mit Leitstellennummer, oder alle stehen in `GetCurrentEvents` | keine |
| Eine Nummer ist seit dem letzten Poll verschwunden | sofort |
| Eine Nummer fehlt weiterhin (z. B. LIS-Einsatz ohne DIBOS-Pendant, Event nicht mehr unter den letzten 15) | höchstens alle 60 s |

Anschließend (bei geschlossenem Einsatz):

- ein **WordPress-Bericht** wird als Hintergrund-Task ausgelöst, sofern konfiguriert (siehe [WordPress-Berichte](Administration-WordPress-Berichte)) — er hält den Poll nicht auf
- das WebSocket-Event `incident_closed` wird gesendet

Voraussetzungen und Grenzen:

- Die Prüfung läuft nur mit *Einsätze anreichern* **oder** *Einsätze anlegen*.
- `GetPublicEvents` liefert nur die **zuletzt 15** Einsätze der Agentur **landesweit** (`qty=15`, alle Feuerwehren mit `ag=FW`). Ein Einsatz, der dort nicht mehr auftaucht (z. B. bei vielen Einsätzen gleichzeitig im Land), wird nicht automatisch geschlossen — dann manuell schließen oder die Auto-Schließen-Funktion der Einsätze nutzen.
- Fällt ein Event nur kurz aus `GetCurrentEvents` heraus (Aussetzer), passiert nichts, solange es in `GetPublicEvents` nicht als `closed` steht.

---

## Benachrichtigungen und Live-Aktualisierung

### Neuer Einsatz aus DIBOS

Ein neuer Einsatz wird **committet, sobald seine Stammdaten stehen** (Stichwort, Adresse, Koordinaten, Melder, Einsatzcode, BMA-Nr., Kommentar) — also **vor** Meldungsprotokoll, Wachenstatus, BMA-Objekt-Matching und Zu-/Absagen. Dann geschieht:

1. **Board-Broadcast** `incident_created` an die Organisation (Alarm-Hinweis im Browser; Übungseinsätze je nach Exercise-Guard ohne Alarm, Titel „Neuer Einsatz aus DIBOS: …")
2. **Einsatzinfo-Benachrichtigung** (SMS, Teams, Push, …) wie bei jedem anderen Alarmweg

Beide laufen auf dem Haupt-Event-Loop, **während** der Worker-Thread die restliche Anreicherung (Meldungen, Objekt, Zu-/Absagen) erledigt und erneut committet. Danach folgen als **Hintergrund-Tasks**, die den Poll nicht blockieren:

3. **Autodruck** (Druckregeln)
4. **Objekt-Einsatzinfo**, falls über die BMA-Nummer ein neues Objekt verknüpft wurde

Zusammen mit dem [Fast Path](#was-bei-jedem-poll-passiert) liegen zwischen der `GetCurrentEvents`-Antwort und dem Auslösen der Benachrichtigung nur noch das Matching und das Anlegen — keine weitere DIBOS-Abfrage, kein Kommentar-Import, kein Objekt-Matching.

### Aktualisierungen bestehender Einsätze

| WebSocket-Event | Wann | Wirkung im Browser |
|---|---|---|
| `dibos_sync` | Anreicherung hat einen Einsatz verändert | Board gleicht Fragmente still serverseitig ab (kein Voll-Reload) |
| `rsvp:changed` | Neue/geänderte Zu-/Absagen | Nur das Zu-/Absage-Widget lädt neu |
| `incident_closed` | Einsatz automatisch geschlossen | Board zeigt den Abschluss |

---

## Einsatz-Infos-Seite

Unter **Admin → DIBOS → Einsätze** (`/admin/dibos/einsaetze`, Rollen `org_admin`/`admin`) steht eine Übersicht, was DIBOS je Einsatz tatsächlich beigetragen hat: DIBOS-Code, Diagnose, BMA-Nr., DIBOS-Kommentar und die Anzahl übernommener Meldungen. Es werden die letzten 100 Einsätze mit mindestens einem DIBOS-Feld gezeigt (Hinweis bei Überschreitung). Die Seite ist der schnellste Weg zu prüfen, ob die Anreicherung funktioniert.

Im Einsatz selbst erscheinen BMA-Nr. und Diagnose auf der Info-Seite des Einsatzes; die Diagnose ist außerdem im Archiv durchsuchbar.

---

## Diagnose-Aufzeichnung (nur System-Admin)

Die Feldnamen der Schnittstelle wurden aus einem echten HTTP-Mitschnitt des Elvis-Clients (v2.3.0.1) abgeleitet, **nicht aus einer offiziellen Dokumentation**. Mit der Aufzeichnung lässt sich prüfen, was tatsächlich über die Leitung kommt.

Unter `/admin/dibos` (Bereich „Diagnose", nur `system_admin`):

- **Aufzeichnung starten** (Dauer wählbar), **abbrechen**, **löschen**
- **Live-Ansicht** (`/admin/dibos/trace/{run_id}/live`): geparster Zwischenstand der Operationen `GetCurrentEvents`, `GetPublicEvents`, `GetCurrentUnits`, `GetCurrentRadios` (`latest.json`)
- Pro Poll-Zyklus werden alle fünf Leseendpunkte abgefragt (`GetCurrentEvents`, `GetPublicEvents`, `GetCurrentUnits`, `GetCurrentRadios`, `GetElvisNotification`); jeder Endpunkt einzeln abgesichert, ein Ausfall stoppt den Zyklus nicht

Ablage: `app_storage/dibos_trace/<org_id>/<run_id>/` (`run_id` = Startzeit in UTC, z. B. `20261004T181233Z`). Pro Austausch eine Request-XML und eine Response-JSON, Dateiname `<seq>_<HHMMSS UTC>_<Operation>_request.xml` bzw. `_response.json`. Antworten über 2 MB werden abgeschnitten und nicht live geparst. Beim Beenden — egal ob Zeit abgelaufen, früh beendet oder abgebrochen — werden die Rohdateien zu `<run_id>.zip` gebündelt; `summary.json` (Austauschliste) und `latest.json` bleiben lesbar daneben liegen.

- **Passwort maskiert:** Im gespeicherten Request steht `<o:Password>***</o:Password>` statt des Servicekonto-Passworts. Aufzeichnungen von vor Oktober 2026 enthalten es noch im Klartext; vor einer Weitergabe prüfen bzw. das Passwort rotieren.
- **Zeitstempel:** Dateinamen und `run_id` sind UTC, die Zeitfelder in den JSON-Antworten (`created`, `dispatched`, …) dagegen Ortszeit ohne Zonenangabe.
- **Früh beendet:** Hat die Aufzeichnung einen eigenen Einsatz gesehen und liefert `GetCurrentEvents` danach 10 min lang nichts mehr, endet sie vorzeitig. Eine manuell gestartete Aufzeichnung ohne Einsatz läuft die volle Dauer.
- Aufzeichnungen werden **7 Tage** aufbewahrt und danach automatisch gelöscht (täglicher Lauf um 04:05 Uhr Wiener Zeit, zusätzlich beim Start einer neuen Aufzeichnung). Eine laufende Aufzeichnung wird nie automatisch gelöscht
- Es gibt bewusst **keine Download-Route über HTTP** — die ZIP-Dateien liegen nur lokal auf dem Server
- Pro Organisation läuft höchstens eine Aufzeichnung gleichzeitig
- Die Aufzeichnung ist **rein lesend** und legt nichts an, reichert nichts an, schließt nichts — das macht ausschließlich der parallel weiterlaufende leichte Poll. Sie hat einen eigenen Client und eigenen Takt (*Poll-Intervall* der Org)

---

## Dienstüberwachung

DIBOS ist Teil der [Systemstatus-Überwachung](Administration-Dienstueberwachung) („Alarm DIBOS"). Die Prüfung ist nur **relevant**, wenn die Org aktiviert, vollständig konfiguriert ist und mindestens einen der drei Schalter gesetzt hat; sonst steht „DIBOS-Poll ist nicht eingerichtet".

| Zustand | Bedingung |
|---|---|
| ok | Letzter `GetCurrentEvents`-Poll erfolgreich und nicht älter als `max(180 s, 6 × Poll-Intervall der Org)` |
| down | Letzter Poll fehlgeschlagen (mit Fehlertext) oder länger als diese Frist keiner |
| unbekannt | Noch nie geprüft |

Die Proben werden sowohl vom leichten Poll als auch von der Voll-Aufzeichnung geschrieben.

---

## Datenschutz und Sicherheit

- **Rein lesend:** Einsatzcockpit sendet keinerlei Alarmierung, Status oder Rückmeldung an DIBOS.
- **Zugangsdaten:** Beide Passwörter werden mit `FERNET_KEY` verschlüsselt (`*_password_enc`). Das Formular ersetzt ein Passwort nur, wenn es neu eingegeben wurde.
- **Audit-Log** (siehe [Audit-Log und Zeitreise](Administration-Audit-Log-und-Zeitreise)): `dibos.config.updated`, `dibos.config.gateway_credentials_rotated`, `dibos.config.service_credentials_rotated`, `dibos.config.test`.
- **Passwort in Aufzeichnungen:** Das WS-Security-Passwort wird vor dem Schreiben der Request-Dateien maskiert. Ältere Aufzeichnungen (vor Oktober 2026) enthalten es im Klartext.
- **Zugangsdaten im Speicher:** Der leichte Poll hält pro Org einen Client offen (Session-Cookie). Er erkennt Änderungen an den Zugangsdaten über einen Hash-Fingerabdruck und baut den Client dann neu auf; Klartext-Passwörter liegen nur im Client selbst.
- **Personenbezug:** Rohdaten enthalten Anrufer (Name/Telefon) und Personenrückmeldungen. Aufzeichnungen sind deshalb auf `system_admin` beschränkt, ohne HTTP-Download und nach 7 Tagen automatisch gelöscht. Übernommene Anrufer-/Meldedaten liegen im Einsatz und unterliegen dessen Aufbewahrung.
- **Mandantentrennung:** Konfiguration ist je Org (1:1); der Loop läuft ohne Tenant-Filter und scoped deshalb jede Abfrage explizit über die Org-ID / Leitstellennummer. Org-Admins sehen und ändern nur die eigene Konfiguration (Prüfung `same_org_or_system_admin`).

---

## Fehlerbehebung

| Symptom | Mögliche Ursache |
|---|---|
| „Anmeldung fehlgeschlagen" beim Test | Anhaltender 401 trotz Session-Cookie: Gateway-Konto **oder** Servicekonto falsch. Beide Ebenen prüfen |
| „Verbindung fehlgeschlagen" | Transportfehler (Firewall, DNS, Basis-URL), HTTP ≥ 400 oder keine gültige JSON-Antwort |
| Test ok, aber nichts passiert | Org nicht **Aktiviert**, global `DIBOS_TRACE_ENABLED=false`, oder keiner der drei Schalter gesetzt; Konfiguration unvollständig; Serverlog (`einsatzleiter.dibos.*`) prüfen |
| Einsatz wird nicht angelegt | *Einsätze anlegen* aus; Event hat keine `eventNumber`; Poll-Fehler im Log |
| Doppelter Einsatz | Einsatz wurde über anderen Weg ohne Leitstellennummer angelegt und liegt außerhalb des 3-h-Fensters oder hat abweichende Adresse/Stichwort. Matching über Leitstellennummer greift erst, wenn sie bekannt ist |
| Falsches Stichwort (`T1`) | `tycod` leer oder unbekannt → Fallback `T1`; in der Diagnose-Aufzeichnung den echten Code prüfen |
| Einsatzort/Melder nicht ergänzt | Felder waren schon befüllt — vorhandene Werte werden nie überschrieben |
| Meldungen fehlen | Kommentar ist `isInternal`, ohne Text/`id`, oder wurde schon importiert |
| Wachenstatus bleibt leer | *Einsätze anreichern* aus; Wache-UNID passt nicht; Status unbekannt (nur AL, UEB, S2, S4, S5); Einsatz nicht `active` |
| Zu-/Absagen erscheinen als Freitext | Keine syBOS-ID am Mitglied hinterlegt (Mitglieder-Excel-Import) |
| Zusage „10 Min" wird nicht als Zeit gezeigt | Erwartetes Verhalten — nur „kommt" vs. „kommt nicht" |
| Einsatz schließt nicht automatisch | Einsatz nicht mehr unter den letzten 15 in `GetPublicEvents`; weder *anreichern* noch *anlegen* aktiv; Einsatz hat keine Leitstellennummer (dann wird `GetPublicEvents` für ihn nie abgefragt) |
| Schließen dauert bis zu 60 s | Die Nummer fehlte schon im vorherigen Poll in `GetCurrentEvents`, das Event war aber noch nicht `closed` — danach wird nur noch alle 60 s nachgesehen |
| Wachenstatus erscheint erst kurz nach dem Alarm | Erwartet: Der Fast Path legt an und alarmiert nur mit `GetCurrentEvents`; der Wachenstatus aus `GetCurrentUnits` folgt im zweiten Durchlauf desselben Zyklus |
| Systemstatus „DIBOS down" | Letzter Poll fehlgeschlagen oder zu lange her — Netz/Zugangsdaten prüfen; bei Neustart des Servers kurz „unbekannt" möglich |
| Rohdatenordner wächst | Auto-Trace läuft bei jedem Einsatz (~190 MB je 2 h); ausschalten, wenn nicht benötigt (7-Tage-Löschung greift automatisch) |

---

## Technische Referenz

### Schnittstelle (Reverse-engineered)

Alle Aufrufe sind `POST` mit einem minimalen SOAP-1.1-Umschlag (`Content-Type: text/xml; charset=utf-8`, `Accept: text/plain`); die **Antwort ist JSON**. Der Umschlag trägt im Header ein WS-Security-`UsernameToken` mit dem Servicekonto; zusätzlich wird HTTP Basic mit dem Gateway-Konto gesendet. Namespace des Benutzerkontextes: `https://dibos.lwz-vorarlberg.at/LWZ_EventHub/`.

**Session-Handshake:** Der erste Request ohne Session-Cookie liefert regelmäßig `401` und setzt dabei ein Cookie. Der Client wiederholt deshalb einmal mit demselben Cookie-Jar; nur ein weiteres `401` gilt als Auth-Fehler (`DibosAuthError`). Der Client ist daher **zustandsbehaftet**, anders als `LisClient`:

| Nutzer | Client-Lebensdauer |
|---|---|
| Leichter Poll | **Ein persistenter Client pro Org** über alle Polls (`dibos_loop._org_clients`). Neu aufgebaut bei geänderten Verbindungsdaten (Hash-Fingerabdruck über URL, Host, `ag`, beide Konten), nach einem Fehler bei `GetCurrentEvents` und wenn die Org nicht mehr pollt. Beim Beenden des Loops werden alle Clients geschlossen. Vorher entstand pro Poll ein neuer Client — also bei **jedem** Poll 401 + Wiederholung + neuer TLS-Handshake |
| Voll-Aufzeichnung | Ein eigener Client für die Dauer der Aufzeichnung |
| Verbindungstest | Ein Client je Test |

Clients werden nie parallel geteilt; innerhalb eines Polls laufen `GetCurrentUnits` und `GetPublicEvents` gleichzeitig über denselben Client (httpx-Verbindungspool, gemeinsamer Cookie-Jar).

| Operation | Pfad | Parameter | Liefert | Größe (Mitschnitt) | Leichter Poll fragt ab … | Genutzt für |
|---|---|---|---|---|---|---|
| `GetCurrentEvents` | `Main/GetCurrentEvents` | — | Eigene aktive Einsätze der Org; `[]` ohne Einsatz | ~6 KB je Einsatz | jeden Poll | Erkennen, Anlegen (Fast Path), Anreichern, Verbindungstest, Monitoring |
| `GetPublicEvents` | `Main/GetPublicEvents` | `qty` (15), `ag` | Die 15 neuesten Einsätze der Agentur **landesweit** (alle `lev3`), offen und geschlossen, je Event mit vollem Meldungsprotokoll | **~490 KB** | nur wenn ein aktiver Einsatz in `GetCurrentEvents` fehlt (sofort, dann ≤ 1×/60 s) | Abschluss-Erkennung |
| `GetCurrentUnits` | `Main/GetCurrentUnits` | — | Einheiten/Wachen der **eigenen** Org (`lev3`) mit Status | ~32 KB | mit *anreichern*, solange ein eigener Einsatz aktiv ist | Wachenstatus; Statuszeiten im Live-Snapshot |
| `GetCurrentRadios` | `Main/GetCurrentRadios` | — | Funkgeräte der Org (ISSI, Alias, Gesprächsgruppe, Position) | ~25 KB | nie | nur Diagnose |
| `GetElvisNotification` | `Elvis/GetElvisNotification` | `serviceUser`, `host` | Elvis-Hinweise (im Mitschnitt immer `[]`) | 2 B | nie | nur Diagnose |

Timeout je Request: 20 s. Nur Leseoperationen sind implementiert.

### Beobachtetes Verhalten der Schnittstelle

Aus dem Mitschnitt zu Einsatz f26008764 (04.10.2026, 2 h, 350 Zyklen à 5 Abfragen) — nicht dokumentiert, nur beobachtet:

| Beobachtung | Folge für die Implementierung |
|---|---|
| Ein eigener Einsatz steht in `GetCurrentEvents`, bis er geschlossen wird, und verschwindet im selben Zyklus, in dem er in `GetPublicEvents` mit `closed` erscheint | Verschwinden aus `GetCurrentEvents` ist der Auslöser, `GetPublicEvents` zu prüfen |
| Das Event stand spätestens 5 s nach `dispatched` (17 s nach `created`) in `GetCurrentEvents` — die Aufzeichnung startete erst danach, der genaue erste Zeitpunkt ist unbekannt | Erkennungszeit ≈ Poll-Intervall; der Fast Path vermeidet zusätzliche Wartezeit |
| `rowVersion` ändert sich bei jeder Änderung am Event (auch bei Zu-/Absagen); nur 40 von 108 Antworten mit Einsatz unterschieden sich | Unveränderte Antworten werden per Fingerabdruck übersprungen |
| `personResponseList` springt zeitweise von 3 Einträgen auf 0 und zurück | Eine leere Liste wird **nie** als Löschung interpretiert (nur Upsert) |
| `comments` wächst stark (Einsatzbeginn: 12 → 21 Einträge in 20 s); in `GetPublicEvents` sind Kommentare 80 % der Bytes; `messageType` 6 (intern, ~95 %), 0 und 3 (sichtbar), 5 (intern) | Nur `isInternal = false` wird übernommen |
| Ein geschlossenes Event fiel einmal kurz aus `GetPublicEvents` heraus und erschien im nächsten Abruf wieder | Fehlende Events nie als „geschlossen" werten |
| Zeitfelder (`created`, `dispatched`, `closed`, `statusTime`, …) sind Ortszeit ohne Zonenangabe | Werden als Org-Zeitzone interpretiert und nach UTC umgerechnet |

### Wichtige Event-Felder (`GetCurrentEvents` / `GetPublicEvents`)

| Feld | Bedeutung |
|---|---|
| `eventNumber` | Leitstellennummer (stabiler Schlüssel), z. B. `f26008764` |
| `id`, `eid` | Interne DIBOS-IDs (nicht verwendet) |
| `ag`, `lev3`, `dgroup` | Agentur (`FW`), Dienststelle (z. B. `F_WOLFU`), Gruppe (z. B. `F_BR`) |
| `tycod`, `subTycod`, `tycodDescription` | Einsatzcode, Untercode, Klartext (z. B. `f2` / „kleiner Brandeinsatz") |
| `diagnose`, `eventComment`, `eventCommentInternal` | Diagnose, Freitextkommentar, interner Kommentar (nicht verwendet) |
| `bmaNo` | BMA-Nummer |
| `status`, `statusText`, `statusTime` | Gesamtstatus, beobachtet: `1` AL, `3` S4, `4` S5, `9` S2, `12` CLOSED |
| `created`, `dispatched`, `closed` | Zeitstempel (naive Werte = Org-Lokalzeit, Bruchteilssekunden variabler Länge) |
| `locationCity/-District/-CityPart/-Street/-StreetNo/-ZipCode/-Object`, `locationLongitude/-Latitude` | Einsatzort |
| `destination*`, `patient*` | Transportziel / Patientendaten (bei Feuerwehr-Events leer, nicht verwendet) |
| `callerList[]` | `callerName`, `callerNumber`, `callAccepted`, … |
| `targetList[]` | `target`, `targetType` (beobachtet: `POCSAG`, `EMAIL ALARM`, `ALARMTEXT`, `SMS-GRUPPE`, `EMAIL BERUF`, `REMOTEC_1/2`, `SIRENE_F`, `TEL. MOBIL BERUF`), `targetCount`, `description` |
| `comments[]` | `id`, `comment`, `isInternal`, `messageType`, `creationDate`, `creationPerson` |
| `personResponseList[]` | `id`, `person`, `function`, `status`, `responseTime`, `department`, `departmentSybos`, `idSybos`, `idDibos`, `changeDate` |
| `unitList[]` | Am Einsatz beteiligte Einheiten (gleiche Felder wie `GetCurrentUnits`; nicht verwendet) |
| `documentList[]` | Dokumente (im Mitschnitt leer, nicht verwendet) |
| `rowVersion`, `changeDate` | Versionsstempel des Events |

### Wichtige Unit-Felder (`GetCurrentUnits`)

`unid`, `unidRfl`, `unitType`, `currentStatus`, `currentStatusText`, `currentStatusTime`, `currentStatusTerminal`, `longitude`, `latitude`, `eventNumber`, `station`, `ag`, `lev3` sowie die Statuszeiten `al`, `s1`–`s8`, `ueb`, `eta`.

- Beobachtete `unitType`: `wache`, `tlf`, `kdof`, `lf-b`, `rlf`, `mtf`, `tmb`, `vf`, `hfunk`.
- Beobachtete Status (`currentStatus` / `currentStatusText`): `1` AL, `2` UEB, `3` S4, `4` S5, `9` S2, `13` LOGGEDOFF, `42` S0. Für die Wache übernommen werden nur AL, UEB, S2, S4, S5 (siehe [Wachenstatus](#wachenstatus)); S0 und LOGGEDOFF werden übersprungen.

### Zustand des leichten Polls (In-Memory, `dibos_loop.py`)

| Variable | Inhalt | Zweck |
|---|---|---|
| `_org_clients` | `org_id → (Fingerabdruck, DibosClient)` | Persistenter Client je Org |
| `_previous_event_numbers` | Leitstellennummern des letzten Polls | Fast Path (neu) und sofortige `GetPublicEvents`-Abfrage (verschwunden) |
| `_last_public_events_at` | Zeitpunkt des letzten `GetPublicEvents` | 60-s-Drossel (`_PUBLIC_EVENTS_MIN_INTERVAL_S`) |
| `_last_enrich_fingerprint` | SHA-256 über Events + Units + PublicEvents des letzten erfolgreichen Durchlaufs | Unveränderte Daten überspringen |

Der Zustand geht bei einem Neustart verloren. Der erste Poll danach behandelt alle laufenden Einsätze als „neu" — das kostet nur einen zusätzlichen, idempotenten Anreicherungslauf. `enrich_and_broadcast` gibt `True` zurück, wenn der Durchlauf ohne Rollback endete. Nur dann wird der Fingerabdruck gespeichert.

Langsame Folgearbeit (Autodruck, Objekt-Einsatzinfo, WordPress-Bericht) startet `dibos_enrich._start_background` als `asyncio`-Task. Starke Referenzen liegen in `_background_tasks`, Fehler werden im Done-Callback geloggt.

### Datenmodell

| Tabelle / Feld | Zweck |
|---|---|
| `org_dibos_config` | Konfiguration je Org (1:1): `enabled`, `base_url`, `host`, `ag`, `wache_unid`, `poll_interval_seconds`, `auto_trace_on_event`, `auto_trace_duration_minutes`, `enrich_incidents`, `create_incidents`, `gateway_user`, `gateway_password_enc`, `service_user`, `service_password_enc` |
| `incident.lis_operation_number` | Leitstellennummer; eindeutig je Org (`uq_incident_org_lis_operation_number`) |
| `incident.dibos_tycod`, `dibos_diagnose`, `dibos_bma_no`, `dibos_event_comment` | DIBOS-Zusatzfelder |
| `incident_wache_status` | Wachenstatus je Einsatz und `wache_unid` (Status, Rohtext, Zeitpunkt) |
| `lis_synced_object` (`obj_type="dibos_comment"`) | Deduplizierung importierter Kommentare |
| `teilnahme.dibos_response_id`, `rsvp_source="dibos"` | Zu-/Absagen; Unique je `org_id` + `dibos_response_id` |
| `member.sybos_id` | Zuordnung der Rückmeldung zum Mitglied |

### Code-Übersicht

| Datei | Aufgabe |
|---|---|
| `app/services/dibos/dibos_client.py` | HTTP-/SOAP-Client, Parser `parse_events`/`parse_units`/`parse_radios` |
| `app/services/dibos/dibos_loop.py` | Globaler Hintergrund-Poll (fester Takt), Org-Auswahl, persistente Clients, Fast Path, bedarfsgesteuerte Folgeabfragen, Fingerabdruck-Skip, Auto-Trace-Start |
| `app/services/dibos/dibos_enrich.py` | Einsatzanlage (Commit + Alarmierung vor der Folgearbeit), Anreicherung, Wache, Zu-/Absagen, Schließen, Broadcasts, Hintergrund-Tasks |
| `app/services/dibos/dibos_capture.py` | Diagnose-Aufzeichnung (rein lesend), Passwort-Maskierung, Früh-Ende, Live-Snapshot, Aufbewahrung |
| `app/services/dibos/dibos_mapping.py` | Zuordnung der bestätigten Wachenstatus |
| `app/models/dibos.py` | `OrgDibosConfig` |
| `app/routers/ui_dibos.py` | Admin-UI (`/admin/dibos`, `/admin/dibos/einsaetze`, Test, Trace-Routen) |
| `app/services/lis/lis_mapping.py`, `lis_matching.py` | Gemeinsam genutzt: Stichwort-Zuordnung, Einsatz-Matching |

Tests: `tests/test_dibos_client.py`, `test_dibos_loop.py`, `test_dibos_enrich.py`, `test_dibos_create_incidents.py`, `test_dibos_wache_status.py`, `test_dibos_capture.py`, `test_dibos_admin_routes.py`.

### Admin-Routen

| Route | Methode | Rolle | Zweck |
|---|---|---|---|
| `/admin/dibos` | GET | org_admin, admin | Konfigurationsseite |
| `/admin/dibos/save` | POST | org_admin, admin | Speichern (Passwörter nur mit `*_secret_changed=1`) |
| `/admin/dibos/test` | POST | org_admin, admin | Verbindungstest (JSON `ok`, `message`) |
| `/admin/dibos/einsaetze` | GET | org_admin, admin | Einsatz-Infos-Seite |
| `/admin/dibos/trace/status`, `/start`, `/{run_id}/cancel`, `/{run_id}/delete`, `/{run_id}/live` | GET/POST | system_admin | Diagnose-Aufzeichnung |

---

**Verwandt:** [LIS/IPR-Anbindung](Administration-LIS-Anbindung) · [Systemstatus & Überwachung](Administration-Dienstueberwachung) · [Einsatz starten](Anwender-Einsatz-starten) · [Objektverwaltung](Administration-Objektverwaltung) · [Stammdaten pflegen](Administration-Stammdaten-pflegen) · [REST-API](Entwickler-REST-API)
