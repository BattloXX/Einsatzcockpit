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

## Material an der Einsatzstelle belassen

Material, das an einer Einsatzstelle bleibt, wird als Ausleihe dokumentiert und erscheint in der Verleih-Übersicht mit dem Chip **Vor Ort**. Es kann sowohl im Stellen-Detail auf dem GSL-Board als auch in der Einheitenansicht auf dem Tablet erfasst werden. Über den Filter **Vor Ort belassen** sehen Sie ausschließlich diese Einträge. Dort sind Einsatzstelle, hinterlassende Einheit, Quelle und erfassende Person nachvollziehbar; der Druckschein enthält Einsatzstelle mit Adresse sowie die Einheit.

Die Rücknahme erfolgt wie bei jeder anderen Ausleihe. Erinnerungen und optionale SMS an den Empfänger funktionieren ebenfalls, sofern Telefonnummer und SMS-Gateway vorhanden sind.

---

## Erinnerungen

Das System sendet automatisch **SMS-Erinnerungen** an offene Ausleihen (wenn SMS-Gateway aktiv und Empfänger-Telefonnummer hinterlegt).

Beim Anlegen kann außerdem **PIN generieren + per SMS senden** gewählt werden. Ohne manuelle PIN erzeugt das System dafür eine sechsstellige PIN. Den Text der Ausleih- und Erinnerungs-SMS verwaltet die Organisation in den [GSL-Einstellungen](Administration-GSL-Einstellungen).

---

## Druckschein

Über **Drucken** auf der Ausleihe-Detailseite wird der **Verleihschein** im Druckdialog geöffnet:
- Ausleihedaten, Empfänger, Einzelpositionen
- Journal-Einträge (Ausgabe / Rücknahme)
- QR-Code für schnellen mobilen Aufruf

---

## Hinweise

- Der Geräteverleih ist an eine aktive **Großschadenslage** gebunden (nicht für Einzeleinsätze verfügbar).
- Stammdaten (Artikel, Stücklisten) werden vom Org-Admin in **Verwaltung → Geräteverleih** gepflegt.
- Das Modul muss in **Einstellungen → GSL-Einstellungen** aktiviert sein.
