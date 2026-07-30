# BMA-Scraper entfernen, Upload zusammenführen, Kontakt-Merge reparieren

Repo: `C:\Users\micro\Documents\OneDrive\Claude\Einsatzcockpit` (github.com/BattloXX/Einsatzcockpit)

## Context

Die Objektverwaltung enthält einen Live-Webscraper gegen die BMA-Webplattform der
Landeswarnzentrale (DIBOS). Der Zugang kam nie zustande — der Scraper funktioniert nicht
und muss weg. Was bleibt, ist der Datenblatt-PDF-Upload, der dieselben Daten aus einem
manuell hochgeladenen PDF liest.

Daneben gibt es heute **zwei getrennte Upload-Buttons** ohne Objekt-Vorauswahl
(„Brandschutzplan hochladen" und „BMA-Datenblatt hochladen"), die der Nutzer je nach
Dateityp auseinanderhalten muss. Sie sollen zu **einem** Upload werden, der gemischte
Dateien in einem Rutsch annimmt und jede Datei passend verarbeitet.

Drittens ein gemeldeter Fehler: **fehlende Kontakte werden bei einem erneuten Upload nicht
mehr ergänzt**. Ursache verifiziert — Warteliste und Verarbeitungslogik benutzen
unterschiedliche Bedingungen, dazu kommen positionsabhängige Kontakt-IDs und eine
„Ignorieren"-Sackgasse (Details unten).

Ergebnis: ein Upload-Button für alle Objektunterlagen, kein Scraper mehr, und ein
Kontakt-Abgleich, bei dem Anzeige und Verarbeitung dieselbe Wahrheit sehen.

### Bestätigte Entscheidungen
1. Datenblatt-PDFs werden künftig **auch als `ObjektDokument` gespeichert** (heute verworfen).
2. Typerkennung: **erst BMA-Parser** (Seite 1 beginnt mit `BMA <Nr>`), **dann KI** als Fallback → Datenblätter brauchen keine KI.
3. Scraper-Abbau **inkl. Alembic-Migration** (Spalten-Drops, Tabelle `bma_import_lauf`, Legacy-Zeilen).
4. Kontakt-`extern_id` wird auf einen **stabilen Schlüssel** (Anlage + Rolle + Namens-Slug) umgestellt.

### Nicht Teil dieser Änderung
Der Review-Queue-Link `/objekte/bma-import` bleibt als eigener Nav-Eintrag bestehen — zusammengelegt
werden nur die beiden **Upload**-Einstiege. `auto_anlegen` verliert seine Admin-UI (Default: an);
ein Toggle kann bei Bedarf später auf der Queue-Seite nachgerüstet werden.

---

## Phase 1 — Scraper entfernen (Code + Tests in EINEM Commit)

Muss zusammen passieren: `tests/test_bma_import.py` importiert `bma_client`/`bma_parser`/`bma_loop`
auf Modulebene → getrennte Commits lassen den Baum sofort rot werden.

**Dateien löschen:** `app/services/bma_import/bma_client.py`, `bma_loop.py`, `bma_parser.py`,
`app/templates/admin/settings_bma_import.html`, `tests/test_bma_client.py`

Die Kontakt-Konstanten aus `bma_parser.py` (`ROLLEN_MAPPING`, `_KONTAKT_FELD_LABELS`,
`_TELEFON_PRAEFIXE`) sind in `bma_pdf_parser.py:74-97` bereits dupliziert — es geht nichts verloren.

**`app/main.py`** — Loop-Start entfernen (~252-256 Import + `create_task`, ~284-285 `.cancel()`,
~291 aus dem `await`-Tupel). **Zeilen 40 und 671 (`ui_bma_import`-Router) bleiben** — dort lebt
weiterhin die Review-Queue.

**`app/config.py` 344-351** — `BMA_IMPORT_ENABLED`, `BMA_IMPORT_SYNC_HOUR`,
`BMA_IMPORT_SYNC_MINUTE`, `BMA_IMPORT_KEEPALIVE_INTERVAL_S` entfernen; dazu
`tests/conftest.py:20-26` (`os.environ["BMA_IMPORT_ENABLED"]`).

