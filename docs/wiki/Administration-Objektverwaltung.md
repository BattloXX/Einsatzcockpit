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

## Kataloge

Unter **`/objekte/kataloge`** (Org-Admin) pflegst du drei Kataloge in Tabs; Standardeinträge
werden bei der Migration bzw. beim Anlegen neuer Organisationen automatisch angelegt:

- **Kategorien**: Gewerbe/Industrie, Wohnanlage, Öffentliches Gebäude, Landwirtschaft, Sonderobjekt, …
- **Gefahren**: Name + Piktogramm-Typ (EX, Gas, Chemie, Hochspannung, PV, NH3, hohe Brandlast, sonstig) —
  der Piktogramm-Typ steuert die Chip-Darstellung in Einsatzansicht, Infoscreen und Druck
- **Merkmale**: Name + Icon (Schlüsselbox, Brandschutzplan, Drehleiterstellplatz, Tiefgarage, Sprinkler, RWA, …);
  eigene Merkmale können ergänzt werden. Einträge mit Objektbezug lassen sich nicht löschen, nur deaktivieren.

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

## Alarm-Infoscreen

Vollbild-Ansicht für Wandmonitore: `/infoscreen/alarm/{token}` — **öffentlich per Token, kein Login**
(wie der Wetter-Infoscreen). Bei Alarm zeigt sie Stichwort + Adresse groß, das verknüpfte Objekt mit
Gefahren-Piktogrammen, die Karte mit Objektsymbolen und die FSD/BMZ/FBF-Standorte; der Wechsel
passiert sofort per WebSocket.

Verwaltung unter **`/infoscreen-alarm/verwaltung`** (Org-Admin, Link auch in der Objektliste):

- **Tokens** anlegen (Name je Monitor, z. B. „Fahrzeughalle") — die URL wird **einmalig** im
  Klartext angezeigt, gespeichert wird nur ein Hash. Kompromittierte Tokens deaktivieren.
- **Ruhezustand** ohne Alarm: **Uhr** (Standard), **letzte Einsätze** oder **Wetter**
  (bettet den bestehenden Wetter-Infoscreen ein — dessen URL hier manuell hinterlegen,
  da der Wetter-Token nur als Hash gespeichert ist)
- **Anzeigedauer** der Alarmansicht (Standard 60 min), danach zurück in den Ruhezustand

**DSGVO:** Wohnanlagen-Hinweise (z. B. zu hilfsbedürftigen Personen) werden **nie** an den
Infoscreen ausgeliefert — fest im Code, nicht konfigurierbar. Am Objektblatt-Druck sind sie nur
über einen expliziten Parameter enthalten.

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
sudo apt-get install -y poppler-utils

# Migrationen 0124–0130 anwenden
alembic upgrade head
```

Optionale `.env`-/SystemSettings-Parameter:

| Parameter | Default | Beschreibung |
|-----------|---------|-------------|
| `OBJEKT_MEDIA_DIR` | `app_storage/objekt_media` | Ablage der Original-PDFs, Einzelseiten und Renderings (außerhalb `static`, Auslieferung nur über Auth-Route) |
| `OBJEKT_PDF_MAX_BYTES` | 100 MB | Maximale Dateigröße je Upload (SystemSettings-Override: `objekt_pdf_max_bytes`) |
| `OBJEKT_PDF_MAX_SEITEN` | 300 | Maximale Seitenzahl je PDF (Override: `objekt_pdf_max_seiten`) |
| `OBJEKT_SEITE_RENDER_DPI` | 150 | Auflösung der Hi-Res-Renderings |

**Speicher-Quota:** Original + Einzelseiten + Renderings zählen auf die Org-Quota
(≈ Faktor 1,5–2 der Originalgröße). Beim Löschen eines Dokuments wird der belegte Speicher
vollständig freigegeben. Ggf. die Quota der Org erhöhen
([Organisationen verwalten](Administration-Organisations-verwalten)).

**Offline-Sync (Android):** `GET /api/objekte/sync` liefert das Manifest aller freigegebenen
Objekte (Session-Auth). Die Android-App synchronisiert damit automatisch alle 6 Stunden Einsatzansichten,
Seitenbilder und PDFs in den lokalen Cache — kein zusätzlicher Serverdienst nötig.
