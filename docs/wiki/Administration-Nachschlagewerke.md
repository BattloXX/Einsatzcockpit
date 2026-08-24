# Nachschlagewerke

← [Zurück zur Startseite](Home)

> URL: `/nachschlagewerke` · Konfiguration: `/admin/settings` + `.env`
> Zugänglich für: alle angemeldeten Nutzer (lesen) · Aktivierung: `system_admin` (systemweit) + `org_admin` (je Org)

Das **Nachschlagewerke-Modul** liefert Einsatzkräften ein **offlinefähiges** Nachschlagewerk am Einsatzort — nach dem Vorbild von rescueTABLET (Gefahrgut + Rettungskarten) und Alamos FE2 (Evakuierungsradien + Ausbreitung):

- **Gefahrgut** — Suche nach UN-Nummer *oder* Stoffname → ERI-Karte (Eigenschaften, Gefahren, Schutzmaßnahmen, ERICard/BAM-Deep-Links)
- **Rettungsdatenblätter** — Rettungskarten für die technische Rettung, Suche nach Hersteller/Modell
- **Karten-Overlays** in der Lageführungskarte — Evakuierungsradius und windbezogene Ausbreitung (Kegel oder Gauß-Modell)

---

## Aktivierung (zweistufig)

Wie bei Objektverwaltung/UAS/Gateway: erst **systemweit**, dann **je Organisation**.

1. **System-Admin:** `Admin → Einstellungen` → Abschnitt **„📚 Nachschlagewerke"** → **Systemweit aktivieren**
2. **Org-Admin:** im selben Dialog die Checkbox **„Nachschlagewerke für diese Organisation aktivieren"** setzen

Erst wenn **beide** Schalter aktiv sind, erscheint der Menüpunkt **„📚 Nachschlagewerke"** (im Dropdown *Dokumentation*, Desktop und mobil). Ausschalten versteckt nur die Ansichten — **keine Daten werden gelöscht**.

---

## Konfiguration (.env)

