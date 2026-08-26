# Einsatzinfo-Freigabe je Kontakt und je Rufnummer + Kontaktdarstellung überarbeiten

## Kontext

Heute wurde die Einsatzinfo an Objektkontakte ausgeliefert (PR #277, `main` @ `f122bd0`).
Sie steuert den Versand über einen Master-Schalter am **Objekt** plus je Kontakt zwei
Häkchen (Mail/SMS) und ein Freitextfeld „SMS an Nummer". Beim Testen auf
`test.einsatzcockpit.com` sind drei Dinge aufgefallen:

1. Ein Kontakt hat **mehrere Rufnummern** (Festnetz Büro, Mobil …). Die Freigabe gehört an
   die einzelne Nummer, nicht als Freitext an den Kontakt — sonst rät die Software, welche
   Nummer gemeint ist.
2. Der Objekt-Schalter ist ein zweiter Ort, an dem dasselbe entschieden wird. Die
   Entscheidung „diese Person bekommt eine Einsatzinfo" gehört an die Person.
3. Die Kontaktpflege ist am Handy praktisch unbedienbar (siehe Screenshot: das
   Neu-Formular fällt zu fünf fingerbreiten Spalten zusammen, die Häkchen stehen unter dem
   Speichern-Knopf).

Dazu die Auflage: eine Freigabe darf einen BMA-Import überleben — **aber nur, solange die
Rufnummer unverändert bleibt**. Eine geänderte Nummer wurde nie freigegeben.

**Entscheidungen des Nutzers (2026-08-26):** Objekt-Schalter entfällt; Übungs-/
Stichwortfilter und Vorlage bleiben am Objekt. Die E-Mail-Freigabe verfällt nach derselben
Regel, wenn der Import die Adresse ändert. Codex prüft erst den Plan, dann baut er.

---

## Zwei Befunde aus der Recherche, die den Entwurf prägen

**A) Importierte Rufnummern sind keine Rufnummern.**
`bma_pdf_parser._baue_kontakt()` (`app/services/bma_import/bma_pdf_parser.py:207-209`) baut
jeden Eintrag als `f"{label}: {nummer}"` mit den Labels aus `_TELEFON_PRAEFIXE`
(`:93-99`) — also z. B. `"Mobil beruflich: +43 664 88162932"`. Damit ist der heute
ausgelieferte Stand für BMA-Kontakte kaputt:

- `objekt_kontakt_notify.sms_nummer()` liefert `kontakt.telefone[0]` und würde eine SMS an
  `"Mobil beruflich: +43 664 88162932"` schicken.
- Die `tel:`-Links in `_kontakte.html` und `_einsatz_inhalt.html:77` bauen
  `tel:{{ tel|replace(' ', '') }}` → `tel:Mobilberuflich:+43664…`, also ein toter Link.

Deshalb muss Nummer und Label getrennt gespeichert werden — das ist ohnehin die
Voraussetzung für ein Häkchen je Nummer.

**B) `.objekt-kontakt` hat gar kein CSS.**
Die Klasse wird in `_kontakte.html:6` gesetzt, es gibt aber **keine einzige Regel** dazu in
`app/static/css/tailwind.input.css`. Das gesamte Layout hängt an einem inline
`grid-template-columns:1fr 1.2fr 1.2fr 1fr 1fr auto`, das nie umbricht — daher der
Screenshot. `_zusatzadressen.html:36` hat dasselbe Problem und taugt nicht als Vorbild.

Brauchbare vorhandene Bausteine:
- `.form-grid` (`tailwind.input.css:300`): `repeat(auto-fit, minmax(220px, 1fr))` — bricht
  ohne Media Query um. Genau das richtige Primitiv.
- Breakpoint-Konvention im Projekt: `@media (max-width: 760px)`; der Objekt-Block liegt bei
  `tailwind.input.css:1745-1790`.
