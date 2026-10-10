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


---

## Ressourcenkarte

Ein Klick auf eine Ressource öffnet die **Ressourcenkarte** (Seitenleiste) mit den Tabs Übersicht, Einsätze, Journal, Personal, Ausstattung und Zugang.

- **Übersicht**: Stammdaten, Gruppenkommandant (GK) mit Telefonnummer, Stellvertreter, Zugangsstatus. Hier lassen sich auch Verbände bilden (mehrere Einheiten zusammenfassen), auflösen und eine Einheit aufteilen. Die Teile eines Verbands erscheinen in der Kräfteübersicht unter dem Verband und werden nicht doppelt gezählt; sie sind nicht einzeln disponierbar.
- **Einsätze** und **Journal**: Aufträge, Statuswechsel, Lagemeldungen und manuelle Einträge (Korrekturen per Storno, nie Löschen).
- **Personal**: Stärke als Summe (Führer/Unterführer/Mannschaft) oder als Namensliste mit Verstärken, Ablösen und Umbuchen. Eine Person kann nur einer Einheit gleichzeitig zugeordnet sein.
- **Ausstattung**: Geräte und Material mit Menge und Status, optional aus der Vorlage des Fahrzeugs; Umbuchen zwischen Einheiten.

### Zugang für den Gruppenkommandanten

Im Tab **Zugang** erzeugt die GSL einen persönlichen Link, mit dem der GK den Einheitenmodus (`/einheit`) auf dem eigenen Smartphone öffnet.

1. Gruppenkommandant mit Mobilnummer hinterlegen.
2. **SMS senden** oder **Nachricht kopieren** / **Link kopieren** (zum Einfügen in Messenger). Der Link wird nie dauerhaft angezeigt.
3. Pro Einheit ist genau ein Zugang gültig. Jeder neue Link entwertet den vorherigen, die laufende Sitzung des GK wird beendet (die Oberfläche fragt vorher nach).
4. **Widerrufen** sperrt sofort. Der Zugang wird außerdem automatisch gesperrt, wenn der GK gewechselt oder entfernt wird, sich die Nummer ändert, die Einheit abrückt, die Lage geschlossen wird oder die Notbremse in den GSL-Einstellungen betätigt wird.
5. Ist in den GSL-Einstellungen „Automatisch senden“ aktiv, geht der Link beim Zuweisen eines GK per SMS raus.

Der GK sieht nur seine eigene Einheit und kann Status melden, Lagemeldungen und Fotos senden, Anforderungen stellen und – wenn die Organisation es erlaubt – Personal und Ausstattung seiner Einheit pflegen. Änderungen funktionieren auch offline und werden nachgesendet.
