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

Abschnitte können auch direkt auf der **Lagekarte** als Polygon eingezeichnet werden — ohne Seitenneuladen. Siehe [Lagekarte der Großschadenslage](Anwender-Grosschadenslage-Karte).

---

## Einsatzstellen anlegen

**Manuell:** + Einsatzstelle-Button im Board  
**Via API:** `POST /api/v1/lage/alarm` mit Alarmierungsdaten  
**Via Karte (Pin-Modus):** Kartenklick auf der Lagekarte → Einsatzgrund eingeben (inkl. automatischer Adressermittlung)  
**Bürgermeldung:** Eingehende Meldungen unter `/lage/{id}/meldungen` akzeptieren

---

## Lagekarte

Die Lagekarte (`/lage/{id}/karte`) zeigt alle Einsatzstellen und Abschnitt-Polygone auf einer interaktiven Karte. Vollständige Dokumentation: [Lagekarte der Großschadenslage](Anwender-Grosschadenslage-Karte).

---

## Stab (SKKM-konform)

Der Stab (`/lage/{id}/stab`) bietet drei Tabs:

### Tab 1 – Einsatzjournal

Das Einsatzjournal ist BMI SKKM-konform aufgebaut und dient zur lückenlosen Dokumentation aller Führungsentscheide:

| Kategorie | Farbe | Bedeutung |
|-----------|-------|-----------|
| **Entscheidung** | Lila | Führungsentscheid, Lagebewertung |
| **Anweisung** | Orange | Auftrag an Abschnitt oder Einheit |
| **Meldung** | Blau | Lagemeldung, Statusänderung |
| **Sonstiges** | Grau | Sonstige Vermerke |

**Eintrag erstellen:**
1. Kategorie aus dem Dropdown wählen
2. Text eingeben
3. **Eintragen** klicken → erscheint sofort mit Zeitstempel und Autor

Einträge können mit ✕ gelöscht werden (Bestätigung erforderlich). Alle Einträge werden live via WebSocket aktualisiert, wenn mehrere Geräte gleichzeitig am Stab arbeiten.

### Tab 2 – Besetzungstafel

SKKM-konforme Stabsfunktionen (EL, S1–S6 etc.) mit aktueller Besetzung, Ampel-Anzeige und Ablöse-Protokoll.

### Tab 3 – Personenjournal

Chronologische Tabelle aller Besetzungseinträge mit Zeitstrahl je Stabsfunktion.

---

## Dashboard

Das Dashboard (`/lage/{id}/dashboard`) bietet eine Echtzeit-Übersicht über:
- Einsatzstellen nach Phase und Priorität
- Aktive Ressourcen-Zuordnungen
- Aktivitäts-Feed (Lageeinträge + Stellen-Protokolle)
- **Mini-Karte** mit allen Einsatzstellen und Abschnitt-Polygonen

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
| Einsatzstelle via Karten-Pin anlegen | `recorder`, `incident_leader`, `admin`, `org_admin` |
| Lage starten / beenden | `incident_leader`, `admin`, `org_admin` |
| Abschnitte verwalten / zeichnen | `incident_leader`, `admin`, `org_admin`, `recorder` |
| Stab-Journal schreiben | `recorder`, `incident_leader`, `admin`, `org_admin` |
| Stab-Journal löschen | `incident_leader`, `admin`, `org_admin` |