- `app/static/css/app.css` ist das **kompilierte** Tailwind-Bundle und wird mitversioniert
  → nach jeder Änderung an `tailwind.input.css` muss `npm run build` laufen.
- Wiederholbare Formularzeilen gibt es schon: `link_label`/`link_url` als
  `list[str] = Form(default=[])` (`ui_objekt.py:1376`) mit `links_aus_form()`
  (`objekt_service.py:703`) und der Alpine-`x-for`-Oberfläche in `_gefahren.html:60-75`.
  Dieses Muster wird für die Telefonzeilen 1:1 übernommen.

**Blast Radius von `telefone_json`** (vollständig geprüft):
Schreiber sind nur `ui_objekt.py:1609/1657` und `bma_sync.py:74`. Leser sind die Property
`ObjektKontakt.telefone` (`app/models/objekt.py:544-552`) und darüber
`_kontakte.html`, `_einsatz_inhalt.html:77`, `pdf/objektblatt.html:97` sowie
`_benachrichtigung.html:29`. **Nicht** betroffen: `build_sync_manifest()` (enthält keine
Kontakte), der Alarm-Infoscreen (rendert keine Kontakte) und die Android-App (kein
Treffer auf `telefone_json`/`ObjektKontakt` im Repo `Einsatzcockpit-Android` — die App
zeigt die Server-Seite `/objekte/{id}/einsatz` als Web-View).

---

## 1. Datenmodell: `telefone_json` v2

Spalte bleibt, Inhalt wird strukturiert:

```json
[{"nummer": "+43 664 88162932", "label": "Mobil beruflich", "sms": true}]
```

In `app/models/objekt.py`:

- Neue Property `telefone_eintraege -> list[dict]` als **einzige** Parse-Stelle. Sie liest
  beide Formate:
  - Element ist ein String → `"Label: Nummer"` aufsplitten (nur am **ersten** `": "`, und
    nur wenn der Teil danach wie eine Nummer aussieht), sonst `label=None`.
  - Element ist ein Objekt → `nummer`/`label`/`sms` übernehmen, fehlendes `sms` = `False`.
  - Kaputtes JSON → `[]` (wie heute).
- Property `telefone -> list[str]` bleibt erhalten und liefert weiterhin die Anzeigetexte
  (`"Label: Nummer"` bzw. die nackte Nummer). **Dadurch bleiben PDF, Einsatzansicht und
  alle bestehenden Templates unverändert lauffähig.**
- Neue Property `sms_nummern -> list[str]` → die freigegebenen Nummern in Reinform.

Neue Helfer in `app/services/objekt_service.py`:

- `telefon_normalisiert(nummer) -> str` — Identität einer Rufnummer für Vergleiche:
  Leerzeichen/`-`/`(`/`)`/`/` entfernen, führendes `00` → `+`, führende nationale `0`
  bleibt unangetastet. Muster: `_normalize_phone` in `sms_dispatch_service.py:76`.
  *(Die drei bestehenden Kopien von `_normalize_phone` werden bewusst NICHT
  zusammengeführt — eigenes Thema, nicht Teil dieser Änderung.)*
- `telefone_aus_form(nummern, labels, sms_indizes) -> str | None` — Serialisierer analog
  `links_aus_form()`. Leere Nummern fallen raus; `sms` wird gesetzt, wenn der Zeilenindex
  in `sms_indizes` steht.
- `telefone_zu_json(raw)` bleibt für Bestandsaufrufer erhalten (erzeugt Einträge ohne
  Label und mit `sms=false`).

## 2. Objekt-Schalter entfällt

- `Objekt.kontakt_info_enabled` wird entfernt (Modell, `OBJEKT_KOPIERBARE_FELDER`,
  Migration, `_benachrichtigung.html`, `ui_objekt.py::benachrichtigung_speichern`).
- `kontakt_info_uebung`, `kontakt_info_stichworte`, `kontakt_info_betreff`,
  `kontakt_info_template` bleiben unverändert am Objekt.
