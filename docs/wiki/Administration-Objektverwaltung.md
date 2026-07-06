# Objektverwaltung (Administration)

Einrichtung und Betrieb des Objektmoduls: Aktivierung, Rollen, Kataloge, Alarm-Matching,
Alarm-Infoscreen, KI-Klassifizierung und Serverkonfiguration.

Anwender-Doku: [Objekte](Anwender-Objekte)

## Modul aktivieren (zweistufig, wie UAS)

Das Modul ist **zweistufig schaltbar** — beide Schalter müssen an sein:

1. **Systemweit** (nur `system_admin`): `/admin/settings` → Abschnitt **„Systemweite Module"** →
   **🏢 Objektverwaltung** → „Systemweit aktivieren". Setzt den SystemSettings-Key
   `objekt_module_enabled`.
2. **Je Organisation** (Org-Admin): `/admin/settings` → Abschnitt **„🏢 Objektverwaltung"** →
   „Objektverwaltung für diese Organisation aktivieren". Solange das System-Flag aus ist,
   ist die Org-Checkbox ausgegraut.

Ausschalten (egal auf welcher Ebene) **versteckt nur die Ansichten** — es werden keine Daten
gelöscht. Beim Wiedereinschalten ist alles sofort wieder da. Jeder Toggle wird im Audit-Log
protokolliert (`objekt.system_toggle` / `objekt.org_toggle`).

Bei inaktivem Modul liefern alle `/objekte`-Routen **404**; der Navigationseintrag, das
Board-Panel und der Offline-Sync verschwinden.

## Weitere Org-Einstellungen

Im selben Abschnitt unter `/admin/settings` (sichtbar sobald das System-Flag an ist):

| Einstellung | Default | Wirkung |
|-------------|---------|---------|
| **Geo-Matching-Radius** | 75 m | Fallback-Stufe des Alarm-Matchings: Einsätze ohne BMA-/Adress-Treffer werden dem nächstgelegenen Objekt innerhalb dieses Radius als *Vorschlag* zugeordnet. In dichten Ortskernen eher 40 m. |
| **KI-Dokumentklassifizierung** | aus | Opt-in für Vision-Vorschläge beim Klassifizieren von Dokumentseiten (siehe unten) |

## Rollen

| Rolle | Rechte im Objektmodul |
|-------|----------------------|
| Alle angemeldeten Benutzer der Org | Freigegebene Objekte lesen, Einsatzansicht, Dokumente ansehen, Objektblatt drucken |
| `objekt_verwalter` (neu) | Objekte anlegen/bearbeiten/freigeben/archivieren, Dokumente hochladen und klassifizieren, Lagekarte pflegen, KI-Vorschläge entscheiden |
| `incident_leader` | Zusätzlich: Objekt-Verknüpfungen am Einsatz bestätigen/lösen/manuell setzen |
| `org_admin` | Alles, zusätzlich: Kataloge, Modul-Toggle, Infoscreen-Tokens, Objekte endgültig löschen |

Die Rolle **Objektverwalter** wird beim Seed automatisch angelegt und unter
[Benutzer und Rollen](Administration-Benutzer-und-Rollen) zugewiesen — gedacht für
Brandschutz-/Objektbeauftragte, die pflegen dürfen, ohne Org-Admin zu sein.

## Kataloge, Auswahllisten & Symbole

Unter **`/objekte/kataloge`** (Org-Admin) pflegst du alle Auswahllisten der Objektverwaltung in Tabs;
Standardeinträge werden bei der Migration bzw. beim Anlegen neuer Organisationen automatisch angelegt.
Standardeinträge (🔒) lassen sich umbenennen/deaktivieren, aber nicht löschen; Einträge mit Objektbezug
sind ebenfalls löschgeschützt (nur deaktivieren).

- **Kategorien**: Gewerbe/Industrie, Wohnanlage, Öffentliches Gebäude, Landwirtschaft, Sonderobjekt, …
- **Gefahren**: Name + Piktogramm-Typ (steuert die Chip-Darstellung in Einsatzansicht, Infoscreen und
  Druck) + optionale **Standard-Links je Gefahrenart** (gelten für alle Objekte mit dieser Gefahr)
- **Merkmale**: Name + Icon (Schlüsselbox, Brandschutzplan, Drehleiterstellplatz, Tiefgarage, Sprinkler, RWA, …)
- **Kontaktarten**, **Dokumentarten** und **Gefahren-Piktogramme**: die früher fest verdrahteten
  Auswahllisten sind jetzt pflegbar (die Dokumentarten steuern u. a. die KI-Klassifikation)
- **Karten-Symbole**: der Symbolkatalog der Objekt-Lagekarte — Kurztext/Emoji + Stil (Kasten, Dreieck,
  Pfeil, Hydrant, …) oder **eigenes Symbolbild hochladen** (SVG/PNG, bis 512 KB) mit Live-Vorschau

## Alarm-Matching

Läuft automatisch bei jeder Einsatzanlage (Alarm-API, LIS-Sync, manuelle Anlage) im Hintergrund,
nur gegen **freigegebene** Objekte, in dieser Reihenfolge:

