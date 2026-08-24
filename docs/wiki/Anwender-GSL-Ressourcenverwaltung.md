# GSL-Ressourcenverwaltung (Einheiten)

← [Zurück zur Startseite](Home)

> URL: `/lage/{lage_id}/einheiten`  
> Zugänglich für: `incident_leader`, `org_admin`, `system_admin`

Die **GSL-Ressourcenverwaltung** ermöglicht die strukturierte Erfassung und Disposition von Einheiten (Fahrzeuge, Trupps, Fremdorganisationen) im Rahmen einer Großschadenslage.

---

## Einheiten anlegen

**Neue Einheit** im Ressourcen-Tab:

| Feld | Beschreibung |
|------|-------------|
| **Bezeichnung** | Kurzname der Einheit (z.B. „RLF-Wolfurt", „GW-Gefahrgut Bregenz") |
| **Typ** | Feuerwehr / Rettungsdienst / Polizei / Technisch / Sonstige |
| **Organisation** | Eigene Org oder **Fremdorganisation** (z.B. FF Lauterach, ÖAMTC Notarzt) |
| **TETRA-Rufname** | Funkrufname |
| **Bemerkungen** | Freitext |

Einheiten können per **Drag & Drop** sortiert werden.

---

## Fremdorganisations-Einheiten

Auch Einheiten von anderen Organisationen (nicht im System registriert) können erfasst werden:
- Feld „Organisation": Freitext-Eingabe des Org-Namens
- Diese Einheiten sind für die Dokumentation verfügbar, aber ohne Systemzugang

---

## Disposition an Einsatzstellen

Eine Einheit kann einer oder mehreren Einsatzstellen zugewiesen werden (**Mehrfach-Disposition**):

**Methode 1 — aus der Einheiten-Liste:**
1. Einheit auswählen → **Zuweisen**
2. Einsatzstelle(n) aus dem Dropdown wählen
3. Bestätigen

**Methode 2 — aus der Einsatzstellen-Karte (Board):**
1. Einsatzstelle öffnen → Detail-Panel
2. Abschnitt „Ressourcen" → **Einheit hinzufügen**
3. Einheit aus der Dropdown-Liste wählen

**Methode 3 — aus dem Detail-Panel der Einheit:**
- Einheit anklicken → Einsatzstellen direkt zuweisen

---

## Ressourcen-Journal

Jede Disposition und Abzug wird im **Ressourcen-Journal** protokolliert:
- Zeitstempel, Einheit, Einsatzstelle, Art (Zugewiesen/Abgezogen)
- Aufrufbar über die Einheiten-Detailansicht

---

## Mehrfach-Disposition

Eine Einheit kann **gleichzeitig an mehrere Einsatzstellen** disponiert sein:
- Alle aktiven Einsatzstellen werden auf der Einheiten-Kachel als Badges angezeigt
- Die Einsatzstellen-Karte zeigt die Einheit nur einmal, aber mit entsprechender Markierung

---

## Anzeige auf der Lagekarte

Einheiten mit hinterlegten Koordinaten (z.B. GPS-Position) erscheinen als Marker auf der Lagekarte.

Im **Taktischen Modus** (ÖBFV E-27) werden Einheiten mit ihrem normierten Typ-Symbol und der entsprechenden Magnetfarbe dargestellt.

→ Siehe [Taktische Lagekarte](Anwender-Taktische-Lagekarte)

---

## Einsatz-Leiter der Einsatzstelle

Jede Einsatzstelle kann einen **Einsatz-Leiter** (EL) hinterlegen:
- Entweder aus dem Mitgliederregister der Org
- Oder Freitext-Name (für Fremdorg-Einsatzleiter)

Der EL erscheint auf der Board-Karte und im Ausdruck.
