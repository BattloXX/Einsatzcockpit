# Übergreifende Meldungen

← [Zurück zur Startseite](Home)

> URL: `/lage/{lage_id}/meldungen/uebergreifend` (via Großschadenslage-Board)  
> Zugänglich für: `incident_leader`, `org_admin`, `system_admin`

**Übergreifende Meldungen** (auch Cross-Marker genannt) sind lageweite Meldungen, die nicht an eine einzelne Einsatzstelle gebunden sind. Typische Anwendungsfälle: Straßensperren, Sammelplätze, Gefahrenbereiche, allgemeine Lagehinweise.

---

## Übersicht

Übergreifende Meldungen erscheinen:
- Als eigene Spalte **„Übergreifend"** im GSL-Board (über das Phasen-Dropdown auswählbar)
- Mit einer **Mini-OSM-Karte** in der Karten-Kachel, wenn Koordinaten hinterlegt sind
- Als Marker auf der **Großschadenslage-Lagekarte**

---

## Meldung anlegen

1. Im GSL-Board das Phasen-Dropdown auf **„Übergreifend"** umschalten.
2. **+ Neue Meldung** klicken.
3. Formular ausfüllen:

| Feld | Beschreibung |
|------|-------------|
| **Titel** | Kurze Beschreibung der Meldung |
| **Status** | Meldung / Achtung / Hinweis / Information |
| **Notizen** | Freitext, Details |
| **Koordinaten (Karte)** | Optional: Ort auf der Mini-Karte festlegen |
| **Adresse** | Wird aus den Koordinaten per Reverse Geocoding vorgeschlagen |

4. **Speichern** — die Meldung erscheint sofort in der Spalte (WebSocket-Update).

---

## Status-Workflow

| Status | Icon | Farbe | Bedeutung |
|--------|------|-------|-----------|
| **Meldung** | 🔴 | Rot | Allgemeine Einsatzmeldung |
| **Achtung** | ⚠️ | Orange | Warnung, Handlungsbedarf |
| **Hinweis** | 💡 | Gelb | Informeller Hinweis |
| **Information** | ℹ️ | Blau | Sachliche Information, kein Handlungsbedarf |

Status kann nachträglich über das Bearbeiten-Modal geändert werden.

---

## Medien (Fotos)

An jede übergreifende Meldung können **Fotos** angehängt werden:
- Kamera-Upload direkt aus dem Browser (Mobilgerät)
- Galerie-Upload (Dateiauswahl)

Der Foto-Zähler (📷 N) wird auf der Kachel angezeigt. Fotos öffnen sich in einer Lightbox.

---

## Karte

Ist ein Standort (Koordinaten) hinterlegt, erscheint in der Kachel eine **Mini-OSM-Karte** mit dem Marker.

Auf der **Großschadenslage-Lagekarte** werden übergreifende Meldungen als eigene Marker-Kategorie dargestellt (unterschiedliche Farbe/Form je Status).

---

## Drucken

Über das Kontext-Menü der Meldung → **Drucken** → Druckansicht mit Titel, Status, Notizen, Koordinaten und Medien-Vorschau.

---

## Erledigte Meldungen

Eine Meldung kann als **erledigt** markiert werden:
- Kachel wird abgedunkelt und mit einem Haken versehen
- Bleibt in der Spalte sichtbar (für die Dokumentation)
- Im Bericht auflisten als abgeschlossene Maßnahmen