- In `objekt_kontakt_notify.dispatch_objekt_einsatzinfo()` entfällt die
  `kontakt_info_enabled`-Bedingung; ein Objekt ohne freigegebenen Kanal fällt automatisch
  raus, weil `sammle_ziele()` leer zurückkommt.
- `_objekt_panel.html:29` und `_ei_objekt_section.html:18` prüfen heute
  `o.kontakt_info_enabled` — Bedingung ersetzen durch „hat das Objekt überhaupt
  freigegebene Empfänger?". Dafür `_panel_context()` um ein
  `hat_empfaenger: dict[objekt_id, bool]` erweitern, statt im Template über alle Kontakte
  zu iterieren.

## 3. Kontakt: E-Mail-Freigabe, Nummern: SMS-Freigabe

- `ObjektKontakt.benachrichtigung_mail` bleibt (E-Mail ist am Kontakt einwertig).
- `ObjektKontakt.benachrichtigung_sms` und `benachrichtigung_telefon` **entfallen** — die
  SMS-Freigabe lebt ab jetzt im `sms`-Flag der jeweiligen Nummer.

## 4. BMA-Import: Freigaben erhalten, aber nur bei unveränderter Kennung

In `app/services/bma_import/bma_sync.py`:

- `_kontakt_felder()` (`:69-76`) darf `telefone_json` **nicht mehr blind** überschreiben.
  Stattdessen eine neue Funktion `_telefone_zusammenfuehren(alt_json, neue_telefone)`:
  1. Alte Einträge über `telefon_normalisiert(nummer)` indizieren.
  2. Für jeden neuen Eintrag Label und Nummer trennen (derselbe Splitter wie in
     `telefone_eintraege`).
  3. `sms` übernehmen, **wenn** die normalisierte Nummer im alten Bestand mit `sms=true`
     stand. Sonst `sms=false`.
  4. Nummern, die der Import nicht mehr liefert, verschwinden mitsamt Freigabe.
- E-Mail analog: in der Zuweisungsschleife (`:200-205`) vor dem `setattr` prüfen — ändert
  sich `email`, wird `benachrichtigung_mail` auf `False` gesetzt (case-insensitiver
  Vergleich, getrimmt). Bleibt die Adresse gleich, bleibt das Häkchen.
- `_adoptionskandidaten()`/`_personen_schluessel()` bleiben unangetastet: die Adoption
  erhält die Zeilen-`id`, damit greifen die Merge-Regeln oben automatisch auch für
  re-gekeyte Zeilen.

## 5. Versanddienst anpassen

In `app/services/objekt_kontakt_notify.py`:

- `sms_nummer(kontakt)` entfällt. `sammle_ziele(objekt)` liefert künftig **je freigegebener
  Nummer** ein SMS-Ziel: `(kontakt, "sms", telefon_normalisiert(eintrag["nummer"]))`,
  dazu wie bisher höchstens ein Mail-Ziel je Kontakt.
- Damit kann ein Kontakt mehrere SMS-Ziele haben. Der Idempotenzschlüssel
  `UNIQUE(incident_id, objekt_kontakt_id, kanal)` in `objekt_kontakt_benachrichtigung`
  reicht dafür **nicht mehr** → Unique-Constraint auf
  `(incident_id, objekt_kontakt_id, kanal, empfaenger)` erweitern (Migration, s. u.).
  `empfaenger` ist bereits `NOT NULL`, trägt also den Schlüssel mit.
  Die Protokoll-Abfrage im Dispatcher wird um `empfaenger == ziel` ergänzt.

## 6. UI: Kontaktdarstellung mobil und Desktop

**`app/templates/objekt/_kontakte.html` — Neuaufbau.**

*Anzeige (Karte je Kontakt statt Tabellenzeile):*
- Kopfzeile: Art-Badge + Name + rechtsbündig Bearbeiten/Löschen.
- Darunter Erreichbarkeit.
- Dann je Nummer eine Zeile: `tel:`-Knopf mit der **normalisierten** Nummer im `href` und
  dem Label als Präfix im sichtbaren Text, plus ein `✉️`/`💬`-Badge, wenn freigegeben.
