# Geräteverleih

← [Zurück zur Startseite](Home)

> URL: `/lage/{lage_id}/verleih`  
> Zugänglich für: `incident_leader`, `recorder`, `org_admin`, `system_admin`  
> Modul muss in **GSL-Einstellungen** aktiviert sein (pro Org durch den Org-Admin)

Das **Geräteverleih-Modul** ermöglicht die strukturierte Ausgabe und Rücknahme von Material im Rahmen einer Großschadenslage. Typische Anwendung: Ausgabe von Taschenlampen, Schutzkleidung, Rettungsmitteln oder Geräten an Einsatzstellen oder Einsatzkräfte.

---

## Grundkonzept

- **Artikel** = einzelne Gerätetype mit Bestand (z.B. „Atemschutzgerät", „Handleuchte", „Notfallrucksack")
- **Stückliste** = vordefiniertes Paket aus mehreren Artikeln (z.B. „Standard-Ausrüstung Trupp")
- **Ausleihe** = konkrete Ausgabe: Empfänger (Einsatzstelle/Einheit/Person), Artikel/Stückliste, Menge, Zeitstempel

---

## Ausleihe anlegen

**URL:** `/lage/{lage_id}/verleih/neu`

1. **Empfänger** auswählen:
   - Einsatzstelle aus der aktuellen Lage (Dropdown)
   - Oder: Freitext (Einheit, Person)
2. **Artikel** oder **Stückliste** wählen.
3. Menge eingeben.
4. Optional: **PIN senden** — SMS-Benachrichtigung an Empfänger (wenn SMS-Gateway aktiv).
5. **Ausgeben** → Ausleihe ist sofort im Journal erfasst.

### Schnellerfassung via Scan

Artikel können per **Barcode/QR-Code-Scan** ausgewählt werden — Kamera des Mobilgeräts direkt im Browser nutzbar (kein App-Download nötig).

---

## Ausleihe-Übersicht

**URL:** `/lage/{lage_id}/verleih`

Liste aller aktiven Ausleihen mit:

| Spalte | Beschreibung |
|--------|-------------|
| **Empfänger** | Einsatzstelle oder Einheit |
| **Material** | Artikel / Stückliste mit Einzelpositionen |
| **Menge** | Ausgegeben / Zurückgegeben |
| **Status** | Offen / Teilweise zurück / Vollständig zurück |
| **Zeitstempel** | Ausgabezeitpunkt |

---

## Rücknahme

Auf der Ausleihe-Kachel oder Detailseite:
- **Vollständig zurück** — alle Positionen auf einmal
- **Teilweise zurück** — einzelne Positionen und Mengen

Jede Rücknahme wird im **Verleih-Journal** protokolliert.

---

## Foto-Dokumentation

An jede Ausleihe können **Fotos** angehängt werden (z.B. Übergabeprotokoll, Zustandsdokumentation). Kamera- und Galerie-Upload direkt im Browser.

---

## Erinnerungen

Das System sendet automatisch **SMS-Erinnerungen** an offene Ausleihen (wenn SMS-Gateway aktiv und Empfänger-Telefonnummer hinterlegt).

---

## Druckschein

Über **Drucken** auf der Ausleihe-Detailseite wird ein **Übergabeschein** generiert:
- Ausleihedaten, Empfänger, Einzelpositionen
- Journal-Einträge (Ausgabe / Rücknahme)
- QR-Code für schnellen mobilen Aufruf

---

## Hinweise

- Der Geräteverleih ist an eine aktive **Großschadenslage** gebunden (nicht für Einzeleinsätze verfügbar).
- Stammdaten (Artikel, Stücklisten) werden vom Org-Admin in **Verwaltung → Geräteverleih** gepflegt.
- Das Modul muss in **Einstellungen → GSL-Einstellungen** aktiviert sein.