**`app/models/bma_import.py`**
- `OrgBmaImportConfig`: alle Scraper-Spalten + `is_fully_configured` entfernen. Übrig: `id`, `org_id`, `auto_anlegen`, `created_at`, `updated_at`, `org`.
- `BmaImportSatz`: `extern_guid`, `quell_change_date` löschen (nur der Scraper schrieb sie); `kontakte_uebernommen` → siehe Phase 2.
- `BmaImportLauf` + Konstanten `BMA_LAUF_*` komplett löschen.

**Folgestellen:** `app/models/__init__.py` (Zeilen 6, 173), `app/core/tenant.py`
(`"bma_import_lauf"` aus `TENANT_TABLES`; `"bma_import_satz"` bleibt),
`app/templates/admin/_layout.html` (Zeile 21 + Nav 384-387).

**`app/services/bma_import/bma_sync.py`** — löschen: Imports 59-64, `BmaDetailseiteUngueltigError`,
`_PLAUSIBILITAETS_*`, `_DETAIL_MAX_ALTER_TAGE`, `_verarbeite_anlage` (274-389),
`_beende_lauf` (612-622), `sync_org_bma` (625-739). Modul-Docstring auf „Datenblatt-PDF-Upload"
umschreiben.

**`app/routers/ui_bma_import.py`** — löschen: `_get_org_id`, `bma_import_settings_page`,
`bma_import_settings_save`, `bma_import_test_connection`, `bma_import_sync_jetzt`.

**`pyproject.toml`** — `beautifulsoup4` entfernen (einziger Nutzer war `bma_parser.py`).
Gegenprobe vorher: `rg -n "bs4|BeautifulSoup" app/ tests/ scripts/`

**Templates** — `objekt/bma_import.html`: Zeile 13 (`/admin/bma-import`) entfernen (sonst 404),
Überschrift auf „Offene Vorschläge aus hochgeladenen BMA-Datenblättern".
`objekt/_bma.html`: „Aus DIBOS importiert" → „Aus BMA-Datenblatt übernommen".

---

## Phase 2 — Kontakt-Merge: eine gemeinsame Wahrheit

### Die verifizierten Ursachen

| | Ursache | Fundstelle |
|---|---|---|
| A | `ignoriere_vorschlag` setzt `bestaetigt_hash = quell_hash`, lässt `kontakte_uebernommen = False` → `_passender_bestaetigter_stand` springt bei `if not satz.kontakte_uebernommen: return True` raus. Jeder weitere Upload meldet ewig „unveraendert". | `bma_sync.py:163, 571` |
| B | **Hauptursache.** Queue filtert nur `bestaetigt_hash != quell_hash`, die Verarbeitung vergleicht zusätzlich die Live-Kontakte → Upload meldet „als Vorschlag vorgemerkt", die Warteliste bleibt leer. Unbehebbar für den Nutzer. | `ui_bma_import.py:246` vs. `bma_sync.py:149-166` |
| C | Scraper-Satz (`"1234"`) und Upload-Satz (`"pdf:1238"`) zeigen auf dasselbe Objekt; `_sync_kontakte` löscht die Kontakte des jeweils anderen. | `bma_sync.py:143-146, 203-206` |
| D | `extern_id = f"pdf:{zaehler}"` ist ein Laufzähler → kommt eine Person weiter oben dazu, verschieben sich alle IDs, ein Alt-Kontakt wird gelöscht. | `bma_pdf_parser.py:140-147, 173` |
| E | `kontakt_loeschen` setzt nur `bestaetigt_hash` zurück, nicht `kontakte_uebernommen`, und greift nur auf dieser einen Route (nicht beim Arbeitskopie-Merge). Zusätzlich ein Bulk-`.update()` auf einer Tenant-Tabelle (CLAUDE.md-Verstoß). | `ui_objekt.py:1647-1659` |

