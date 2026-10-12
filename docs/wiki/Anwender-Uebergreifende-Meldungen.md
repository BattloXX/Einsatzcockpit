# Übergreifende Meldungen

← [Zurück zur Startseite](Home)

> Bedienung: Phasen-Chip **📍 Übergreifend** im Großschadenslage-Board
> Lesen: `readonly` und höher; bearbeiten: `recorder`, `incident_leader`, `admin`, `org_admin`

**Übergreifende Meldungen** (auch Cross-Marker genannt) sind lageweite Meldungen, die nicht an eine einzelne Einsatzstelle gebunden sind. Typische Anwendungsfälle: Straßensperren, Sammelplätze, Gefahrenbereiche, allgemeine Lagehinweise.

---

## Übersicht

Übergreifende Meldungen erscheinen:
- Als eigene Spalte **„Übergreifend"** im GSL-Board (mobil über den Phasen-Chip **📍 Übergreifend**)
- Mit einer **Mini-OSM-Karte** in der Karten-Kachel, wenn Koordinaten hinterlegt sind
- Als Marker auf der **Großschadenslage-Lagekarte**

---

## Meldung anlegen

1. Im GSL-Board den Phasen-Chip **📍 Übergreifend** wählen.
2. **+** öffnen und die Meldung erfassen.
3. Formular ausfüllen:

| Feld | Beschreibung |
|------|-------------|
| **Titel** | Kurze Beschreibung der Meldung |
| **Typ** | z. B. Überflutete Straße, Murenabgang, Straßensperre oder Sonstige Lageinfo |
| **Beschreibung** | Freitext, Details |
| **Koordinaten (Karte)** | Optional: Ort auf der Mini-Karte festlegen |
| **Straße / Nr. / Ort** | Ortsangabe zur Meldung |

4. **Speichern** — die Meldung erscheint sofort in der Spalte (WebSocket-Update).

---

## Status-Workflow

| Status | Icon | Farbe | Bedeutung |
|--------|------|-------|-----------|
| **Unbestätigt** | ● | Grau | Noch nicht verifiziert |
| **Aktiv / Gefahr** | ● | Rot | Akute Lage oder Gefahr |
| **In Bearbeitung** | ● | Orange | Bearbeitung läuft |
| **An Dritte / Beobachtung** | ● | Blau | Weitergegeben oder beobachten |
| **Behoben / Aufgehoben** | ● | Grün | Keine weitere Maßnahme offen |

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

Der Status **Behoben / Aufgehoben** schließt die Meldung fachlich ab. Auf der Lagekarte kann er über
**Beh. ausbl.** ausgeblendet werden; die Meldung bleibt für Dokumentation und Druck erhalten.