1. **BMA-/RFL-Nummer im Alarmtext** — Regex robust gegen Schreibvarianten
   („bmz 1044", „BMA-Nr.: 1044", „rfl/1044") → Verknüpfung **bestätigt**
2. **Adress-Übereinstimmung** (normalisiert, inklusive Stiegen-Zusatzadressen) → **bestätigt**;
   bei mehreren Treffern alle als Vorschlag
3. **Geo-Nähe** (< Radius, siehe oben) → immer nur **Vorschlag**; läuft nach dem
   Hintergrund-Geocoding erneut

Treffer erscheinen sofort im Objekt-Panel am Board (WebSocket) und lösen die
Infoscreen-Alarmansicht aus. Verknüpfen/Lösen wird im Audit-Log dokumentiert.

Zusätzlich legt das System für jede Gefahr eines verknüpften Objekts automatisch eine Meldung in der
Board-Spalte **„Objektgefahren"** an (idempotent, inkl. der gepflegten weiterführenden Links) — beim
Lösen der Verknüpfung werden diese Meldungen wieder entfernt.

## Alarm-Infoscreen

> Der Alarm-Infoscreen ist ein **eigener Verwaltungspunkt**: **Administration → Verwaltung →
> 📺 Alarm-Infoscreen**. Er ist **unabhängig vom Objektmodul** (funktioniert auch ohne aktivierte
> Objektverwaltung — Objekt-/Gefahren-/Hydranten-Anreicherung greift dann nur, wenn ein Objekt verknüpft ist).

Vollbild-Ansicht für Wandmonitore: `/infoscreen/alarm/{token}` — **öffentlich per Token, kein Login**
(wie der Wetter-Infoscreen). Die Alarmansicht ist als taktisches Lage-Dashboard aufgebaut:

- **Kopfzeile** mit dem **Org-Logo** (aus den Organisations-Einstellungen), Datum/Uhrzeit und einem
  kleinen **Wetter-Badge** (Temperatur + Wind, Open-Meteo, aus den Org-Fallback-Koordinaten).
- **Roter Alarmkopf**: Stichwort-Kürzel-Badge (z. B. `F3`) + die **Einsatzmeldung** als große Kopfzeile,
  Adresse und eine mitlaufende **Einsatzzeit-Uhr**; die **Zu-/Absagen** erscheinen als Zähler.
- **Links** das verknüpfte Objekt mit Gefahren-Piktogrammen (inkl. UN-Nummer) und der
  FSD/BMZ/FBF/Laufkarten-Block.
- **Mitte** die Lagekarte mit den Objektsymbolen **und den Löschwasser-Entnahmestellen (Hydranten,
  OSM + manuell im Objekt gesetzte)** rund um den Einsatz-/Objektstandort.
- **Rechts** die Spalte **„Kräfte im Einsatz"**: die **komplette Ausrückordnung (AAO)** des Stichworts
  als Grundgerüst. Jede Einheit trägt ihren **BOS-Funkstatus** als Farbpille: noch nicht ausgerückte
  Fahrzeuge stehen laut AAO auf der Wache (**S2**); sobald das **LIS** einen Status meldet bzw. das
  Fahrzeug im Einsatz geführt wird, wird der reale Status gezogen (**S4** zum Einsatzort, **S5** am
  Einsatzort, **S1** einsatzbereit). **Nachalarmierte** Einheiten außerhalb der AAO werden angehängt.
  Darunter — sobald vorhanden — die **Rückmeldungen mit Namen** (Zusage/Absage je Mitglied).
  Statuswechsel, neue Fahrzeuge und Rückmeldungen erscheinen automatisch (Poll alle 15 s).

Der Wechsel in die Alarmansicht passiert sofort per WebSocket. Ein aktiver Einsatz bleibt sichtbar,
**solange er aktiv ist** (kein Zeitfenster mehr). Läuft eine **Großschadenslage**, zeigt der Monitor
eine eigene Sonderansicht, die bleibt, solange die Lage aktiv ist (Reihenfolge: Großschadenslage →
Einsatz → Ruhe).

Verwaltung unter **`/infoscreen-alarm/verwaltung`** (Org-Admin, eigener Menüpunkt unter Verwaltung):

- **Monitore/Tokens** anlegen (Name je Monitor, z. B. „Fahrzeughalle"). Die vollständige **Monitor-URL
  bleibt dauerhaft sichtbar und kopierbar** (Token verschlüsselt gespeichert; benötigt einen gesetzten
  `FERNET_KEY`). Kompromittierte Tokens deaktivieren.
- **Rotations-URLs** anlegen: beliebige Webseiten mit Verweildauer, die im Ruhezustand rotieren.
- **Monitor-Matrix**: je Monitor auswählen, welche URLs (und ob **Wetter**) im Ruhezustand angezeigt
  werden — verschiedene Monitore können also unterschiedliche Inhalte zeigen. Der Wetter-Eintrag nutzt
  die zentral hinterlegte Wetter-URL, ohne sie je Monitor erneut einzugeben.
- **Ruhezustand** (Fallback ohne konfigurierte URLs): **Uhr** (Standard), **letzte Einsätze** oder
  **Wetter** (bettet den bestehenden Wetter-Infoscreen ein — dessen URL hier manuell hinterlegen,
  da der Wetter-Token nur als Hash gespeichert ist)
- **Großschadenslage-Sonderansicht** an/aus (Standard: an)

**Wohnanlagen-Hinweise am Monitor:** Die einsatztaktischen Wohnanlagen-Hinweise (z. B. „3. OG:
Bewohner mit Gehhilfe") werden bei verknüpftem Objekt in der linken Spalte hervorgehoben angezeigt.
Da der Monitor-Token nur intern (Gerätehaus) bekannt ist, ist das gewollt. **Datenschutz beachten:**
in den Hinweisen sparsam und sachlich bleiben — keine Namen, Diagnosen oder Personenlisten
(wie beim Erfassen in der Objektverwaltung vorgegeben). Am Objektblatt-Druck sind die Hinweise nach
wie vor nur über einen expliziten Parameter enthalten.

## KI-Dokumentklassifizierung

Vision-Analyse der zerlegten PDF-Seiten (Anthropic Claude): schlägt Dokumentart, Titel,
Melderlinien und Stand vor. Voraussetzungen:

1. KI-Assistent aktiv (zentraler Key oder BYOK je Org, siehe [Einstellungen](Administration-Einstellungen))
2. Org-Opt-in **„KI-Dokumentklassifizierung"** in den Org-Einstellungen

Objektverwalter starten die Analyse im Dokumente-Abschnitt („✨ KI-Vorschläge für n Seiten",
max. 20 Seiten je Lauf). Vorschläge landen in einer **Review-Liste** — übernehmen, verwerfen oder
alle übernehmen; **nie automatische Übernahme**. Token-Verbrauch zählt auf das normale
KI-Monatskontingent der Org; Seitenbilder werden vor dem Versand auf ~1024 px verkleinert.

## Serverkonfiguration

```bash
# Debian: Poppler für die PDF-Seiten-Rasterung (pdf2image)
# + Tesseract für die OCR-Volltextsuche gescannter Dokumente
sudo apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-deu

# Migrationen 0124–0137 anwenden
alembic upgrade head
```

Für die **Gefahrgut-Anreicherung** (UN-Nummer → Stoffname/Klasse/Kemler) die vollständige offene
BAM-CSV („Datenbank GEFAHRGUT", Lizenz dl-de/by-2.0) von OffeneDaten.de beziehen und nach
`app/data/bam_gefahrgut.csv` legen (mitgeliefert ist nur ein kleiner Auszug). Ohne Datei bleibt die
manuelle Link-Pflege aktiv. Der **🚒 ERICard-Absprung** (CEFIC-Notfall-Interventionskarte) wird rein
aus UN-Nummer + Gefahrnummer als Deep-Link erzeugt (`ericards.net`, deutsche Ansicht) und braucht
keine lokale Datei — er funktioniert auch ohne die BAM-CSV, sobald eine UN-Nummer gepflegt ist. Für die dauerhaft kopierbaren Monitor-URLs muss `FERNET_KEY` gesetzt sein.

Optionale `.env`-/SystemSettings-Parameter:

| Parameter | Default | Beschreibung |
|-----------|---------|-------------|
| `OBJEKT_MEDIA_DIR` | `app_storage/objekt_media` | Ablage der Original-PDFs, Einzelseiten und Renderings (außerhalb `static`, Auslieferung nur über Auth-Route) |
| `OBJEKT_PDF_MAX_BYTES` | 100 MB | Maximale Dateigröße je Upload (SystemSettings-Override: `objekt_pdf_max_bytes`) |
| `OBJEKT_PDF_MAX_SEITEN` | 300 | Maximale Seitenzahl je PDF (Override: `objekt_pdf_max_seiten`) |
| `OBJEKT_SEITE_RENDER_DPI` | 150 | Auflösung der Hi-Res-Renderings |
| `OBJEKT_OCR_ENABLED` | `true` | OCR-Fallback (Tesseract) für Scan-PDFs ohne Textlayer |
| `OBJEKT_OCR_LANG` | `deu+eng` | Tesseract-Sprachpakete für die OCR |
| `OBJEKT_SYMBOL_MAX_BYTES` | 512 KB | Maximale Größe je hochgeladenem Karten-Symbolbild (SVG/PNG) |

**Speicher-Quota:** Original + Einzelseiten + Renderings zählen auf die Org-Quota
(≈ Faktor 1,5–2 der Originalgröße). Beim Löschen eines Dokuments wird der belegte Speicher
vollständig freigegeben. Ggf. die Quota der Org erhöhen
([Organisationen verwalten](Administration-Organisations-verwalten)).

**Offline-Sync (Android):** `GET /api/objekte/sync` liefert das Manifest aller freigegebenen
Objekte (Session-Auth). Die Android-App synchronisiert damit automatisch alle 6 Stunden Einsatzansichten,
Seitenbilder und PDFs in den lokalen Cache — kein zusätzlicher Serverdienst nötig.
