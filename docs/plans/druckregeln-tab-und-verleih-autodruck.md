# Plan: Druckregeln als eigenes Tab + Entfernung der alten Verleihschein-Autodruck-Oberflaeche

Stand: 2026-08-27, Basis-Commit `a4c846d`. Alle Zeilennummern gegen diesen Stand geprueft.

## Ziel

1. Die Druckregel-Verwaltung verlaesst die ueberlange Gateway-Detailseite und bekommt ein
   eigenes Tab (eigene Seite) unter `/gateway/{gateway_id}/druckregeln`.
2. Die alte, parallel existierende Oberflaeche fuer den automatischen Verleihschein-Druck
   (Checkbox-Panel "Verleihschein automatisch drucken" + `OrgSettings.verleih_autodruck`)
   entfaellt vollstaendig - erst nachdem sichergestellt ist, dass die Druckregel-Variante
   funktional alles abdeckt und Bestandsdaten migriert werden.

---

## Teil A - Druckregeln in eigenes Tab

### A1. Gemeinsames Styling extrahieren

`app/templates/gateway/detail.html` enthaelt in Zeile 4-193 einen `<style>`-Block mit dem
kompletten `.gwx`-Dark-Theme. Die neue Seite braucht dasselbe Theme.

- Neu: `app/templates/gateway/_gwx_style.html` - enthaelt exakt diesen `<style>`-Block
  (unveraendert uebernommen, plus die neuen Tab-Regeln aus A2).
- `detail.html` ersetzt den Block durch `{% include "gateway/_gwx_style.html" %}`.
- Die neue `druckregeln.html` inkludiert denselben Block.
- `liste.html` (eigenes `.gwl`-Theme) bleibt unangetastet.

### A2. Tab-Leiste

- Neu: `app/templates/gateway/_tabs.html` mit Jinja-Makro `gwtabs(gw_id, active)`,
  Semantik analog `app/templates/_tabnav.html` (`role="tablist"` / `role="tab"` /
  `aria-selected`), aber im `.gwx`-Look (eigene Klassen `gwx-tabs` / `gwx-tabs__item` /
  `gwx-tabs__item--active`, CSS in `_gwx_style.html`).
- Tabs:
  | Label | Ziel | `active`-Key |
  |---|---|---|
  | `🖨️ Gateway & Drucker` | `/gateway/{gw_id}` | `gateway` |
  | `⚙️ Druckregeln` | `/gateway/{gw_id}/druckregeln` | `regeln` |
- Platzierung: direkt unter der `gwx-bar`-Toolbar (also vor `<div class="gwx-body">`), auf
  **beiden** Seiten identisch.
- Die Druckhistorie bleibt bewusst auf der Gateway-Seite (nicht Teil des Auftrags).

### A3. Neue Route

In `app/routers/ui_gateway.py`, direkt nach `gateway_detail` (endet Zeile 116):

```python
@router.get("/{gateway_id:int}/druckregeln", response_class=HTMLResponse)
def gateway_druckregeln(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    printers = db.query(Printer).filter(Printer.gateway_id == gw.id).order_by(Printer.name).all()
    rules = (
        db.query(PrintRule).filter(PrintRule.org_id == user.org_id)
        .order_by(PrintRule.sort_order, PrintRule.name).all()
    )
    return templates.TemplateResponse(request, "gateway/druckregeln.html", {
        "user": user, "gw": gw, "printers": printers, "rules": rules,
        "connected": _is_connected(user.org_id),
        "doc_labels": RULE_DOCUMENT_LABELS,
        "objekt_element_labels": OBJEKT_ELEMENT_LABELS,
        "trigger_labels": TRIGGER_LABELS,
    })
```

- Kein Routing-Konflikt mit `/gateway/printers.json` (Int-Converter, vgl.
  `tests/test_gateway_routes.py`).
- `app/templates/admin/_layout.html` Zeile 19 matcht bereits auf `_p.startswith('/gateway')`
  -> Sidebar-Gruppe/Aktiv-Zustand bleibt korrekt, **keine Aenderung noetig**.

### A4. Template-Umzug

Aus `detail.html` **entfernen** und nach `app/templates/gateway/druckregeln.html`
**verschieben** (Inhalt unveraendert, nur eingerueckt/umgehaengt):