- E-Mail analog als `mailto:`-Knopf mit Badge.

*Bearbeiten/Neu (ein Formular-Layout für beide):*
- Statt des starren 6-Spalten-Grids die Klasse `form-grid` für Art/Name/E-Mail/
  Erreichbarkeit — bricht per `auto-fit` von selbst um, kein Media Query nötig.
- Telefonzeilen als wiederholbarer Block nach dem Muster `_gefahren.html:60-75`
  (Alpine `x-for`, „+ Nummer"/„Entfernen"): je Zeile `telefon_label`, `telefon_nummer` und
  eine Checkbox `telefon_sms` mit dem **Zeilenindex als `value`** — nicht angehakte
  Checkboxen werden nicht übertragen, deshalb muss der Index im Wert stehen, sonst
  verrutscht die Zuordnung.
- Aktionsknöpfe (`Abbr.`/`Speichern`) kommen in eine **eigene, letzte** Zeile über die
  volle Breite — behebt den im Screenshot sichtbaren Fehler, dass die Häkchen unter
  „Speichern" stehen.
- Auf ≤760px alle Spalten einspaltig, Knöpfe auf volle Breite und mit ≥44px Höhe.

**Neues CSS** in `app/static/css/tailwind.input.css` im Objekt-Block (~Z. 1745-1790):
`.objekt-kontakt` (Karte mit Rahmen/Abstand), `.objekt-kontakt__kopf`,
`.objekt-kontakt__kanaele`, `.objekt-kontakt__telzeile` sowie die passenden Regeln im
vorhandenen `@media (max-width: 760px)`-Block bei Zeile 1785.
Danach **`npm run build`** ausführen und `app/static/css/app.css` mitcommitten.

**Routen** `kontakt_neu` / `kontakt_speichern` (`ui_objekt.py:1582` / `:1621`):
`telefone: str` ersetzen durch `telefon_nummer: list[str] = Form(default=[])`,
`telefon_label: list[str] = Form(default=[])`, `telefon_sms: list[str] = Form(default=[])`;
`benachrichtigung_sms`/`benachrichtigung_telefon` entfallen. Serialisierung über
`telefone_aus_form()`. Der feldgenaue `write_objekt_change`-Diff bleibt wie er ist.

**`_benachrichtigung.html`:** Master-Schalter raus; die Empfängerliste zeigt künftig je
Kontakt die freigegebenen Nummern einzeln. Der DSGVO-Hinweis zu `{meldung}` bleibt.

**`_einsatz_inhalt.html:77`** (Einsatzansicht, wird auch in der Android-App angezeigt):
`tel:`-Link auf die normalisierte Nummer umstellen, Label als Text davor — sonst bleiben
die Wähl-Links für BMA-Kontakte tot (Befund A).

## 7. Migration `0224_objekt_kontakt_telefon_freigabe.py`

`down_revision = "0223"`. Reihenfolge:

1. **Daten zuerst**, solange die alten Spalten noch existieren: alle `objekt_kontakt`-Zeilen
   lesen und `telefone_json` ins neue Format schreiben. Dabei `"Label: Nummer"` splitten
   und — falls `benachrichtigung_sms` gesetzt war — `sms=true` auf der Nummer setzen, die
   zu `benachrichtigung_telefon` passt (normalisiert verglichen), ersatzweise auf der
   ersten Nummer. Reines SQL/`json`, keine ORM-Modelle importieren (Muster:
   `0188_objekt_kontakt_dedupe_unique.py`, das ebenfalls historisch stabil arbeitet).
2. `objekt_kontakt.benachrichtigung_sms` und `benachrichtigung_telefon` droppen.
3. `objekt.kontakt_info_enabled` droppen.
4. Unique-Constraint `uq_objekt_kontakt_benachrichtigung` droppen und mit der Spalte
   `empfaenger` neu anlegen.