Der bestehende Test `test_pdf_anlage_nach_manuellem_kontakt_loeschen_wird_wieder_vorschlag`
(`tests/test_bma_import.py:1073`) prüft **nur den Rückgabewert**, nie die Queue-Sichtbarkeit —
deshalb ist B durchgerutscht.

### `kontakte_uebernommen` → `ignoriert_hash`

`kontakte_uebernommen` war ein Ersatz für die eigentlich fehlende Information „der Verwalter hat
ausdrücklich *ignoriert*". Neu in `BmaImportSatz`: `ignoriert_hash: Mapped[str | None]`
(`String(64)`), `kontakte_uebernommen` entfällt. Ein nie übernommener Vorschlag ist damit
korrekterweise ein *offener* Vorschlag.

### Das eine Prädikat — `bma_sync.py`

`_passender_bestaetigter_stand` und `_bma_kontakte_ids_am_objekt` (143-166) ersetzen durch:

- `_kontakt_praefix(satz) -> str` — `f"{satz.extern_id}:"` (Doppelpunkt verhindert, dass `pdf:123` auch `pdf:1238:…` fängt)
- `_importierte_kontakt_ids(satz, objekt) -> set[str]` — **satz-scoped** statt global über `extern_quelle == "dibos_bma"`. Behebt C im Code, nicht nur in den Daten: zwei Datenblätter an einem Objekt verwalten disjunkte Kontaktmengen.
- `kontakt_abweichung(satz, objekt) -> tuple[list[dict], list[str]]` — (fehlende, überzählige); liest die Quelle aus `rohdaten_json`, also genau dem Stand, den `verarbeite_pdf_anlage` zuletzt geschrieben hat.
- **`ist_offener_vorschlag(satz, objekt) -> bool`** — EINZIGE Quelle der Wahrheit, benutzt von `verarbeite_pdf_anlage` **und** `_queue_context`:
  ```
  objekt is None                                   -> False
  objekt.status nicht freigegeben/ueberarbeitung    -> False
  ignoriert_hash == quell_hash                      -> False
  bestaetigt_hash != quell_hash                     -> True
  sonst: bool(fehlende Kontakte)
  ```

Bewusste Asymmetrie: **nur fehlende** Kontakte machen einen Satz wieder offen, überzählige nicht —
sonst erzeugt ein manuell ergänzter Import-Kontakt eine Dauerschleife. Überzählige werden im Diff
angezeigt und beim nächsten Anwenden entfernt.

### Anpassungen

- **`verarbeite_pdf_anlage`** (392-487): `kontakte_uebernommen = True` (481) löschen. Non-Entwurf-Zweig: `db.flush()` **vor** `ist_offener_vorschlag(...)` (sonst liest das Prädikat veraltetes `rohdaten_json`), dann `"vorschlag"`/`"unveraendert"` daraus ableiten. Entwurf-Zweig: nach Auto-Apply `bestaetigt_hash = quell_hash`, `ignoriert_hash = None`.
- **`_sync_kontakte`** (169-211): Signatur `(db, satz, objekt, kontakte, user_id)`; `bestehende` über den Satz-Präfix filtern statt global.
- **`wende_anlage_auf_objekt_an`** (237-271): `satz` als Parameter; `kontakte: list[dict]` (kein `| None` mehr — das war der Scraper-Fall).
- **`uebernehme_vorschlag`** (520-565): `kontakte_uebernommen = True` → `ignoriert_hash = None`. `db.expire(ziel, ["kontakte"])` (558) **unverändert lassen**. **Zusätzlich `db.expire(basis, ["kontakte"])` nach `uebernimm_arbeitskopie`** — sonst sieht der Queue-Rerender im selben Request (`ui_bma_import.py:414`) die frisch gemergten Kontakte nicht und listet den Satz sofort wieder als offen. *Hier kippt das Feature sonst wieder* → Test 1 deckt genau das ab.
- **`ignoriere_vorschlag`** (568-573): `satz.ignoriert_hash = satz.quell_hash` statt `bestaetigt_hash`.
- **`lege_objekt_fuer_satz_an`** (576-597): `kontakte_uebernommen = True` → `ignoriert_hash = None`.
- **`baue_diff`** (490-517): zusätzlich `kontakte_fehlend`, `kontakte_ueberzaehlig` und `grund` (`"quelle_geaendert"` / `"kontakte_fehlen"`) liefern — heute zeigt der Diff nur Stammdaten und eine rohe Kontaktliste, der Nutzer kann den Fehler nicht diagnostizieren.
- **`_queue_context`** (`ui_bma_import.py` 220-262): `if ist_offener_vorschlag(satz, objekt)`. Die Statusprüfung (245) entfällt (steckt im Prädikat). Objekte mit `selectinload(Objekt.kontakte)` in EINER Query laden — das Prädikat fasst jetzt `objekt.kontakte` an (sonst N+1).
- **`_bma_import_content.html`**: „Werden ergänzt (n)" / „Werden entfernt (n)" + Hinweiszeile bei `grund == 'kontakte_fehlen'`. Nur gerade ASCII-Quotes in Attributen (CLAUDE.md).
- **`ui_objekt.py::kontakt_loeschen`** (~1647-1659): den `if kontakt.extern_quelle:`-Block **ersatzlos löschen**. Begründung als Kommentar: `ist_offener_vorschlag()` vergleicht bei jedem Abgleich live; der Merker griff nur für diese eine Route und war ein Bulk-`.update()` auf einer Tenant-Tabelle.