| Was | Zeilen in `detail.html` (Stand a4c846d) |
|---|---|
| Flash `rule_saved` (Teil der Zeile `saved or rule_saved`) | 209 |
| Flash `rule_created` | 210 |
| Flash `error=rule_dup` | 213 |
| Flash `error=rule_documents` | 214 |
| Flash `test_ok`, `test_err=printer`, `test_err=incident` | 216-218 |
| Panel `#regeln` (Kommentar + kompletter `gwx-col-12`-Block) | 522-700 |
| Sortable-Drag&Drop-Skript (`{% if rules %}<script>...`) | 759-793 |

- Zeile 209 wird auf `detail.html` zu `{% if q.get('saved') %}`; auf `druckregeln.html`
  entsteht `{% if q.get('rule_saved') %}`.
- Auf `detail.html` bleiben: Statuskacheln, `#kopplung`, `#wut`, `#drucker`, `#historie`,
  Pairing-/Token-/Discover-/Test-Flashes.
- Die Kachel "Regeln aktiv" (Zeile 248-253) bleibt auf der Gateway-Seite, wird aber zum Link
  auf `/gateway/{{ gw.id }}/druckregeln`. `rules` bleibt daher im Detail-Context.
- `druckregeln.html`: `{% extends "admin/_layout.html" %}`, Titel
  `Druckregeln - {{ gw.name }} - {{ brand }}`, gleiche `gwx-bar` wie detail.html
  (Brand-Icon + Gateway-Name + Zurueck-Link `← Uebersicht`), darunter die Tabs, darunter
  `gwx-body` mit Flashes + Regel-Panel.
- Leerzustand auf der neuen Seite ergaenzen: Hinweis, dass ein automatischer
  Verleihschein-Druck ueber eine Regel mit Ausloeser "Verleihschein angelegt" abgebildet wird
  (ersetzt den Erklaertext des entfallenden Panels, s. Teil B).
- **CLAUDE.md-Pflicht:** nur gerade ASCII-Anfuehrungszeichen in Markup/Attributen. Beim
  Verschieben nichts autokorrigieren lassen.

### A5. Redirect-Ziele

`_rule_return()` (`ui_gateway.py:429-434`) zeigt heute auf `/gateway/{gw}`:

```python
def _rule_return(gw: int | None, suffix: str) -> str:
    base = f"/gateway/{gw}/druckregeln" if gw else "/gateway"
    return f"{base}?{suffix}"
```

Damit landen `rules/create`, `rules/{id}/save`, `rules/{id}/test`, `rules/{id}/toggle`,
`rules/{id}/delete` auf dem neuen Tab. Die Anker `#regeln` / `#regel-<id>` existieren dort
weiterhin. `rules/reorder` liefert JSON und ist nicht betroffen.

---

## Teil B - Alte Verleihschein-Autodruck-Oberflaeche entfernen

### B1. Was existiert heute doppelt

**Alt (zu entfernen):**
- Panel "🧾 Verleihschein automatisch drucken", `detail.html:494-520` - eine Checkbox.
- Route `POST /gateway/{gateway_id}/verleih-autodruck`, `ui_gateway.py:120-139`.
- Spalte `OrgSettings.verleih_autodruck`, `app/models/master.py:474-476`
  (Migration `0146_verleih_autodruck.py`).
- Fallback-Zweig in `print_dispatcher.autoprint_verleih_background`, Zeilen 441-452,
  plus Helper `_resolve_autoprint_printer` (Zeilen 387-409, **einziger** Aufrufer).

**Wichtig - tatsaechliche Fallback-Bedingung:** Der Erklaertext im alten Panel
("existiert eine solche Regel, hat sie Vorrang") beschreibt das Verhalten *falsch*. Der Code
prueft `if not jobs` (Zeile 441), also **"die Regelauswertung hat keinen neuen Job erzeugt"**,
nicht "es existiert keine Regel". Der Toggle druckt also weiterhin, wenn eine
`verleih_created`-Regel zwar existiert, aber inaktiv ist, kein `verleih_schein` als Dokument
hat, keinen Zieldrucker hat, per Filter nicht greift oder ihr Job schon per
`idempotency_key` dedupliziert wurde (`create_print_job` Zeile 96-104). Diese Semantik ist
fuer die Migration in B3 massgeblich.

**Neu (bleibt):** Druckregel mit Ausloeser `verleih_created`
(`TRIGGER_VERLEIH_CREATED`), Dokument `verleih_schein` (`DOC_VERLEIH_SCHEIN`), ausgewertet in
`autoprint_verleih_background` Zeile 437-438 -> `on_event` -> `_jobs_for_rule` Zeile 585-589.

