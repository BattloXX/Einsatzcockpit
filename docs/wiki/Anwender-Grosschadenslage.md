# Großschadenslage

Das Großschadenslage-Modul ermöglicht die strukturierte Führung von Großeinsätzen mit mehreren gleichzeitigen Einsatzstellen (Massenanfall, Unwetter, Industrieunfall o. ä.).

---

## Übersicht

Eine **Großschadenslage (Lage)** fasst mehrere **Einsatzstellen** in einem gemeinsamen Führungsrahmen zusammen. Jede Einsatzstelle durchläuft Phasen von der Erstalarmierung bis zur Erledigung und kann einem **Abschnitt** zugeordnet werden.

Auf der Startseite (`/`) erscheint eine aktive Lage als eigene Karte mit Gesamtanzahl der Einsatzstellen. Einzelne Einsätze, die zur Lage gehören, werden dort nicht separat aufgelistet.

---

## Lage starten

1. `/lage` aufrufen
2. **🚨 Neue Lage starten** (Einsatzleiter / Admin)
3. Name und optionale Beschreibung eingeben
4. Übungs-Flag setzen wenn gewünscht

Eine Lage kann auch **automatisch** gestartet werden, wenn ein Alarmstichwort in den Systemeinstellungen als Großschadensauslöser konfiguriert ist.

---

## Einsatzstellen-Karte

Jede Einsatzstelle wird als Karte im Phasen-Kanban dargestellt:

| Feld | Beschreibung |
|------|-------------|
| **Einsatzmeldung** (groß) | Inhalt des Feldes „Einsatzgrund" — das eigentliche Schadensbild |
| **Adresse** | Straße, Hausnummer, Ort |
| **Abschnitt** | Farbiger Badge wenn der Einsatzstelle ein Abschnitt zugeordnet ist |
| **Priorität** | Farbiges Badge (Sofort / Dringend / Normal / Aufschiebbar) |
| **Fahrzeuge** | 🚒 Anzahl aktiv zugeteilter Fahrzeuge |
| **Quelle** | `API`-Badge (Alarmierungssystem) oder `Bürger`-Badge (Bürgermeldung) |

---

## Phasen

| Phase | Bedeutung |
|-------|-----------|
| **Eingegangen** | Meldung eingelangt, noch nicht erkundet |
| **Erkundung** | Erkundungstrupp unterwegs |
| **Bewertet** | Lage bekannt, Entscheidung steht aus |
| **Disponiert** | Einsatzmittel zugewiesen |
| **In Arbeit** | Aktive Bekämpfung / Maßnahmen laufen |
| **Erledigt** | Abgeschlossen |

Phasen werden per **Drag & Drop** gewechselt oder über den Detail-Dialog.

---

## Prioritäten

| Priorität | Farbe | Bedeutung |
|-----------|-------|-----------|
| **Sofort** | Rot | Gefahr für Leib und Leben |
| **Dringend** | Orange | Orts-/Dammschutz, drohende Ausweitung |
| **Normal** | Gelb | Kritische Infrastruktur / Umwelt |
| **Aufschiebbar** | Grau | Reine Sachwerte |

Die Priorität kann im Detail-Dialog manuell geändert werden.

---

## ✨ KI-Auto-Priorisierung

Wenn der KI-Assistent aktiviert ist (`ai_enabled = true`), wird beim Anlegen einer Einsatzstelle automatisch eine Priorität vorgeschlagen:

- **Manuell angelegt**: KI analysiert die Einsatzmeldung sofort beim Speichern
- **Via API** (Alarmierungssystem): Analyse läuft als Hintergrund-Task nach dem Anlegen
- **Aus Bürgermeldung übernommen**: Analyse läuft beim Akzeptieren der Meldung

Die KI bewertet `danger_score` (Gefahrenlage 1–4) und `urgency_score` (Dringlichkeit 1–4) und leitet daraus die `prio_vorschlag` ab. Der Einsatzleiter kann die Priorität jederzeit überschreiben.

---

## Abschnitte

Abschnitte (`/lage/{id}/sektoren`) strukturieren die Einsatzstellen geografisch oder taktisch. Jeder Abschnitt hat einen Namen, eine Farbe und optional einen Abschnittsleiter. Die Abschnittsfarbe erscheint als farbiger Badge auf der Einsatzstellenkarte.

---

## Einsatzstellen anlegen

**Manuell:** + Einsatzstelle-Button im Board  
**Via API:** `POST /api/v1/lage/alarm` mit Alarmierungsdaten  
**Bürgermeldung:** Eingehende Meldungen unter `/lage/{id}/meldungen` akzeptieren

---

## Lage beenden

1. Button **Lage beenden** im Board-Header
2. Bestätigung erforderlich
3. Status wechselt auf `closed`; das Board ist danach schreibgeschützt

---

## Berechtigungen

| Aktion | Rolle |
|--------|-------|
| Lage ansehen | `readonly` und höher |
| Einsatzstelle anlegen/bearbeiten | `recorder`, `incident_leader`, `admin`, `org_admin` |
| Lage starten / beenden | `incident_leader`, `admin`, `org_admin` |
| Abschnitte verwalten | `incident_leader`, `admin`, `org_admin` |