| Variable | Bedeutung | Default |
|----------|-----------|---------|
| `NACHSCHLAGEWERK_DATA_DIR` | Persistentes Verzeichnis für gesyncte/gecachte Daten (außerhalb des read-only Repos) | `app_storage/nachschlagewerk` |
| `NACHSCHLAGEWERK_SYNC_ENABLED` | Täglicher Sync (03:00 Europe/Vienna) für Gefahrgut **und** Rettungskarten-Katalog | `true` |
| `NACHSCHLAGEWERK_GEFAHRGUT_URL` | Quelle des vollständigen `;`-getrennten Gefahrgut-Datensatzes | *(leer → Seed)* |
| `NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL` | JSON-Katalog-API verfügbarer Rettungskarten (Euro NCAP / CTIF „Euro Rescue") | `https://api.rescue.euroncap.com/euro-rescue/variants` |
| `NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE` | Zusätzliche On-demand-Quelle für Direktabruf, mit `{hersteller}`/`{modell}` | *(leer → nur Katalog/Deep-Links)* |
| `NACHSCHLAGEWERK_RETTUNGSKARTEN_MAX_BYTES` | Max. PDF-Größe je Rettungskarte | `26214400` (25 MB) |

> Ohne zusätzliche Konfiguration ist das Modul **sofort nutzbar**: Gefahrgut läuft gegen den mitgelieferten Seed-Datensatz, und der Rettungskarten-Katalog wird beim Start automatisch aus der frei bereitgestellten Euro-Rescue-API (>2000 Modelle) synchronisiert. `NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL` leeren, um den Katalog-Sync abzuschalten.

---

## Gefahrgut-Datensatz (`NACHSCHLAGEWERK_GEFAHRGUT_URL`)

Der mitgelieferte Seed (`app/data/bam_gefahrgut.csv`) ist ein kleiner redaktioneller Auszug (~20 häufige ADR-Stoffe). Für den vollständigen Bestand (~9.500 Stoffe) den **Datenservice der Datenbank GEFAHRGUT der BAM** nutzen — seit 23.07.2025 **kostenlos**, veröffentlicht unter **dl-de/by-2.0** (Quellenangabe „Datenbank GEFAHRGUT, BAM" Pflicht).

**Format:** die BAM liefert `BAM-Gefahrgutdaten.csv` — **`;`-getrennt**, Spaltennamen in der ersten Zeile. Der Parser ordnet die Spalten **tolerant** über Schlüsselwörter zu (UN-Nummer, Benennung/Stoffname, Klasse, Klassifizierungscode, Gefahrnummer/Kemler, Verpackungsgruppe).

Der Sync akzeptiert **sowohl eine direkte `;`-CSV als auch ein ZIP**: Enthält die Antwort ein ZIP (z. B. der BAM-Download mit `BAM-Gefahrgutdaten.csv` + `BAM-Gefahrgutstatus.csv`), wird es automatisch entpackt und das **richtige** CSV-Member erkannt (das mit den meisten gültigen UN-Zeilen — also die Gefahrgutdaten, nicht die Status-Datei). Zeichensätze UTF-8 und Windows-1252 werden automatisch erkannt.

### Vorschläge

**Empfohlen (voll automatisch, kein manueller Aufwand):** direkt die BAM-**ZIP-URL** setzen — Entpacken übernimmt der Sync:

```env
NACHSCHLAGEWERK_GEFAHRGUT_URL=https://www.dgg.bam.de/.../BAM-Gefahrgutdaten.zip
```

> Die exakte, aktuelle Download-URL des ZIP auf der BAM-Seite (siehe unten) übernehmen — sie ändert sich gelegentlich.

**Alternative (eigener Spiegel):** die entpackte CSV auf eigenem Webspace ablegen und daraufsetzen — nützlich, wenn die BAM-Direkt-URL nicht dauerhaft stabil ist:

```env
NACHSCHLAGEWERK_GEFAHRGUT_URL=https://einsatzcockpit.com/static/data/bam_gefahrgut.csv
```

**Bezugsquellen:**
- BAM-Datenservice (ADR im BAM-Nummern-System): <https://www.dgg.bam.de/de/produkte/datenservice/testdaten_download/>
- Web-App zur Kontrolle einzelner UN-Nummern: <https://www.dgg.bam.de/dgginfo/>
- Offene-Daten-Spiegel: <https://offenedaten.de/dataset/gefahrgut>

> **Prüfschritt:** Nach dem ersten Sync einmal `1203` (Benzin) und `Chlor` suchen. Kommen Treffer mit korrekter Klasse/Kemler, passt die Spaltenzuordnung. Falls nicht: Spaltenüberschriften der BAM-CSV mit den o. g. Schlüsselwörtern abgleichen.

Der Sync übernimmt einen neuen Stand nur, wenn er **≥ 50 gültige UN-Zeilen** enthält (Schutz gegen kaputte Downloads), und ersetzt die lokale Datei **atomar**.

---

## Rettungsdatenblätter

### Modell-Katalog (Euro NCAP / CTIF „Euro Rescue") — Standard

Euro NCAP stellt gemeinsam mit CTIF alle Rettungsblätter **frei für Einsatzkräfte** bereit
(*„Free Downloadable Rescue Information for First Responders"*) — inklusive einer **offenen
JSON-Katalog-API** mit **>2000 Fahrzeugen** und **direkten (überwiegend deutschen) Rettungsblatt-PDFs**.

Das Modul **synchronisiert daraus täglich nur das Verzeichnis** (Hersteller/Modell/Baujahr/Antrieb
+ PDF-Link je Modell — keine Massen-Spiegelung der Dokumente) in die Tabelle
`rettungskarten_katalog`. Einsatzkräfte suchen dann **offlinefähig** nach Hersteller/Modell
(clientseitig über einen gecachten Index) und öffnen die passende Karte. **Erst beim Öffnen**
wird das PDF on-demand geladen und lokal gecacht — danach offline verfügbar.

- Ablauf: **Suchen → „📄 Öffnen"** → PDF wird geladen, gespeichert und geöffnet (deutsche Karte
  bevorzugt, sonst englische). Marken-Kürzel wie „VW" finden „Volkswagen".
- Steuerung: `NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL` (Default = Euro-Rescue-API); leeren
  schaltet den Katalog-Sync ab. Sync im täglichen Nachschlagewerk-Loop (03:00, Start-Sync sofort).
- Auslieferung des PDFs unter `/nachschlagewerk-cache/rettungskarten/{id}/original.pdf`
  (unveränderliche URL → Offline-Cache, Bucket `ec-nachschlagewerk-v1`).

### Zusätzlicher Direktabruf (`NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE`) — optional

Für Modelle **außerhalb** des Katalogs oder eine **eigene, lizenzierte Sammlung**: ein
URL-Template mit `{hersteller}`/`{modell}` (URL-sicher eingesetzt) auf **eigenem Webspace** setzen.
Der einklappbare „Direktabruf"-Bereich der Seite lädt dann daraus on-demand und cacht ebenfalls.

```env
NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE=https://einsatzcockpit.com/static/rettungskarten/{hersteller}_{modell}.pdf
```

> **Nicht** ADAC/Hersteller automatisiert abgreifen — das verletzt deren Nutzungsbedingungen. Der
> Euro-Rescue-Katalog ist ausdrücklich frei für Einsatzkräfte und daher unbedenklich.

**Weitere Bezugsquellen / Deep-Links (Fallback):**
- Euro Rescue (Euro NCAP/CTIF): <https://rescue.euroncap.com/> · App Stores
- ADAC Rettungskarten: <https://www.adac.de/rund-ums-fahrzeug/unfall-schaden-panne/rettungskarte/>
- ÖAMTC Rettungskarte: <https://www.oeamtc.at/>

---

## Offline-Funktion

- **Gefahrgut:** der komplette Datensatz wird als `GET /nachschlagewerke/gefahrgut/index.json` bereitgestellt und vom **Service Worker** gecacht (Bucket `ec-nachschlagewerk-v1`). Die Suche läuft clientseitig — **auch ohne Netz**.
- **Rettungskarten-Katalog:** `GET /nachschlagewerke/rettungskarten/katalog.json` wird ebenfalls vom Service Worker gecacht → **Modellsuche auch ohne Netz**. Bereits einmal geöffnete PDFs liegen im selben Cache-Bucket (cache-first) und sind danach offline verfügbar.
- **Kartenkacheln** bleiben online (unverändertes Verhalten).

App-Updates löschen diesen Offline-Bestand **nicht**.

---

## So funktioniert es intern (Kurzüberblick)

| Bereich | Umsetzung |
|---------|-----------|
| Feature-Flag | `SystemSettings.nachschlagewerke_module_enabled` AND `OrgSettings.nachschlagewerke_module_enabled` |
| Gefahrgut-Suche | `app/services/gefahrgut_service.py` (`suche`, `eintrag_un`, `alle_eintraege`) |
| Täglicher Sync | `app/services/nachschlagewerk_sync.py` (Loop 03:00: Gefahrgut-CSV + Rettungskarten-Katalog) |
| Rettungskarten-Katalog | Model `RettungskartenKatalog` (Migration `0169`), `app/services/rettungskarten_katalog_service.py` (Sync/Suche), JS `static/js/rettungskarten_katalog.js` (Offline-Suche) |
| Rettungskarten-PDF (on-demand) | Model `RettungsdatenblattCache`, `app/services/rettungskarten_service.py` (`hole_aus_katalog`, `finde_oder_hole`) |
| Karten-Overlays | LagefuehrungFeature-Typen `gefahrenradius` / `ausbreitung`; `evakuierung_service.py`, `ausbreitung_service.py` |

Technischer Plan: `docs/plans/nachschlagewerke-plan.md`.

---

## Verwandte Seiten

- [Nachschlagewerke (Anwender)](Anwender-Nachschlagewerke)
- [Objektverwaltung](Administration-Objektverwaltung) — Objekt-Gefahren nutzen dieselbe Gefahrgut-Anreicherung
- [Lageführung](Anwender-Lagefuehrung) — hier liegen die Karten-Overlays