### B2. Abdeckungsnachweis (Fallback -> Druckregel)

| Fallback-Verhalten | Regel-Aequivalent | Status |
|---|---|---|
| Dokument `verleih_schein` | Dokument-Checkbox "Verleihschein" (`RULE_DOCUMENT_LABELS`, nur bei Ausloeser `verleih_created` sichtbar) | abgedeckt |
| Zieldrucker = Rolle `standard`, sonst erster aktiver | Explizite Drucker-Auswahl `printer_ids` (mehrere moeglich) | abgedeckt, maechtiger |
| `copies: 1` | Feld "Exemplare" (Default 1) | abgedeckt |
| `duplex` aus `printer.defaults` | Feld "Duplex" (Default `off`) | abgedeckt, **Default weicht ab** -> Migration muss den Druckerwert uebernehmen (B3) |
| Nur bei aktivem Gateway-Modul + gekoppeltem Gateway | `on_event` prueft `gateway_effective_enabled` + gekoppeltes Gateway identisch | abgedeckt |
| Keine Filter | Filter sind optional/leer | abgedeckt |
| kein Aequivalent | Seitenbereich, Farbe, A3, Zeitfenster, Uebungs-/Echt-Filter, aktiv/inaktiv | Zugewinn |

Zwei echte Luecken, die **vor** dem Entfernen zu schliessen sind: B3 (Bestandsdaten) und
B4 (`uebung`-Filter laeuft beim Verleih-Trigger fail-closed).

**Zwei Vorbefunde, die NICHT Teil dieses Auftrags sind** (nur dokumentiert, damit der
Abdeckungsnachweis ehrlich bleibt - nicht implementieren):

1. `PrintRule.fallback_printer_id` ist ein **totes Feld**. Es wird im Editor angeboten
   (`detail.html:630-636`) und gespeichert (`ui_gateway.py:470,514`), aber der Dispatcher
   liest es nirgends - `_jobs_for_rule` verwendet ausschliesslich `rule.printer_ids`
   (`print_dispatcher.py:530-539`). Der alte Toggle kennt ohnehin keinen Fallback-Drucker,
   die Abdeckung leidet also nicht darunter. Feld und Beschriftung bleiben unveraendert.
2. Der Regel-**Testdruck** kann fuer `verleih_created` prinzipbedingt nichts erzeugen:
   `rule_test` laedt immer den letzten `Incident` (`ui_gateway.py:578-598`) und
   `build_test_jobs` baut nur `_incident_context` (`print_dispatcher.py:597-611`) - ohne
   `ausleihe_id` ueberspringt `_jobs_for_rule` den Verleihschein (Zeile 579-584). Der
   Testdruck-Button bleibt, zaehlt aber nicht als Zugewinn gegenueber dem Toggle.

### B3. Migration `0226_verleih_autodruck_zu_druckregel.py`

`down_revision = "0225"`. Muster und Wiederaufsetzbarkeit wie
`alembic/versions/0224_objekt_kontakt_telefon_freigabe.py` (jeder Schritt prueft erst, ob er
noch noetig ist; MySQL committet DDL sofort).

**Portabilitaet:** Der Migrationstest laeuft wie `tests/test_migration_0224_wiederaufsetzbar.py`
gegen SQLite, Produktion ist MariaDB. Daher **keine** MySQL-only-Konstrukte:
- Spaltenexistenz ueber `sa.inspect(op.get_bind()).get_columns("org_settings")`
  (Muster 0224:71-88), nicht `ADD/DROP COLUMN IF EXISTS`.
- Spalte droppen via `op.batch_alter_table("org_settings")` (Muster 0224:131-138,143-155).
- **Kein** `JSON_EXTRACT`: `printer.defaults` als Text selektieren und in Python mit
  `json.loads` auswerten, um die Rolle `standard` zu finden.
- Alle Werte als gebundene Parameter (`sa.text(...).bindparams(...)`), nie per String-Format.

**Ersatzkriterium (aus der Fallback-Semantik in B1 abgeleitet):** Eine bestehende Regel gilt
nur dann als vollwertiger Ersatz des Toggles, wenn sie **wirksam** ist, also
`aktiv = 1` **und** `verleih_schein` in `documents` **und** `printer_ids` nicht leer.
Grund: bei jeder anderen Regel druckt heute weiterhin der Toggle (`if not jobs`), ein
blosses "Regel vorhanden -> ueberspringen" wuerde also stilles Nicht-mehr-Drucken erzeugen.

