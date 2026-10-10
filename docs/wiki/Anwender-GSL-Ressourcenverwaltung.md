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


---

## Einheit schnell anlegen

Über **+ Einheit** lässt sich eine Einheit in einem Vorgang erfassen: Typ, Fahrzeug bzw. Organisation, Bezeichnung, Funkrufname, Status, Abschnitt, Bereitstellungsraum, Gruppenkommandant mit Mobilnummer und Besatzungsstärke. Unter „Weitere Angaben“ stehen Stellvertreter, Führer/Unterführer/Mannschaft, AGT, Sanitäter und eine Bemerkung. Ausstattung wird danach in der Ressourcenkarte gepflegt. Ist die Nummer des Gruppenkommandanten eingetragen und der Zugang aktiv, geht – je nach Einstellung – automatisch eine SMS mit dem persönlichen Link raus.

## QR-Zugang für Einheiten (Ausdruck)

Im Tab **Zugang** gibt es zusätzlich zum persönlichen SMS-Link einen **QR-Zugang**, der auf DIN A4 ausgedruckt und der Einheit übergeben wird.

- **Ausstellen / Anzeigen / Drucken**: „QR-Code ausstellen“, „QR anzeigen“ (nur auf Klick, wird nach 2 Minuten wieder ausgeblendet), „QR-Code drucken“ (Druckerauswahl). Erneutes Drucken ändert den Zugang nicht; „Neuen QR-Code“ macht den alten Ausdruck ungültig.
- **Gültigkeit und PIN**: Standardmäßig 72 Stunden (einstellbar). Optional verlangt der QR-Zugang eine PIN; sie steht **nicht** auf dem Blatt, sondern wird nur der Einsatzleitung angezeigt und separat übergeben.
- **Rechte**: Der QR-Zugang zeigt nur die eigene Einheit, erlaubt Status, Lagemeldungen und Fotos – keine Personal-/Ausstattungspflege.
- **Widerruf** erfolgt manuell, beim Wechsel oder Entfernen des Gruppenkommandanten, beim Abrücken der Einheit, beim Lageende und über die Notbremse.
- Auf dem Ausdruck steht: „Kräfteanforderungen und dringende Meldungen ausschließlich über Funk!“

### Automatischer Ausdruck bei Neuanlage

Unter **Gateway → Druckregeln** gibt es den Auslöser „GSL – Neue Einheit / QR-Einheitenzugang“ (Standard: keine Regel, also kein Autodruck). Nach dem Anlegen einer Einheit wird – bei aktiver Regel, passendem Echt-/Übungsfilter und aktiviertem QR-Zugang – genau ein A4-Auftrag erzeugt. Ein Druckfehler hat keine Auswirkung auf die Einheit; der Status steht in der Ressourcenkarte.

## Mobilnummer durch den Gruppenkommandanten

Fehlt dem Gruppenkommandanten die Nummer, erscheint in der Einheitenansicht ein nicht blockierender Hinweis. Der Gruppenkommandant gibt seine Mobilnummer ein und bestätigt sie mit einem SMS-Code. Erst danach wird sie gespeichert; die Führung sieht „Nummer bestätigt“. Bei einer Änderung wird der persönliche Zugang gesperrt und ein neuer an die bestätigte Nummer gesendet.

## Automatische Auftrags-SMS

Bei aktiviertem Schalter erhält der Gruppenkommandant eine SMS bei **neuem Auftrag**, **geändertem Auftragstext** und **Rückzug** – nicht bei Reihenfolgeänderung, Fotos oder Lagemeldungen. Die SMS enthält den Link zur Einheitenansicht und den Hinweis, dass dringende Meldungen und Kräfteanforderungen über Funk laufen. Eine SMS ist keine Lesebestätigung. Bei fehlgeschlagenem Versand gibt es in der Ressourcenkarte „Auftrags-SMS erneut senden“. Fahrzeug-Tablets mit Einheitenprofil erhalten zusätzlich eine Push-Benachrichtigung.

## Einheitenansicht auf dem Smartphone/Tablet

„Auftrag erhalten“ bestätigt den Auftrag. „Jetzt synchronisieren“ überträgt offline erfasste Meldungen; nicht übermittelbare Einträge lassen sich kopieren.