---

## Phase 3 — Stabile Kontakt-IDs (`bma_pdf_parser.py`)

Neu (nach Zeile 97), `import unicodedata` ergänzen:
- `namens_slug(name)` — `"Andreas Böhler"` → `"andreas-boehler"` (Umlaut-Map + NFKD, `[^a-z0-9]+` → `-`, max. 60 Zeichen, Fallback `"unbenannt"`)
- `baue_kontakt_extern_id(anlage_extern_id, art, name)` — `"pdf:1238:bma_alarmperson:andreas-boehler"`. Maximallänge 88 < `objekt_kontakt.extern_id VARCHAR(100)` (`app/models/objekt.py:516`).
- `_mit_stabilen_ids(bloecke, anlage_extern_id)` — vergibt die IDs; **Kollision** (dieselbe Person zweimal in derselben Rolle im selben PDF): zweiter Treffer `#2`, dritter `#3`, in Auftrittsreihenfolge.
- **`ist_bma_datenblatt(data: bytes) -> bool`** — für Phase 4. Liest **nur Seite 1** (ein Brandschutzplan hat bis zu 300 Seiten; `parse_datenblatt_pdf` würde alle extrahieren, nur um an der fehlenden BMA-Nummer zu scheitern). Wirft nie.

Anpassen: `_extrahiere_kontaktbloecke` (133-162) — `zaehler`/`_id` entfernen. `_baue_kontakt` (165-179) —
`extern_id` nicht mehr selbst setzen. `parse_datenblatt_text` (~283) — `_mit_stabilen_ids(...)` aufrufen.

---

## Phase 4 — Ein Upload für alles

| | Wahl | Grund |
|---|---|---|
| Pfad | `GET`/`POST` **`/objekte/dokument-upload`** (bleibt in `ui_objekt.py`) | steht bereits korrekt **vor** `/{objekt_id}` registriert (Kommentar 621-627); `BackgroundTasks`, `_geocode_objekt`, `write_objekt_change` sind dort schon verdrahtet |
| Feld | `dateien: list[UploadFile] = File(...)` | Multi-File aus dem Datenblatt-Pfad |
| Template | `objekt/dokument_upload.html` (umgeschrieben) | `objekt/bma_datenblatt_upload.html` wird gelöscht |
| Antwort | 200 mit Ergebnisliste (kein 303-Redirect) | Mehrdatei-Upload kann nicht auf *ein* Objekt redirecten |
| Altpfad | `GET /objekte/bma-import/datenblatt-upload` → `RedirectResponse(..., 301)`; POST entfällt | Redirect billig, Multipart-Weiterleitung nicht |