`downgrade()` spiegelbildlich (Rückschreiben ins String-Format, erste `sms`-Nummer in
`benachrichtigung_telefon`). SQLite-Einschränkung wie in `0223` beachten
(`ALTER COLUMN … DROP DEFAULT` gibt es dort nicht; `op.drop_constraint` auf SQLite nur
über `batch_alter_table`).

## 8. Tests

`tests/test_objekt_kontakt_benachrichtigung.py` anpassen (die Fälle 3, 8, 15, 16 hängen an
den entfallenden Feldern), dazu neu:

- `telefone_eintraege` liest beide Formate: alte String-Liste, neue Objekt-Liste,
  gemischt, kaputtes JSON → `[]`.
- `"Mobil beruflich: +43 664 88162932"` wird korrekt in Label + Nummer zerlegt;
  `"Musterstraße 3: Klingel"` (kein Nummernanteil) wird **nicht** zerlegt.
- `telefon_normalisiert`: `+43 664 88162932` == `0043-664/881 629 32`, aber ≠ `+43 664 1`.
- `telefone` (Anzeige-Property) liefert für beide Formate dieselben Strings wie vorher —
  sichert PDF/Einsatzansicht ab.
- **BMA-Merge:** Nummer unverändert → `sms` bleibt; Nummer geändert → `sms` weg; Nummer
  entfällt → Eintrag weg; neue Nummer → `sms=false`.
- **E-Mail-Regel:** Adresse unverändert → `benachrichtigung_mail` bleibt; Adresse geändert
  → Häkchen fällt.
- **Versand:** Kontakt mit zwei Nummern, nur eine freigegeben → genau eine SMS an genau
  diese Nummer; zwei freigegebene Nummern → zwei Protokollzeilen, Idempotenz greift je
  Nummer.
- Objekt ohne freigegebenen Kanal → kein Versand (Ersatz für den entfallenen Schalter).
- **Migration:** Alt-Zeile mit `benachrichtigung_sms=1` + `benachrichtigung_telefon` landet
  nach `upgrade()` mit `sms=true` auf der richtigen Nummer.
- Routentest: Anlegen/Speichern eines Kontakts mit drei Nummern, davon Nummer 2 freigegeben
  → korrektes `telefone_json` (prüft die Index-Zuordnung der Checkboxen).

Lauf: `.venv/bin/python -m pytest tests/ -q` (volle Suite, aktuell 2221 passed / 1 skipped).

---

## Ablauf mit Codex

1. **Review:** Codex bekommt diesen Plan plus die Befunde A und B zur kritischen Prüfung —
   Schwerpunkt: Format-Migration und Rückwärtskompatibilität der `telefone`-Property,
   Korrektheit der BMA-Merge-Regeln, Vollständigkeit des Blast Radius, Unique-Constraint-
   Erweiterung.
2. Ich arbeite die Rückmeldung ein.
3. **Umsetzung** durch Codex in der Reihenfolge: Modell/Helfer → Migration → BMA-Merge →
   Versanddienst → UI+CSS → Tests → CHANGELOG.
4. Ich verifiziere Diff und volle Suite selbst, dann Branch, PR, Merge.

## Verifikation

- `.venv/bin/python -m pytest tests/ -q` grün.
- `alembic upgrade head` von `0223` aus; danach `alembic downgrade 0223` und wieder hoch
  (die Datenmigration muss beide Richtungen überstehen).
- `npm run build` läuft, `app/static/css/app.css` ist neu erzeugt und mitcommittet.
- Manuell auf `test.einsatzcockpit.com` am Objekt mit dem Kontakt „Matus": Kontakt
  bearbeiten, zweite Nummer für SMS freigeben, speichern → Badge erscheint nur an dieser
  Nummer; Seite am Handy prüfen (≤760px, einspaltig, Knöpfe voll breit); `tel:`-Link eines
  BMA-Kontakts wählt tatsächlich die Nummer.

