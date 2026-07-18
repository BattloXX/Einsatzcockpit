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
| `NACHSCHLAGEWERK_SYNC_ENABLED` | Täglicher Gefahrgut-Sync (03:00 Europe/Vienna) | `true` |
| `NACHSCHLAGEWERK_GEFAHRGUT_URL` | Quelle des vollständigen `;`-getrennten Gefahrgut-Datensatzes | *(leer → Seed)* |
| `NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE` | On-demand-Quelle für Rettungskarten-PDFs, mit `{hersteller}`/`{modell}` | *(leer → nur Deep-Links)* |
| `NACHSCHLAGEWERK_RETTUNGSKARTEN_MAX_BYTES` | Max. PDF-Größe je Rettungskarte | `26214400` (25 MB) |

> Ohne gesetzte URLs ist das Modul **sofort nutzbar**: Gefahrgut läuft gegen den mitgelieferten Seed-Datensatz, Rettungskarten zeigen Deep-Links auf offizielle Freigabe-Quellen.

---

## Gefahrgut-Datensatz (`NACHSCHLAGEWERK_GEFAHRGUT_URL`)

Der mitgelieferte Seed (`app/data/bam_gefahrgut.csv`) ist ein kleiner redaktioneller Auszug (~20 häufige ADR-Stoffe). Für den vollständigen Bestand (~9.500 Stoffe) den **Datenservice der Datenbank GEFAHRGUT der BAM** nutzen — seit 23.07.2025 **kostenlos**, veröffentlicht unter **dl-de/by-2.0** (Quellenangabe „Datenbank GEFAHRGUT, BAM" Pflicht).

**Format:** die BAM liefert `BAM-Gefahrgutdaten.csv` — **`;`-getrennt**, Spaltennamen in der ersten Zeile. Der Parser ordnet die Spalten **tolerant** über Schlüsselwörter zu (UN-Nummer, Benennung/Stoffname, Klasse, Klassifizierungscode, Gefahrnummer/Kemler, Verpackungsgruppe).

### Vorschläge

**Empfohlen (voll automatisch, kein manueller Aufwand):**
Die BAM stellt die Daten als **ZIP** bereit — der Sync erwartet aber eine **direkte `;`-CSV**. Daher die entpackte `BAM-Gefahrgutdaten.csv` **einmalig auf eigenem Webspace ablegen** und die URL dorthin setzen:

```env
NACHSCHLAGEWERK_GEFAHRGUT_URL=https://einsatzcockpit.com/static/data/bam_gefahrgut.csv
```

Der tägliche Loop lädt dann automatisch die aktuelle Datei; ein Aktualisieren dieser einen Datei (BAM aktualisiert wenige Male im Jahr) genügt.

**Bezugsquellen für die CSV:**
- BAM-Datenservice (ADR im BAM-Nummern-System): <https://www.dgg.bam.de/de/produkte/datenservice/testdaten_download/>
- Web-App zur Kontrolle einzelner UN-Nummern: <https://www.dgg.bam.de/dgginfo/>
- Offene-Daten-Spiegel: <https://offenedaten.de/dataset/gefahrgut>

> **Prüfschritt:** Nach dem ersten Ablegen einmal `1203` (Benzin) und `Chlor` suchen. Kommen Treffer mit korrekter Klasse/Kemler, passt die Spaltenzuordnung. Falls nicht: Spaltenüberschriften der BAM-CSV mit den o. g. Schlüsselwörtern abgleichen.
>
> **Alternative (Codeerweiterung):** Soll direkt die BAM-**ZIP-URL** gesetzt werden können, muss der Sync um ein Entpacken erweitert werden — auf Wunsch umsetzbar.

Der Sync übernimmt einen neuen Stand nur, wenn er **≥ 50 gültige UN-Zeilen** enthält (Schutz gegen kaputte Downloads), und ersetzt die lokale Datei **atomar**.

---

## Rettungsdatenblätter (`NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE`)

Rettungskarten (ADAC / Euro Rescue / Hersteller) sind **urheberrechtlich geschützt** — ein automatisches Massen-Spiegeln verstößt gegen deren Nutzungsbedingungen. Das Modul arbeitet daher **on-demand**: beim ersten Aufruf zu einem Modell wird **eine** Karte geladen und lokal gecacht (danach offline verfügbar). Es gibt **keine** frei abrufbare Modell-→-PDF-Schnittstelle von Euro Rescue/ACEA (Euro Rescue ist eine App).

### Vorschläge

**Empfohlen (rechtlich sauber, Standard):** Template **leer lassen**. Dann zeigt die Suche **Deep-Links** auf offizielle Freigabe-Quellen (Euro Rescue, Herstellerseiten) — kein Hosting, keine Rechtsfragen, aber auch kein Offline-Cache.

**Für echten Offline-Betrieb (mit eigener, lizenzierter Sammlung):** Offiziell bezogene/lizenzierte PDFs (z. B. ADAC für Mitglieder, Herstellerdownloads) auf **eigenem Webspace** nach einem Namensschema ablegen und das Template daraufsetzen:

```env
NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE=https://einsatzcockpit.com/static/rettungskarten/{hersteller}_{modell}.pdf
```

`{hersteller}` und `{modell}` werden URL-sicher eingesetzt (Leerzeichen → `+`). Beim ersten Aufruf holt das Modul die passende Datei aus **eurem** Bestand und cacht sie — legal, weil selbst beschafft, und trotzdem automatisch.

> **Nicht** ADAC/Euro Rescue automatisiert abgreifen — das verletzt deren Nutzungsbedingungen. Auslieferung im Tool läuft unter `/nachschlagewerk-cache/rettungskarten/{id}/original.pdf` (unveränderliche URL → Offline-Cache).

**Bezugsquellen / Deep-Links:**
- ADAC Rettungskarten: <https://www.adac.de/rund-ums-fahrzeug/unfall-schaden-panne/rettungskarte/>
- ÖAMTC Rettungskarte: <https://www.oeamtc.at/>
- Euro Rescue (Euro NCAP/CTIF, App): App Stores

---

## Offline-Funktion

- **Gefahrgut:** der komplette Datensatz wird als `GET /nachschlagewerke/gefahrgut/index.json` bereitgestellt und vom **Service Worker** gecacht (Bucket `ec-nachschlagewerk-v1`). Die Suche läuft clientseitig — **auch ohne Netz**.
- **Rettungskarten:** bereits einmal geöffnete PDFs liegen im selben Cache-Bucket (cache-first) und sind danach offline verfügbar.
- **Kartenkacheln** bleiben online (unverändertes Verhalten).

App-Updates löschen diesen Offline-Bestand **nicht**.

---

## So funktioniert es intern (Kurzüberblick)

| Bereich | Umsetzung |
|---------|-----------|
| Feature-Flag | `SystemSettings.nachschlagewerke_module_enabled` AND `OrgSettings.nachschlagewerke_module_enabled` |
| Gefahrgut-Suche | `app/services/gefahrgut_service.py` (`suche`, `eintrag_un`, `alle_eintraege`) |
| Täglicher Sync | `app/services/nachschlagewerk_sync.py` (Loop 03:00, atomarer Ersatz) |
| Rettungskarten | Model `RettungsdatenblattCache`, `app/services/rettungskarten_service.py` |
| Karten-Overlays | LagefuehrungFeature-Typen `gefahrenradius` / `ausbreitung`; `evakuierung_service.py`, `ausbreitung_service.py` |

Technischer Plan: `docs/plans/nachschlagewerke-plan.md`.

---

## Verwandte Seiten

- [Nachschlagewerke (Anwender)](Anwender-Nachschlagewerke)
- [Objektverwaltung](Administration-Objektverwaltung) — Objekt-Gefahren nutzen dieselbe Gefahrgut-Anreicherung
- [Lageführung](Anwender-Lagefuehrung) — hier liegen die Karten-Overlays
