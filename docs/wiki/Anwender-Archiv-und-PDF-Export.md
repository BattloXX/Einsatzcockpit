# Archiv und PDF-Export

← [Zurück zur Startseite](Home.md)

## Einsatz abschließen

Wenn der Einsatz beendet ist:

1. Im Einsatz-Board: **Einsatz abschließen** (oben rechts)
2. Bestätigungs-Dialog erscheint (4-stelliger Code wird angezeigt, zur Vermeidung von Fehlbedienungen)
3. Code eingeben → **Abschließen bestätigen**

**Was passiert:**
- Status wird auf `closed` gesetzt
- `closed_at` Zeitstempel wird gesetzt
- Alle QR-Tokens werden invalidiert
- Einsatz ist ab sofort **read-only** (außer Admin)
- Einsatz erscheint im Archiv

## Archiv aufrufen

Menü → **Archiv** (oder `/archiv`)

## Archiv filtern

| Filter | Möglichkeiten |
|--------|--------------|
| Datum | Von/bis Datum |
| Stichwort | T1–T7, F1–F4 |
| Übungen | Einschließen / Ausschließen / Nur Übungen |
| Suche | Freitext in Adresse und Meldung |

## Einsatzdetails anzeigen

Auf Einsatz in der Liste klicken → vollständige Detail-Ansicht mit:
- Kopfdaten (Alarm, Adresse, Meldung, Einsatzleiter)
- Alle Spalten und Fahrzeuge (wie im Board, aber read-only)
- Alle Aufträge und Meldungen
- Alle erfassten Personen
- Einsatz-Verlaufsprotokoll

## PDF-Bericht herunterladen

Im Einsatz-Archiv: **PDF herunterladen** → Browser lädt die Datei herunter.

### Inhalt des PDF-Berichts

1. **Deckblatt**: Einsatznummer, Datum, Stichwort, Adresse, Einsatzleiter
2. **Fahrzeuge**: Alle Fahrzeuge sortiert nach Spalten
3. **Aufträge**: Alle Aufträge mit Status (erledigt/storniert/offen)
4. **Meldungen**: Alle zeitgesteuerten Meldungen
5. **Gerettete Personen**: Alle erfassten Personen
6. **Verlaufsprotokoll**: Chronologische Aufzeichnung aller Aktionen
7. **Atemschutz-Protokoll**: Alle Trupps mit Zeiten, Drücken und Warnungen

Übungseinsätze sind mit einem diagonalen **ÜBUNG**-Banner auf jeder Seite gekennzeichnet.

### PDF-Layout

- Format: A4 Hochkant
- Farbig (Status-Ampel-Farben bleiben erhalten)
- Druckoptimiert

## Website-Entwurf erstellen

Ist für die Organisation die WordPress-Anbindung eingerichtet (siehe
[WordPress-Berichte](Administration-WordPress-Berichte.md)), erscheint bei einem
abgeschlossenen Einsatz (ab Rolle **Bearbeiter**) der Button **„Website-Entwurf
erstellen"**. Damit wird — falls nicht ohnehin schon automatisch beim Abschließen
geschehen — ein Beitragsentwurf im Wehr-Blog angelegt, den ein Redakteur dort nur noch
mit Bild und Text ergänzen und veröffentlichen muss. Ein erneuter Klick legt **keinen**
zweiten Entwurf an, sondern öffnet nur den Link zum bestehenden.

## Einsatz erneut öffnen (Admin)

Falls ein abgeschlossener Einsatz nachbearbeitet werden muss:

**Admin** → **Archiv** → Einsatz anklicken → **Einsatz wieder öffnen** (nur Admin)

## Zeitreise (Admin)

Für jeden abgeschlossenen Einsatz kann der genaue Zustand zu jedem Zeitpunkt rekonstruiert werden:

**Admin** → **Einsatz-Historie** → Zeitpunkt auswählen → Board-Zustand wird angezeigt

Details: [Audit-Log und Zeitreise](Administration-Audit-Log-und-Zeitreise.md)
