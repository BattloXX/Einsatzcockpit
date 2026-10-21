# Ausbau: Einsatz- & Katastrophenpläne (E1–E6)

## Context
Das Modul „Katastrophenpläne“ (K1–K13, M1–M6) bildet heute nur eine Art von Plan ab. Der Pilot hat gezeigt, dass der Wolfurter „HOCHWASSER – Maßnahmenplan“ nur als normaler Katastrophenplan importiert werden konnte. Alarm- und Einsatzpläne (AEP) lassen sich gar nicht sauber ablegen.

Geplant sind:
- eine eigene Planart,
- Maßnahmen getrennt nach Akteur (Feuerwehr / Netzbetreiber / Behörde …),
- Telefonnummern nur noch über zentrale ECP-Kontakte,
- sichtbare Quellennachweise bis zur PDF-Seite,
- ein verbindlicher Prüfzyklus mit verantwortlicher Person.

Getroffene Entscheidungen (User, 2026-10-21):
- **Name:** „Einsatz- & Katastrophenpläne“. Die neue URL ist `/plaene`, die alte leitet weiter.
- **Akteure:** Standardkatalog, je Org erweiterbar.
- **Telefonnummern im Text:** nur Warnung, keine Freigabesperre. Dazu kommt eine Umwandlungshilfe.
- **Überprüfung:** Intervall + verantwortlicher Benutzer + Bestätigung „überprüft, keine Änderung“ ohne neue Version.

Intern bleiben das Präfix `katplan_*`, die Rollen-Codes `katplan_verwalter`/`katplan_freigeber` und die **MCP-Toolnamen** unverändert, damit KI-Clients stabil bleiben. Nur Texte und Beschreibungen ändern sich.

Was es schon gibt (Erkundung):
- Quelle je Element: `quelle_dokument_id` + `quelle_seiten`, mit Link zur Seitenansicht.
- Wiedervorlage: `KatplanPlan.wiedervorlage_am`, Mail an die Freigeber in `task_reminder._check_katplan_wiedervorlagen`.
- Prüfprotokoll: `KatplanPruefung`.
- Telefon-QS-Hinweis `telefon_im_text`, prüft aber nur `beschreibung_html` und nur bei vorhandenem Kontakt-Link.
- Link-Rollen: `KATPLAN_LINK_ROLLEN`.

Letzte Migration: 0301, neue also ab 0302 (Alembic-IDs nur Nummern).

## Umsetzung in Paketen (je 1 PR, Implementierung per Codex nach bewährtem Muster)

### E1 – Umbenennung + Planart „Alarm- und Einsatzplan“ (Migration 0302)
- **Modell:** `KatplanPlan.planart` String(30), NOT NULL, Default `katastrophenplan`.
  - Werte in `app/models/katplan.py` als `KATPLAN_PLANARTEN = {alarm_einsatzplan: „Alarm- und Einsatzplan“, massnahmenplan: „Maßnahmenplan“, katastrophenplan: „Katastrophenplan“}`.
  - Bestand → `katastrophenplan`, der Pilot-Hochwasserplan per Hand → `massnahmenplan`.
- **Planart-Metadaten** in `app/services/katplan_typen.py` (neues `PLANARTEN`):
  - Label, Kürzel (AEP/MP/KP), Icon, Standard-Prüfintervall (AEP 12, MP 24, KP 24 Monate).
  - Kapitelvorlage für neue Pläne. AEP: Alarmierung · Erstmaßnahmen · Einsatzdurchführung · Verständigungen · Objekt-/Anfahrtsinfos · Abschluss. Die Phasen sind als Alarmstufen vorbelegt.
- **UI:**
  - Auswahl im Formular `katplan/formular.html` („Neuer Plan“ mit Planart zuerst).
  - Planart-Chip in `_liste.html`/`_kopf.html` und Filter in der Liste.
  - Der PDF-Titel richtet sich nach der Planart (`pdf/katplan.html`, `katplan_pdf_service.py`).
  - Der Snapshot (`erstelle_snapshot` in `katplan_freigabe_service.py`) nimmt `planart` in `plan_json` auf.
  - `stammdaten_aendern` erlaubt `planart`.