**Config entkoppeln:** `_get_or_create_config` von `ui_bma_import.py` nach `bma_sync.py` verschieben als
`hole_oder_erstelle_config(db, org_id)`. Beide Router importieren sie **funktions-lokal** —
`ui_bma_import` importiert `require_objekt_enabled` aus `ui_objekt`, ein Modul-Level-Import wäre zirkulär.

### Ablauf je Datei

```
Vorprüfung (billig, vor jedem KI-Aufruf):
  leer? / > OBJEKT_PDF_MAX_BYTES? / _detect_mime != application/pdf?  -> Fehlerzeile, continue

Typerkennung + Zielauflösung (vor dem Savepoint, da async/KI):
  ist_bma_datenblatt(data)?
    ja  -> parse_datenblatt_pdf(data)        (ValueError -> Fehlerzeile, continue)
    nein-> KI aus?  -> Fehlerzeile "…KI-Klassifikation wird benötigt…", continue
           KI an?   -> await identifiziere_objekt(data, dateiname, org_id)

Schreiben, pro Datei in `with db.begin_nested():`
  Datenblatt: verarbeite_pdf_anlage(...) -> satz; objekt über satz.objekt_id
              objekt is None (auto_anlegen aus, kein Treffer) -> Hinweiszeile, continue
  sonst:      finde_passendes_objekt() / erstelle_objekt_aus_identitaet()
  BEIDE:      await store_dokument_upload(datei, objekt, user, db)
              write_objekt_change + write_audit
              objekt.id / dokument.id in lokale Variablen (nicht ORM über except hinweg)

db.commit()

Background-Tasks ERST nach dem Commit registrieren (eine zurückgerollte Datei
darf keinen Task hinterlassen):
  verarbeite_dokument(dokument_id)                      immer
  analysiere_unklassifizierte_seiten(objekt_id)         nur wenn KI an UND kein Datenblatt
                                                        (Datenblatt ist bereits strukturiert
                                                         geparst -> reiner Token-Verbrauch)
  _geocode_objekt(...)                                  nur bei neu_erstellt
```

**Wichtig:** `await datei.seek(0)` nach `datei.read()` — `store_dokument_upload` liest den Stream erneut.

**Wichtig:** `continue` innerhalb von `with db.begin_nested():` verlässt den Block regulär → der
Savepoint wird **committet**, nicht zurückgerollt. Beim `objekt is None`-Zweig ist das gewünscht
(der `BmaImportSatz` mit `zuordnung='offen'` soll erhalten bleiben, damit die Anlage in der Queue
zur manuellen Zuordnung erscheint). Als Kommentar festhalten, damit es nicht später „aufgeräumt" wird.

**KI-Gate:** Das Formular wird **immer** gerendert, auch ohne KI — nur der Hinweistext wechselt
(heute wird es bei `ki_enabled == False` komplett ausgeblendet). Ohne KI scheitern
Nicht-Datenblätter **einzeln** mit klarer Meldung, kein Seiten-Abbruch.

**Navigation** (`objekt/liste.html` 16-17): aus zwei Upload-Links wird einer —
`🔥 BMA-Vorschläge` (Queue, bleibt) und `📄 Unterlagen hochladen`.
`objekt/bma_import.html` Zeile 12 → `/objekte/dokument-upload`.

---

## Phase 5 — Alembic `0186_bma_scraper_entfernen.py`

`revision = "0186"`, `down_revision = "0185"`. Muster von 0184/0185 übernehmen:
`if conn.dialect.name != "mysql": return` (Tests laufen auf SQLite über
`Base.metadata.create_all`, `tests/conftest.py:13,79`; CI fährt `alembic upgrade head` gegen
MariaDB 10.11, `.github/workflows/ci.yml:52-58`) + `_column_exists`-Guards für Idempotenz.

**Reihenfolge ist zwingend:**