Upgrade:
1. Spalte `org_settings.verleih_autodruck` per Inspector vorhanden? Wenn nein -> Migration
   lief bereits, sofort beenden (Wiederaufsetzbarkeit).
2. Fuer jede Org mit `verleih_autodruck = 1`:
   - Existiert bereits eine **wirksame** `verleih_created`-Regel (Kriterium oben)?
     -> ueberspringen.
   - Sonst Zieldrucker analog `_resolve_autoprint_printer` (`print_dispatcher.py:387-409`)
     aufloesen: erstes `gateway` der Org mit `device_token_hash IS NOT NULL`, dazu dessen
     `printer` mit `aktiv = 1` nach `name` sortiert, bevorzugt der erste, dessen
     `defaults`-JSON `role == "standard"` enthaelt, sonst der erste der Liste.
   - Neue Regel anlegen:
     `name = 'Verleihschein automatisch'`; bei Kollision Suffix ` (2)`, ` (3)`, ...
     (`print_rule` hat `UniqueConstraint("org_id", "name", name="uq_print_rule_org_name")`,
     `app/models/gateway.py:385`),
     `trigger = 'verleih_created'`, `documents = ["verleih_schein"]`,
     `printer_ids = [<id>]` bzw. `[]`, `objekt_elements = []`, `filters = {}`,
     `options = {"copies": 1, "duplex": <printer.defaults.duplex oder "off">}`,
     `sort_order = max(sort_order der Org) + 1`, `erstellt_am`/`aktualisiert_am` = jetzt (UTC),
     `aktiv = 1` wenn ein Drucker aufgeloest wurde, sonst `aktiv = 0`
     (damit nichts still ins Leere druckt, die Absicht aber im neuen Tab sichtbar bleibt).
   - JSON-Spalten als JSON-Text schreiben (`json.dumps`) - `documents`, `printer_ids`,
     `objekt_elements`, `filters`, `options` sind `JSON`-Spalten (`app/models/gateway.py:364-377`).
3. Spalte `org_settings.verleih_autodruck` per `batch_alter_table` droppen.

Downgrade: Spalte per `batch_alter_table` wieder hinzufuegen
(`sa.Boolean, nullable=False, server_default="0"`). Die erzeugten Regeln bleiben stehen; die
alten Toggle-Werte sind verloren. Im Docstring als bewusst verlustbehaftet dokumentieren
(Muster 0224:1-16). Ein erneuter Upgrade nach Downgrade legt dank Ersatzkriterium **keine**
zweite Regel an, sofern die erste wirksam ist.

### B4. `is_exercise` im Verleih-Kontext

`_filter_matches` (`print_dispatcher.py:244-281`) bricht **fail-closed** ab, wenn
`filters.uebung` gesetzt ist und `context["is_exercise"]` fehlt (Zeile 250-254). Der
Verleih-Kontext (Zeile 437) ist heute nur `{"gsl_id", "ausleihe_id"}` - eine Regel mit
"Nur Echteinsaetze"/"Nur Uebungen" wuerde beim Verleih-Trigger also **nie** greifen, obwohl
das Feld im Editor angeboten wird.

Fix in `autoprint_verleih_background`: die zugehoerige Lage laden und `is_exercise` mitgeben.

```python
from app.models.major_incident import MajorIncident
lage = db.get(MajorIncident, a.lage_id) if a.lage_id else None
context = {
    "gsl_id": a.lage_id,
    "ausleihe_id": a.id,
    "is_exercise": getattr(lage, "is_exercise", None),
}
```

(`VerleihAusleihe.lage_id` -> `app/models/verleih.py:144`; `MajorIncident.is_exercise` ->
`app/models/major_incident.py:193`.)

### B5. Regel-Editor: Filter ausloeserabhaengig

Im Filter-Block (`detail.html:664-684`, nach dem Umzug in `druckregeln.html`) ist bereits
`x-data="{ trigger: '...' }"` aktiv. Fuer `trigger === 'verleih_created'` ausblenden, weil
`_filter_matches` sie fuer nicht-einsatzbezogene Trigger ohnehin ignoriert
(`hat_einsatzbezug`, Zeile 248 / 259 / 275) bzw. mangels Kontext wirkungslos sind:

- "Min. Alarmstufe" (Zeile 672) -> `x-show="trigger !== 'verleih_created'"`
- "Nur BMA-Alarme" (Zeile 680-682) -> `x-show="trigger !== 'verleih_created'"`
- "Stichwort enthaelt" (Zeile 674) -> `x-show="trigger !== 'verleih_created'"`
  (Verleih-Kontext hat kein `stichwort`, der Filter waere ein No-Op)

Sichtbar bleiben "Einsatzart" (funktioniert nach B4) und das Zeitfenster (funktioniert
bereits, `now_hhmm` wird in `on_event` Zeile 216-227 immer gesetzt).

### B6. Loeschungen

1. `app/templates/gateway/detail.html`: Panel Zeile 494-520 ersatzlos entfernen.
2. `app/routers/ui_gateway.py`: Route `gateway_verleih_autodruck` (118-139) entfernen;
   in `gateway_detail` (Zeile 100-116) den `OrgSettings`-Import, `os_row` und den
   Context-Key `"verleih_autodruck"` (Zeile 114) entfernen. `get_passthrough_status` und
   `ws_bus` bleiben.
3. `app/models/master.py`: Feld + Kommentar Zeile 474-476 entfernen.
4. `app/services/print_dispatcher.py`: Fallback-Block Zeile 441-452 und Helper
   `_resolve_autoprint_printer` (387-409) entfernen; Docstring von
   `autoprint_verleih_background` (413-416) auf "ausschliesslich regelbasiert" umschreiben;
   nun unbenutzte Importe (`OrgSettings`) entfernen.
5. `app/routers/ui_verleih.py:612`: Kommentar
   "Optionaler Auto-Druck am Stationsdrucker (nur wenn OrgSettings.verleih_autodruck aktiv)."
   auf die neue Semantik umschreiben (regelbasiert, Ausloeser `verleih_created`).
6. Pruefen, dass danach kein Treffer mehr bleibt:
   `grep -rn "verleih_autodruck\|verleih-autodruck\|_resolve_autoprint_printer" app/ tests/`
   (`alembic/versions/0146_*.py` und `0226_*.py` bleiben als Migrationshistorie unveraendert).

---

## Teil C - Doku

- `docs/wiki/Administration-Print-Alarm-Gateway.md`:
  - Abschnitt "Druckregeln (Automatikdruck)" (Zeile 91-105): Hinweis, dass Druckregeln ein
    eigenes Tab sind (`/gateway/<id>/druckregeln`).
  - Ausloeser-Tabelle (Zeile 97) um `verleih_created` ergaenzen.
  - Dokumente-Zeile (Zeile 99) um "GSL-Gesamtbericht", "Objektblatt", "Verleihschein".
  - Filter-Zeile (Zeile 98) um "Einsatzart (Echt/Uebung)", "Zeitfenster", "Nur BMA".
  - Neuer Absatz: Verleihscheine automatisch drucken = Regel mit Ausloeser "Verleihschein
    angelegt" + Dokument "Verleihschein"; Hinweis auf die Migration bestehender
    Toggle-Einstellungen.
- `CHANGELOG.md`: neue Zeile `**2026.08.27.1**` / Datum `2026-08-27` - der oberste Eintrag
  ist bereits `2026.08.27`, und die CalVer-Konvention (Eintrag `2026.08.17`) sieht fuer
  mehrere Releases am selben Tag den Suffix `.1` vor. Inhalt:
  Druckregeln als eigenes Tab, Verleihschein-Autodruck vollstaendig ueber Druckregeln
  (alter Schalter migriert und entfernt), Uebungsfilter jetzt auch fuer Verleihscheine.

---

## Teil D - Tests

Neu bzw. erweitert:

1. `tests/test_gateway_routes.py`: `_match_endpoint("/gateway/7/druckregeln")
   == "gateway_druckregeln"`; bestehende Assertions fuer `/gateway/printers.json` und
   `/gateway/42` muessen weiter gruen sein.
2. Neu `tests/test_gateway_druckregeln_tab.py`:
   - `_rule_return(7, "rule_saved=1#regel-3")` -> `/gateway/7/druckregeln?rule_saved=1#regel-3`;
     `_rule_return(None, "rule_deleted=1#regeln")` -> `/gateway?...`.
   - Rendering-Smoke: `GET /gateway/{id}/druckregeln` als `org_admin` -> 200, enthaelt
     `id="regeln"` und den Tab-Link auf `/gateway/{id}`; `GET /gateway/{id}` enthaelt
     **kein** `id="regeln"` mehr, aber den Tab-Link auf `/druckregeln`.
   - Guard: ohne Gateway-Modul -> 404; als `recorder` -> 403.