- **Umbenennung:**
  - Router-Präfix `/katastrophenplaene` → `/plaene` (`app/routers/ui_katplan.py:81`).
  - Neuer kleiner Redirect-Router `/katastrophenplaene/{rest:path}`: 308, behält Methode und Query.
  - Danach alle URLs in Templates/Services/Tests ersetzen.
- **Texte:**
  - Navigation `modul_navigation.py:39`
  - Seitentitel unter `templates/katplan/*`
  - `admin/settings.html`
  - Rollenlabels `seed_data.py:38-39`. Bestehende DB-Labels prüfen, ggf. per Migration nachziehen.
  - Mail-Betreff in `task_reminder.py`
  - `public_content.py`
  - `app/core/tenant.py:128`
  - MCP-Beschreibungen in `app/mcp/tools/katplan*.py` und `suche.py`
  - Suche-Kategorie-Label

### E2 – Prüfzyklus mit verantwortlicher Person (Migration 0303)
- **Neue Felder an `KatplanPlan`:**
  - `pruef_verantwortlich_user_id` (FK user, SET NULL)
  - `pruefintervall_monate` (int, NULL = Default der Planart)
  - `zuletzt_geprueft_am` (date) und `zuletzt_geprueft_von_id`
  - `wiedervorlage_eskaliert_am` (date)

  `wiedervorlage_am` bleibt als „nächste Überprüfung fällig“.
- **Service** `katplan_freigabe_service.py`:
  - Neu `ueberpruefung_bestaetigen(plan, user, kommentar)`:
    - nur bei Status `freigegeben` und nur ohne offene Arbeitsstand-Änderungen; sonst Hinweis „erst freigeben“;
    - erlaubt dem Verantwortlichen oder einem Freigeber;
    - schreibt `KatplanPruefung(aktion="ueberprueft", version_id=aktuelle)`;
    - setzt das nächste Datum = heute + Intervall und setzt `wiedervorlage_erinnert_am`/`_eskaliert_am` zurück.
  - `freigeben()` setzt das Prüfdatum ebenfalls neu.
- **Erinnerung** (`task_reminder._check_katplan_wiedervorlagen` + `pruefe_wiedervorlagen`):
  - Mail an die verantwortliche Person 14 Tage vorher und bei Fälligkeit.
  - Ab 30 Tagen überfällig Eskalation an `katplan_freigeber`. Ohne verantwortliche Person geht die Mail wie bisher an die Freigeber.
- **UI:**
  - Im Kopf (`_kopf.html`) für alle Leser: „Geprüft am … von … · nächste Prüfung … · verantwortlich …“, dazu der Button „Überprüfung bestätigen“ (Dialog mit Kommentar).
  - Im Reiter Versionen eine gemeinsame Zeitleiste aus Freigaben und Überprüfungen.
  - Listenfilter „Überprüfung fällig“.
  - Die Org-Einstellung für Default-Intervalle je Planart liegt in `admin/settings.html`.
  - Im PDF erscheint der Prüfvermerk in der Kopftabelle.
  - Bezeichnungen klarstellen: „Verantwortliche Stelle (extern)“ vs. „Überprüfung durch (ECP-Benutzer)“.

### E3 – Rollenbezogene Maßnahmen / Akteure (Migration 0304)
- **Neue Tabelle `katplan_akteur`:** org_id, schluessel, bezeichnung, farbe, icon (Tabler), sortierung, aktiv.
  - Der Standardkatalog wird lazy je Org angelegt: Feuerwehr, Netzbetreiber, Behörde, Polizei, Rettungsdienst, Gemeinde/Bauhof, Sonstige.
  - Die Pflege liegt in den Admin-Einstellungen. Deaktivieren statt Löschen, sobald ein Akteur verwendet wird.
- **Akteure am Element:**
  - `KatplanElement.akteure_json` (Liste von Schlüsseln) und dasselbe Feld an `KatplanVersionElement`, damit Snapshots stabil bleiben. Der Snapshot speichert dazu auch die Bezeichnungen.
  - Mehrfachauswahl im Editor `_element_form.html` für alle Typen; sichtbar vor allem bei massnahme/ablauf/zustaendigkeit.
  - Akteur in `suchtext` aufnehmen.