1. **Kontakt-Remap** (in Python, nicht SQL) — braucht die Alt-Sätze noch.
   `namens_slug` als **eingefrorene Kopie** in die Migration duplizieren: eine Migration muss auch
   dann noch exakt dasselbe Ergebnis liefern, wenn der App-Code weiterwandert.
   Je `(org_id, objekt_id)` den Präfix bestimmen:
   - genau eine `pdf:%`-Zeile → `basis = satz.extern_id`
   - keine `pdf:%`, aber Scraper-Zeile mit `bma_nummer` → `basis = 'pdf:' + bma_nummer`. **Das ist der Trick**: genau diesen Präfix erzeugt ein künftiger Datenblatt-Upload derselben Anlage (`bma_pdf_parser.py:261`) → die Kontakte werden gematcht statt dupliziert.
   - mehrdeutig (mehrere `pdf:%` am selben Objekt) oder keine `bma_nummer` → **nicht raten**: `extern_quelle = NULL, extern_id = NULL`. Der Kontakt bleibt erhalten und gilt fortan als händisch gepflegt.

   Dann alle `objekt_kontakt` mit `extern_quelle = 'dibos_bma'` an diesem Objekt (egal ob `pdf:N`
   oder Scraper-Schema `12345:3`) auf `f"{basis}:{art}:{slug(name)}"` umschreiben, Kollisionen
   je Objekt mit `#2`, `#3` (stabil sortiert nach `sort, id`).
   Verwaiste `dibos_bma`-Kontakte ohne `bma_import_satz` ebenfalls auf `NULL/NULL`.
   **Kein `DELETE` auf `objekt_kontakt`** — das sind echte Personen an echten Objekten.

2. **Legacy-Sätze löschen:** `DELETE FROM bma_import_satz WHERE extern_id NOT LIKE 'pdf:%'`.
   Der Scraper frischt sie nie wieder auf; in der Queue blieben sie als unauflösbare Einträge hängen.
   Die Objektzuordnung geht nicht verloren — ein späterer Datenblatt-Upload findet dasselbe Objekt
   über `finde_passendes_objekt()` (BMA-Nummer gegen `objekt_bma.bma_nummer`, die der Scraper schon schrieb).
   Die Kontakte wurden in Schritt 1 gerettet.

3. **`kontakte_uebernommen` → `ignoriert_hash`:** Spalte anlegen, dann
   `UPDATE … SET ignoriert_hash = bestaetigt_hash WHERE kontakte_uebernommen = FALSE AND bestaetigt_hash IS NOT NULL AND bestaetigt_hash = quell_hash`
   (genau die Konstellation „ignoriert, nie übernommen" — 0185 hat echte Übernahmen bereits auf TRUE
   nachgezogen, der Wert ist hier verlässlich), dann alte Spalte droppen.

4. **Spalten-Drops:** `bma_import_satz`: `extern_guid`, `quell_change_date`.
   `org_bma_import_config`: `enabled`, `base_url`, `session_cookie_enc`, `session_gesetzt_am`,
   `keepalive_aktiv`, `sync_stunde`, `sync_minute`, `letzter_lauf_am`, `letzter_lauf_status`,
   `letzter_lauf_meldung`, `import_rechnungsadresse`.

5. `DROP TABLE IF EXISTS bma_import_lauf`

`downgrade()`: Struktur wiederherstellen (Spalten/Tabelle leer, `ignoriert_hash` → `kontakte_uebernommen`),
Daten explizit als nicht wiederherstellbar dokumentieren (Muster: Kommentar in 0185).

**Deploy-Reihenfolge:** `ignoriert_hash` wird **neu gelesen** → `alembic upgrade head` muss **vor**
dem App-Start laufen, sonst „Unknown column" bei jedem SELECT auf `bma_import_satz`. Die gedroppten
Spalten sind unkritisch (alle haben DB-Defaults).

---

## Phase 6 — Tests

**Ersatzlos gestrichen:** `tests/test_bma_client.py` (ganz); `tests/test_bma_import.py`
140-415 (`bma_parser`), 418-587 (`bma_client`), 673-920 (`sync_org_bma`), 1133-1196 (`bma_loop`),
101-107 + 132-137; `tests/test_ui_bma_import.py` 79-200.