3. `tests/test_gateway_pr1.py` ergaenzen:
   - `verleih_created`-Regel mit `filters={"uebung": "nur_uebung"}` greift bei
     `is_exercise=True` und greift **nicht** bei `is_exercise=False`
     (Gegenstueck zu `test_filter_uebung_ohne_kontext_fail_closed`, Zeile 258).
   - `test_verleih_created_ignoriert_min_alarmstufe_und_nur_bma` (Zeile 360) bleibt
     unveraendert gruen.
3b. **Integrationstest fuer `autoprint_verleih_background` selbst** (nicht nur
   `_filter_matches`/`on_event`): echte `MajorIncident`- und `VerleihAusleihe`-Zeilen anlegen,
   Funktion `await`en und pruefen, dass die Regel mit `uebung = "nur_uebung"` auf einer
   Uebungslage einen `verleih_schein`-Job erzeugt und auf einer Echtlage keinen. Nur so ist
   belegt, dass B4 den Kontext wirklich befuellt. `dispatch_job` dabei monkeypatchen
   (Muster `test_serial_ingest_idempotent`, Zeile 486).
4. Neu `tests/test_migration_0226_verleih_autodruck.py` (Muster
   `tests/test_migration_0224_wiederaufsetzbar.py`, SQLite):
   - Org mit `verleih_autodruck=1` + gekoppeltem Gateway + Drucker Rolle `standard`
     (und einem weiteren Drucker ohne Rolle) -> genau eine `print_rule` mit
     `trigger='verleih_created'`, `documents=["verleih_schein"]`,
     `printer_ids=[<standard>]`, `aktiv=1`.
   - Duplex-Uebernahme: `printer.defaults = {"role":"standard","duplex":"long"}`
     -> `options["duplex"] == "long"`.
   - Org mit `verleih_autodruck=1` ohne Drucker/ohne gekoppeltes Gateway
     -> Regel mit `printer_ids=[]`, `aktiv=0`.
   - Org mit mehreren Gateways -> Drucker des ersten gekoppelten Gateways wird gewaehlt.
   - Org mit `verleih_autodruck=1` und bereits **wirksamer** `verleih_created`-Regel
     -> keine zweite Regel.
   - Org mit `verleih_autodruck=1` und vorhandener, aber **unwirksamer** Regel
     (inaktiv / ohne `verleih_schein` / ohne `printer_ids`) -> es wird sehr wohl eine
     Regel angelegt (sonst stiller Verhaltensverlust, siehe B1/B3).
   - Namenskollision: Org hat bereits eine Regel `Verleihschein automatisch`
     -> neue Regel heisst `Verleihschein automatisch (2)`, kein Unique-Fehler.
   - Org mit `verleih_autodruck=0` -> keine Regel.
   - Zweiter Upgrade-Lauf nach abgebrochenem ersten Lauf laeuft fehlerfrei durch.
   - Downgrade -> Spalte existiert wieder (Default 0), Regeln bleiben; anschliessender
     Upgrade legt keine Dubletten an.
5. `OrgSettings` hat kein Attribut `verleih_autodruck` mehr (einfacher `hasattr`-Test),
   und `POST /gateway/{id}/verleih-autodruck` liefert 404/405.

Abschliessend: `pytest -q` vollstaendig gruen.

---

## Offene Punkte / bewusste Entscheidungen

- **Druckhistorie** bleibt auf der Gateway-Seite - nicht Teil des Auftrags.
- **Druckregeln sind org-weit**, die Seite haengt aber am `gateway_id`, weil die
  Drucker-Auswahl je Gateway aufgeloest wird (bestehendes Verhalten, `_rule_return` traegt
  `gw` bereits durch). Bei mehreren Gateways einer Org sieht man auf jedem Tab dieselben
  Regeln, aber die Drucker des jeweiligen Gateways - unveraendert gegenueber heute.
- **Downgrade** von 0226 stellt die Spalte, aber nicht die alten Werte wieder her.
- **Nicht angefasst (Vorbefunde, siehe B2):** `PrintRule.fallback_printer_id` wird vom
  Dispatcher nicht ausgewertet, und der Regel-Testdruck kann keine Verleihscheine erzeugen.
  Beides ist aelter als dieser Umbau und blockiert die Abloesung des Toggles nicht.