- **Leseansicht** `_version_inhalt.html`:
  - Akteur-Filterchips analog zu den Phasen-Chips.
  - Umschalter „Gliederung: Kapitel | Akteur“, wobei die Akteur-Ansicht die Maßnahmen je Akteur gruppiert.
  - Farbige Akteur-Chips in `_element_anzeige.html`.
- **PDF:**
  - Optional „nach Akteur gliedern“.
  - „Auszug für Akteur X“, z. B. nur der Netzbetreiber-Teil zum Weitergeben, über einen Parameter an `render_katplan_html`.
- **QS:** neuer Hinweis `massnahme_ohne_akteur` für massnahme-Elemente in AEP/Maßnahmenplänen.
- **MCP:**
  - `akteure` in `katplan_element_entwurf_anlegen/_aendern` (Validierung gegen den Katalog in `validiere_element_felder`).
  - Filter `akteur` in `katplan_elemente`.
  - Katalog in `katplan_lesen`.

### E4 – Zentrale Kontakte statt Telefonnummern (keine Migration)
- **QS** `katplan_qs_service.py`:
  - `telefon_im_text` prüft künftig `beschreibung_html` **und** alle Textfelder in `daten_json` (Schritte, Hinweise …), auch ohne vorhandenen Kontakt-Link.
  - Der Hinweis nennt die gefundene Nummer.
  - Er bleibt eine Warnung und wird im Freigabe-Dialog als Zusammenfassung angezeigt („3 Elemente mit Telefonnummern im Text“).
- **Umwandlungshilfe** (HTMX-Dialog vom QS-Hinweis aus):
  - Die erkannte Nummer wird normalisiert und über `nummer_normalisiert` in `KontaktTelefon` gesucht (Duplikatlogik aus dem Kontaktmodul wiederverwenden, vgl. `kontakt_duplikate_pruefen`).
  - Wahlweise einen bestehenden Kontakt verknüpfen oder einen neuen Kontakt vorbefüllt anlegen. Verknüpft wird per `KatplanLink`, Rolle frei wählbar, Default `zu_verstaendigen`.
  - Optional die Nummer aus dem Text entfernen.
  - Das Anlegen setzt das Kontaktrecht voraus, sonst ist nur Verknüpfen möglich.
- **Gemeinsames Makro** `templates/_macros/kontakt_karte.html` (Name, Funktion, Organisation, `tel:`-Buttons) für `katplan/_links_anzeige.html`. Optional später auch `objekt/_kontakte.html`/`incident/_kontakte.html`, um die Duplikate abzubauen.
- **AEP-Komfort:**
  - Das Kapitel „Verständigungen“ zeigt automatisch alle Links mit Rolle `zu_verstaendigen`/`verantwortlich`, gruppiert nach Akteur. Das ersetzt die getippte Telefonliste.
  - Im PDF als Verständigungsliste aus den Live-Kontaktdaten zum Freigabezeitpunkt (bestehender `link_snapshot`).
- **MCP:** `katplan_element_lesen` liefert die erkannten Nummern als Hinweis.

### E5 – Quellennachweise je Maßnahme schärfen (keine Migration)
Die Basis gibt es bereits: Dokument + Seite je Element. Ergänzt wird:
- **Leseansicht:**
  - Quellen-Chip „Quelle S. 13“ an jeder Maßnahme.
  - Popover mit Seitenthumbnail (`KatplanDokumentSeite.thumb`).
  - Link „Original-PDF auf Seite öffnen“ (Datei-Route mit `#page=N`) zusätzlich zur Seitenansicht.
- **PDF-Export:** Quellenangabe als Fußzeile je Maßnahme sowie ein Quellenverzeichnis am Ende (Dokument, Version, Seiten).
- **QS:**
  - Neuer Hinweis `quelle_fehlt` für Maßnahmen, wenn der Plan Quelldokumente hat (Hinweis, keine Sperre).
  - Der Hinweis `quelle_neuer` bleibt.
- **Editor:** Seitenauswahl mit Thumbnail-Vorschau statt Freitext-Seitenfeld; `parse_seiten`/`validiere_element_quelle` werden wiederverwendet.