**Umschreiben:**
- `tests/test_bma_pdf_parser.py` — alle `pdf:N`-Erwartungen auf `pdf:<nr>:<art>:<slug>`
- `tests/test_ui_bma_import.py` 378-640 — Upload-Tests auf `/objekte/dokument-upload`, Feld `dateien`
- `tests/test_objekt_plan_upload.py` 278-282, 335-353, 355-478 — Multi-File, `files=[("dateien", …)]`, `303` → `200`; **Formular wird jetzt auch ohne KI gerendert** → Assertion invertieren (auf den Warntext prüfen)
- `tests/test_kontakt_loeschen_invalidiert_bma_bestaetigung.py` — Prämisse fällt weg; umbenennen zu `test_bma_kontakt_wiedervorlage.py`, auf `ist_offener_vorschlag()` umstellen

**Neue Regressionstests (Pflicht):**
1. **Queue-Sichtbarkeit == Verarbeitungs-Urteil** (HTTP-Roundtrip): freigegebenes Objekt → Datenblatt hoch → Vorschlag → übernehmen → **Queue ist danach sofort leer** (deckt die `db.expire(basis, …)`-Falle) → einen Kontakt löschen → dasselbe PDF erneut → Antwort meldet „Vorschlag" **und** `GET /objekte/bma-import` zeigt ihn. Dazu als Service-Test: `(ergebnis == "vorschlag") == ist_offener_vorschlag(satz, objekt)`.
2. **Fehlender Kontakt wird beim erneuten Upload wieder angelegt**: Erweiterung von `test_pdf_anlage_nach_manuellem_kontakt_loeschen_wird_wieder_vorschlag` (1073) — zusätzlich `uebernehme_vorschlag()` und danach die Kontaktzahl assertieren (der Alt-Test prüfte nur den Rückgabewert; genau das ließ den Bug durch).
3. **Neuer Kontakt oben im Datenblatt überschreibt die anderen nicht**: `_ECHTES_DATENBLATT` (929) mit vorangestellter Person → 3 Kontakte, Telefon/E-Mail der Bestandskontakte unverändert, `extern_id` stabil.
4. **„Ignorieren" bleibt ignoriert**, bis sich die Quelle ändert (danach wieder Vorschlag + in der Queue).
5. **Zwei Anlagen an einem Objekt** löschen sich nicht gegenseitig die Kontakte.
6. **Gemischter Upload in einem Request** (Plan + Datenblatt) → beide `ok`, zwei `ObjektDokument`, `ObjektBMA.bma_nummer` gesetzt.
7. **KI aus**: Datenblatt geht durch, Nicht-Datenblatt scheitert einzeln, HTTP 200.
8. **Datenblatt-PDF wird gespeichert** (`ObjektDokument`-Count == 1) — bisher verworfen.
9. **Eine kaputte Datei rollt die guten nicht zurück** (Ersatz für Test 534, gemischte Typen).
10. **Aufräumen**: `bma_import_lauf` nicht in `TENANT_TABLES`; `from app.models import BmaImportLauf` schlägt fehl; `/admin/bma-import` → 404; Altpfad → 301.

---

## Phase 7 — Dokumentation

