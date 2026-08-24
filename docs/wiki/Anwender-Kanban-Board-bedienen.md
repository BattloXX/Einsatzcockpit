# Kanban-Board bedienen

← [Zurück zur Startseite](Home)

## Aufbau des Boards

Das Board besteht aus **fixen Spalten** (immer vorhanden) und optionalen **Abschnittsspalten**.

### Fixe Spalten

| Spalte | Bedeutung |
|--------|-----------|
| **Alarmiert** | Fahrzeuge, die alarmiert wurden aber noch nicht am Einsatzort |
| **Aktiv** | Fahrzeuge im Einsatz |
| **Aufträge** | Aufgaben ohne zugewiesenes Fahrzeug |
| **Meldungen** | Zeitgesteuerte Meldungen (5-Min, 10-Min, eigene) |
| **Nachbarwehren** | Fahrzeuge von Nachbardepartements |
| **Gerettete Personen** | Erfasste Personen |

### Abschnittsspalten

Für größere Einsätze können eigene Abschnitte angelegt werden:  
Sidebar → **+ Abschnitt hinzufügen** → Name eingeben

## Fahrzeuge verschieben (Drag & Drop)

Fahrzeug-Karte anfassen und in eine andere Spalte ziehen. Die Änderung wird sofort auf alle verbundenen Geräte übertragen.

**Auf Touch-Geräten:** Karte lange drücken → verschieben.

## Status-Ampel

Jede Fahrzeug-Karte zeigt einen farbigen Punkt:

| Farbe | Bedeutung |
|-------|-----------|
| 🟢 Grün | Kein offener Auftrag |
| 🟡 Gelb | 1 offener Auftrag |
| 🔴 Rot | 2 oder mehr offene Aufträge |

## Fahrzeug-Karte

Auf eine Karte klicken öffnet die Detailansicht:
- **Kommandant** zuweisen (aus Mannschaftsregister)
- **Aufträge** für dieses Fahrzeug anlegen/ansehen
- **Fahrzeug entfernen** (aus Einsatz nehmen)

## Aufträge anlegen

Sidebar → **+ Auftrag** (oder `Strg + A`)

- **Titel**: Kurze Aufgabenbeschreibung
- **Detail**: Optionaler Freitext
- **Fahrzeug**: Optional einem Fahrzeug zuweisen

Oder direkt in einer Fahrzeug-Karte: **+ Auftrag**

## Aufträge abhaken

Auftrag anklicken → **✓ Erledigt** → der Auftrag wird als erledigt markiert und vom Fahrzeug entfernt.

## Meldungen

Zeitgesteuerte Meldungen erscheinen als Pop-up, wenn die Zeit abgelaufen ist. Sie können:
- **Erledigt** markiert werden (Pop-up verschwindet)
- **Verschoben** werden (neue Zeit eingeben)
- **Storniert** werden

Neue Meldung: Sidebar → **+ Meldung** (oder `Strg + M`)

## Lage-Ticker

Am unteren Rand der Sidebar rotieren **Lage-Hinweise** — kurze Erinnerungen an wichtige Maßnahmen je nach Stichwort.

## Timer

In der Kopfzeile läuft ein **Einsatz-Timer** seit dem Alarmdatum. Bei 5 und 10 Minuten erscheint ein Pop-up.