### E6 – Doku, Website, Pilot
- Texte anpassen (nur Dateiinhalte, Wiki-Dateinamen bleiben für stabile Links):
  - Wiki `docs/wiki/Anwender-Katastrophenplaene.md`, `Administration-…`, `Entwickler-…`
  - `_Sidebar.md`, `Home.md`
  - README, CHANGELOG
  - Website-Funktionen (`public_content.py`)
- Plan-Doku `docs/plans/einsatzplaene-ausbau.md` mit diesem Stand und einem §Umsetzungsstand.
- **Pilot lokal:**
  - Den Hochwasserplan auf `massnahmenplan` setzen, Akteure zuordnen, Telefon-Hinweise per Assistent auflösen.
  - Einen Beispiel-AEP aus der Vorlage anlegen.

## Kritische Dateien
- `app/models/katplan.py`, `alembic/versions/0302…0304`
- `app/services/katplan_typen.py`, `katplan_service.py`, `katplan_freigabe_service.py`, `katplan_qs_service.py`, `katplan_link_service.py`, `katplan_pdf_service.py`, `katplan_mcp_service.py`, `task_reminder.py`, `modul_navigation.py`
- `app/routers/ui_katplan.py`
- `app/templates/katplan/*` (`_kopf`, `_liste`, `formular`, `_element_form`, `_element_anzeige`, `_version_inhalt`, `_qualitaet`, `_links_anzeige`), `templates/pdf/katplan.html`, `admin/settings.html`
- `app/mcp/tools/katplan.py`, `katplan_schreiben.py`

## Verifikation
- Je Paket: `ruff`, `mypy`, `pytest -n auto` (volle Suite wegen `test_css_klassen_definiert`). Neue Tests:
  - Redirect `/katastrophenplaene/*` → `/plaene/*` (GET+POST)
  - Planart im Snapshot
  - `ueberpruefung_bestaetigen` (Rechte, Datum, Protokoll, Sperre bei offenem Arbeitsstand)
  - Erinnerung/Eskalation mit festem Datum
  - Akteur-Filter + Snapshot-Stabilität bei umbenanntem Akteur
  - Telefon-Erkennung in `daten_json`
  - Umwandlung → `KatplanLink`
  - `quelle_fehlt`
  - MCP-Felder `akteure`/`planart`
- Migrationen gegen MariaDB (CI) und SQLite-Demo.
- Browser-Check mit der SQLite-Demo (Scratchpad-Skript, Port 8141) und Playwright:
  - AEP anlegen
  - Akteur-Gliederung
  - Überprüfung bestätigen
  - Telefon-Assistent
  - Quellen-Popover
  - PDF-Auszug „Netzbetreiber“
  - Dark Mode prüfen (`form-control`)
- Abschließend die CI auf main bestätigen.
- Offen für die Produktion: die Migrationen 0302–0304 ausrollen.

## Abweichungen vom Plan
- E2: Kein eigener Org-Schalter für Standard-Prüfintervalle. Der Standard kommt aus der Planart, das Intervall lässt sich je Plan überschreiben.
- E3: Der Akteurskatalog wird unter `/plaene/akteure` im Modul gepflegt statt in den Org-Einstellungen. Der Akteurskatalog im Snapshot-Kopf zählt nicht zum Inhalts-Hash.

## Umsetzungsstand
| Paket | PR | Stand |
|---|---|---|
| E1 Umbenennung + Planart (0302) | #671 | gemergt 2026-10-21 |
| E2 Prüfzyklus (0303) | #672 | gemergt 2026-10-21 |
| E3 Akteure (0304) | #673 | gemergt 2026-10-21 |
| E4 Zentrale Kontakte/Telefon-Assistent | #674 | gemergt 2026-10-21 |
| E5 Quellennachweise + E6 Doku | #675 | 2026-10-21 |
| E6 Pilot | – | offen (lokal mit Pilot-PDF) |

Offen für die Produktion: Migrationen 0302–0304 ausrollen; Hochwasser-Pilotplan auf „Maßnahmenplan“ umstellen, Akteure zuordnen, Telefon-Hinweise auflösen.