---
---

# REVISION 2 — nach Codex-Plan-Review (2026-08-26)

**Diese Revision hat Vorrang vor allem, was oben steht.** Wo sie einem Abschnitt oben
widerspricht, gilt die Revision.

## R0. Zusätzliche Entscheidung des Nutzers

**Manuelle Bearbeitung: das Formular gewinnt.** Die Verfallsregel („Freigabe gilt nur für
genau diese Nummer/Adresse") greift **ausschließlich beim BMA-Import**. Ändert ein
Objektverwalter Nummer oder Adresse von Hand und lässt das Häkchen gesetzt, bleibt die
Freigabe bestehen — er sieht beides im selben Formular, die Eingabe ist bewusst. Die
Änderung wird wie bisher feldgenau über `write_objekt_change` protokolliert.

## R1. Blast Radius war unvollständig

Zusätzlich zu den oben genannten Lesern gibt es diese, die im Plan fehlten:

- **Lageführung:** `app/routers/ui_lagefuehrung.py:436-438` serialisiert `k.telefone` in ein
  Dict für die Karten-Popups, gerendert in `app/static/js/lagefuehrung.js:469`
  (`k.telefone.join(", ")`). Läuft mit der kompatiblen `list[str]`-Property weiter,
  **braucht aber einen Regressionstest** (Bestand: `tests/test_lagefuehrung_pr6.py:109-110`).
- **Arbeitskopie:** `_kopiere_kindzeile` (`objekt_service.py:172-189`, aufgerufen `:233-237`
  und `:330-334`) kopiert `telefone_json` roh mit — SMS-Freigaben werden also dupliziert.
  Das ist fachlich gewollt (die Kopie soll den Stand des Objekts abbilden), muss aber
  getestet werden.
- **Org-Export/-Import:** `org_export_service.py:246-267` und
  `org_import_service.py:174-180` arbeiten generisch auf Spaltenebene. Kein Codeänderung
  nötig, aber: ein **altes v1-Backup** bringt `telefone_json` als String-Liste zurück. Das
  Modell liest das (Abwärtskompatibilität), und der nächste Speichervorgang bzw.
  BMA-Import überführt es nach v2. Ein **neues Backup** lässt sich nicht in eine
  Installation auf Stand 0223 zurückspielen — im Docstring der Migration dokumentieren.

Weitere Bestandstests, die angefasst werden müssen: `tests/test_objekt_pr2.py:57-71`,
`tests/test_bma_kontakt_sync.py:73` und `:169`, `tests/test_objekt_arbeitskopie.py:94-95`,
`tests/test_org_import_service.py:79-96`.

## R2. Legacy-Splitter: strenger als im Ursprungsplan

Der Vorschlag „am letzten `: ` splitten, wenn der Rest wie eine Nummer aussieht" wird
**verworfen** — er würde `"Notruf: 24/7"` oder `"Stock: 2. OG"` zu Kandidaten machen.
Stattdessen:

1. **Nur exakte Treffer** gegen die fünf bekannten BMA-Label werden zerlegt:
   `Telefon beruflich`, `Telefon privat`, `Mobil beruflich`, `Mobil privat`, `Pager`
   (Quelle: `bma_pdf_parser.py:93-99`). Präfix muss exakt `"<Label>: "` sein.
2. **Alles andere bleibt unzerlegt**: `label=None`, `nummer` = der ganze String. Genau so
   wird es heute schon angezeigt — Zerlegen brächte keinen Nutzen, nur Risiko.
3. Genau **eine** Implementierung, die Modell, Migration und BMA-Sync gemeinsam nutzen.
   Da die Migration keine App-Module importieren darf, wird die Funktion dort als
   wörtliche, kommentierte Kopie hinterlegt (Muster: `namens_slug` in
   `0188_objekt_kontakt_dedupe_unique.py:20`).
4. **Zusätzlich:** `bma_pdf_parser._baue_kontakt()` (`:203-215`) liefert künftig
   strukturierte Einträge `{"label": …, "nummer": …}` statt zusammengeklebter Strings.
   Der Legacy-Splitter ist damit nur noch für Bestandsdaten zuständig.

Negativtests: `"Notruf: 24/7"`, `"Stock: 2. OG"`, `"Firma: Zentrale: +43 555 123"`,
`"Telefon: DW 123"` bleiben allesamt unzerlegt.

## R3. BMA-Merge: Einbauposition präzisiert

Der Merge gehört **nicht** in `_kontakt_felder()` — dort ist die alte Kontaktzeile noch
gar nicht bekannt (`bma_sync.py:69-76`, aufgerufen `:169` **vor** der Kontaktauswahl
`:170-184`). Verbindliche Reihenfolge in `_sync_kontakte`:

```python
felder = _kontakt_felder(daten)          # OHNE telefone_json
kontakt = <exakter Treffer | Adoption>   # :170-184
if kontakt is None:
    kontakt = ObjektKontakt(..., telefone_json=<neu, alle sms=False>)   # :187
else:
    neues_json = _telefone_zusammenfuehren(kontakt.telefone_json, daten["telefone"])
    # E-Mail VOR der setattr-Schleife pruefen (:200-205):
    if _mail_key(kontakt.email) != _mail_key(felder["email"]):
        kontakt.benachrichtigung_mail = False
```

Damit greift die Regel auf **allen drei** Pfaden: exakte `extern_id`, Re-Keying und
Adoption einer handgepflegten Zeile (die Adoption behält dasselbe ORM-Objekt, `:172-184`).

Weitere Auflagen:
- Der BMA-Pfad darf **nicht mehr** über `telefone_zu_json(", ".join(...))` laufen
  (`objekt_service.py:42-43`) — der Legacy-Parser würde an Kommas innerhalb einer Nummer
  erneut zerlegen.
- **Normalisierte Duplikate** im alten oder neuen Bestand: alle Einträge mit derselben
  normalisierten Nummer teilen dasselbe `sms`-Flag; eine alte Freigabe darf sich nicht auf
  mehrere sichtbare Einträge vervielfachen.
- `_mail_key` = getrimmt und `casefold()`.
- **Audit:** Wird eine Freigabe durch den Import entzogen (Nummer geändert/entfallen oder
  Adresse geändert), einen `write_objekt_change`-Eintrag im Bereich `kontakte` schreiben
  (`before="freigegeben"`, `after="entzogen"`). Heute protokolliert der Sync nur pauschal
  mit `before=None` (`bma_sync.py:219-221`) — der Verlust einer Einwilligung muss
  nachvollziehbar sein.

Tests: exakter Treffer / Adoption / Re-Keying jeweils × (Nummer unverändert | geändert |
entfallen) und × (E-Mail unverändert | geändert).

## R4. Idempotenz

- Unique-Constraint auf `(incident_id, objekt_kontakt_id, kanal, empfaenger)` — wie geplant.
  **Der Constraint muss auch im ORM nachgezogen werden** (`app/models/objekt.py:725-729`),
  nicht nur in der Migration.
- Die Protokollabfrage im Dispatcher (`objekt_kontakt_notify.py:197-202`) muss `empfaenger`
  aufnehmen.
- **NULL-Lücke:** `objekt_kontakt_id` ist `ON DELETE SET NULL` nullable; mehrere Zeilen mit
  NULL gelten in MySQL wie SQLite als verschieden. Das öffnet **keinen** realen
  Doppelversandpfad — die Lücke entsteht erst nach dem Löschen eines Kontakts, und ein
  gelöschter Kontakt wird vom Dispatcher nicht mehr eingesammelt. Wird im Modell-Docstring
  dokumentiert; **kein** zusätzlicher Snapshot-Schlüssel (unverhältnismäßig).
- **Race:** Zwei parallele Dispatcher können beide an der Vorabfrage vorbeilaufen. Der
  DB-Constraint ist die maßgebliche Absicherung → `IntegrityError` beim Insert abfangen,
  als „bereits versendet" werten, `db.rollback()` auf einen Savepoint und weitermachen.
  Test dafür ergänzen.
- Empfängerform kanonisieren: E-Mail `casefold()`, SMS immer die normalisierte Nummer —
  sonst greift der Schlüssel nicht.

## R5. Formularparser absichern

`telefone_aus_form()` muss:
- `telefon_sms` strikt als nichtnegative Ganzzahlen parsen; alles andere verwerfen.
- Indizes **vor** dem Entfernen leerer Zeilen zuordnen — sonst wandert eine Freigabe, wenn
  davor eine leere Zeile steht.
- Out-of-range-, negative und doppelte Indizes ignorieren.
- Unterschiedlich lange Listen `telefon_nummer`/`telefon_label` ablehnen (HTTP 400) statt
  wie `links_aus_form` still mit `zip(strict=False)` zu kürzen (`objekt_service.py:708`) —
  bei einer Versandfreigabe ist stilles Kürzen nicht akzeptabel.
- Höchstzahl Telefonzeilen (20) und Längen (Nummer 30, Label 40) serverseitig begrenzen.

Tests: negative, nichtnumerische, doppelte, sehr große und out-of-range Indizes; leere
Zeile vor einer markierten Zeile; ungleich lange Listen.

## R6. Migration 0224 — verlustbehaftet, ehrlich dokumentiert

Der Downgrade **kann nicht verlustfrei sein**. Die Behauptung „spiegelbildlich" und der
Roundtrip-Test aus dem Ursprungsplan entfallen. Festgelegte Policy:

- `benachrichtigung_sms = any(eintrag.sms)`
- `benachrichtigung_telefon = erste freigegebene Nummer` (weitere gehen verloren)
- `kontakt_info_enabled = True`, wenn das Objekt mindestens einen freigegebenen Kanal hat
- Verlust im Docstring der Migration ausdrücklich benennen

Technisch:
- Unter SQLite **alle** Drops (Spalten und Constraint) über `batch_alter_table` — aus dem
  `DROP DEFAULT`-Sonderweg in `0223:15-23` folgt nicht, dass normale Drops portabel sind.
- Unter MySQL beim Constraint-Drop `type_="unique"` angeben.
- Verifikation: `0223 → 0224` und `0224 → 0223` müssen **fehlerfrei laufen**; ein
  Datengleichstand wird nicht erwartet.
- Testdaten für die Datenmigration: `NULL`, leere Liste, kaputtes JSON, gemischte Formate,
  normalisierte Duplikate, mehrere SMS-Freigaben.

## R7. Panel-Aggregation: Tenant und N+1

- `_panel_context()` lädt Kontakte bisher **nicht** mit (`ui_objekt.py:2508-2512` lädt nur
  Gefahren und BMA) → für `hat_empfaenger` `selectinload(ObjektEinsatz.objekt).selectinload(Objekt.kontakte)`
  ergänzen, sonst N+1.
- Die neue Aggregation explizit über `org_id` bzw. die bereits gescopten Objekt-IDs
  einschränken (CLAUDE.md: der Listener filtert nur SELECTs).
- Cross-Org-Test für die Aggregation ergänzen.

## R8. Protokoll-Retention

`ObjektKontaktBenachrichtigung` speichert Empfänger **und** vollen Nachrichtentext
(`app/models/objekt.py:745-750`, geschrieben in `objekt_kontakt_notify.py:227-231`). Mit
mehreren Empfängern je Kontakt wächst das. Für diese Änderung: **keine** neue
Retention-Mechanik bauen, aber im Modell-Docstring festhalten, dass die Zeilen
personenbezogen sind und mit dem Einsatz (`ON DELETE CASCADE`) verschwinden. Ein eigenes
Löschkonzept analog `sms_log_retention.py` ist ein separates Thema.