`README.md` 82 (Feature-Zeile auf Datenblatt-Import + gemeinsamen Upload umschreiben), 511
(Migrationstabelle um `0186` ergänzen, `0181` mit „Live-Scraper mit 0186 entfernt" annotieren).
`docs/wiki/Administration-Objektverwaltung.md` 171-256 („Zugangsdaten hinterlegen" 183-198 und
„Grenzen" ersetzen; `BMA_IMPORT_*`-Zeilen 254-256 löschen; „Ignorieren"-Absatz um
„…erscheint erst wieder, wenn sich die Quelle ändert **oder importierte Kontakte am Objekt fehlen**"
ergänzen). `docs/plans/bma-webplattform-lwz-ticket.md` als „Verworfen 2026-07-30" erhalten —
das Ticket dokumentiert, *warum* der Zugang nie kam.

---

## Verifikation

```bash
cd C:/Users/micro/Documents/OneDrive/Claude/Einsatzcockpit

# 1. Keine Scraper-Reste
rg -n "BmaClient|bma_parser|bma_loop|sync_org_bma|BmaImportLauf|kontakte_uebernommen|BMA_IMPORT_" app/ tests/
rg -n "bs4|BeautifulSoup" app/ tests/ scripts/

# 2. Volle Suite (Ausgangsbasis: 1907 grün)
python -m pytest -q

# 3. Gezielt
python -m pytest tests/test_bma_import.py tests/test_bma_pdf_parser.py \
                 tests/test_ui_bma_import.py tests/test_objekt_plan_upload.py \
                 tests/test_objekt_pr3.py -q

# 4. Migration gegen echtes MySQL/MariaDB (SQLite überspringt sie!)
alembic upgrade head && alembic downgrade 0185 && alembic upgrade head
```

**Manuell im laufenden Server** (`/objekte/dokument-upload`):
Brandschutzplan + BMA-Datenblatt gemeinsam auswählen → beide `ok`, beide im Dokumente-Tab,
Datenblatt-Objekt hat BMA-Nummer und Kontakte. Dann einen importierten Kontakt löschen,
dasselbe Datenblatt erneut hochladen → Meldung „Vorschlag" **und** der Eintrag steht unter
`/objekte/bma-import` → übernehmen → Kontakt ist wieder da, Queue leer.

---

## Risiken

| # | Risiko | Abmilderung |
|---|---|---|
| R1 | Remap trifft die falsche Anlage (Objekt mit mehreren `pdf:%`-Sätzen) | Mehrdeutigkeit → nicht raten, `extern_quelle = NULL`. Vorher zählen: `SELECT objekt_id, COUNT(*) FROM bma_import_satz WHERE extern_id LIKE 'pdf:%' GROUP BY objekt_id HAVING COUNT(*) > 1` |
| R2 | **Kein Rollback für `0186`** (Daten weg) | Vor `alembic upgrade` `mysqldump` von `bma_import_satz`, `bma_import_lauf`, `org_bma_import_config`, `objekt_kontakt`. Ohne Dump kein Rückweg |
| R3 | Einmalige Vorschlags-Welle nach Deploy (Sätze mit fehlenden Kontakten erscheinen nun korrekt) | Erwartet und gewollt; im Release-Hinweis ankündigen. Vorher abschätzen: `SELECT COUNT(*) FROM bma_import_satz WHERE bestaetigt_hash = quell_hash` |
| R4 | Slug-Instabilität bei Schreibweisen-Wechsel („Boehler" ↔ „Böhler") | Bewusst: das *ist* eine Datenänderung. Der Diff zeigt „wird ergänzt"/„wird entfernt", der Verwalter entscheidet |
| R5 | Verwaiste PDF-Datei bei Savepoint-Rollback (`store_dokument_upload` schreibt vor dem Commit) | Bestand auch heute im Single-File-Pfad. `store_dokument_upload` ist der letzte Schreibschritt je Datei. Optionale Härtung: Pfad merken, im `except` `unlink(missing_ok=True)` |
| R6 | Fehlklassifikation: Plan, dessen Seite 1 mit „BMA 1234" beginnt | Praktisch unwahrscheinlich (Datenblatt-Zeile 1 ist exakt `BMA <Nr>` allein). Härtung falls nötig: zusätzlich „1. Angaben zur Brandmeldeanlage" auf Seite 1 verlangen |
| R7 | Zirkulärer Import `ui_objekt` ↔ `ui_bma_import` | Alle neuen Zugriffe funktions-lokal; `hole_oder_erstelle_config` liegt im Service (`bma_sync.py`), nicht im Router |

---

## Abschluss

Nach der Umsetzung: aus diesem Plan einen Codex-Task erstellen
(`/codex:rescue` bzw. `codex-companion.mjs task`), damit Codex die Implementierung übernimmt.
