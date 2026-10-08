# Straßensperren & Anfahrtsrouting

Das Modul sammelt geplante und aktuelle Einschränkungen für die eigene Wehr und freigegebene Nachbarwehren. Bei aktiven Einsätzen kann es die Anfahrt vom hinterlegten Fahrzeug-Startpunkt prüfen. Die Routeninformation ist eine Unterstützung; sie ersetzt keine Lagebeurteilung.

## Übersicht und Status

Unter `/strassensperren` lassen sich Sperren nach Status, Zeitraum (heute oder sieben Tage), eigener/geteilter Herkunft, Text und Einschränkungstyp filtern. Die Karten- und Listenfarben bedeuten:

| Farbe | Bedeutung |
|-------|-----------|
| Rot | Vollsperre |
| Orange | Teilsperre, Einbahnregelung oder Gewichts-, Höhen- bzw. Breitenbeschränkung |
| Gelb | andere Einschränkung |
| Grau | geplant, abgelaufen oder deaktiviert |

Der Status wird aus Zeitraum und Deaktivierung bestimmt: **geplant**, **aktiv**, **abgelaufen** oder **deaktiviert**. Die Statusansicht unter `/strassensperren/status` zeigt aktuelle und geplante Sperren samt Karte. Für einen Monitor gibt es dieselbe Ansicht über einen Alarm-Infoscreen-Link.

## Sperre anlegen und pflegen

Objektverwalter legen eine Sperre über **+ Sperre anlegen** an. Erfasse zumindest Straße oder eine Geometrie, optional Straße, Abschnitt *von/bis*, Fahrtrichtung, Zeitraum in der Ortszeit der Organisation, Einschränkungstyp, Priorität, Beschreibung, Quelle und Maße für Gewicht, Höhe, Breite oder Länge.

### Adressprüfung

Straße sowie *Von* und *Bis* bieten Vorschläge aus Photon. Während der Eingabe prüft Einsatzcockpit die
Straße und Kreuzungen gegen OpenStreetMap. Abweichende OSM-Schreibweisen können direkt übernommen werden;
bei einer nicht gefundenen Straße zeigen Vorschläge mögliche Alternativen. Ist OpenStreetMap nicht erreichbar,
kann die Sperre weiterhin gespeichert werden und die Prüfung später wiederholt werden.

Auf der Karte kann die Lage als Punkt, Linie oder Fläche gezeichnet werden. Selbst gezeichnete Geometrie gilt als geprüft (Qualität **manuell**).

### Abschnitt automatisch ermitteln & Qualität

**Abschnitt aus Adresse ermitteln** sucht die Straße im OpenStreetMap-Straßennetz der Gemeinde (Feld **Ort**, sonst der Ort der Organisation) und schneidet den Abschnitt zu. *Von* und *Bis* dürfen eine Querstraße („Kreuzung Kellaweg“, „Einmündung L 190“, „ab Schulstraße“) oder eine Hausnummer („Nr. 12“, „12a“) sein. Bleiben beide leer, wird die ganze Straße samt Abzweigungen übernommen.

| Qualität | Bedeutung | Umfahrung |
|----------|-----------|-----------|
| **hoch** | Straße eindeutig gefunden, beide Enden eindeutig – oder eine ganze, zusammenhängende Straße bis 1,5 km | sofort berücksichtigt |
| **mittel** | plausibel, aber z. B. nur ein Ende gefunden, Kreuzung mehrdeutig oder lange ganze Straße | erst nach Bestätigung |
| **niedrig** | Näherung ohne OSM-Netz (nur über Hausnummern) oder unsicherer Straßenname | erst nach Bestätigung |

Mehrdeutigkeiten (z. B. eine gleichnamige Straße in der Nachbargemeinde) werden unter der Karte angezeigt. Nicht eindeutige Sperren tragen das Kennzeichen **„Geometrie prüfen“**: Karte kontrollieren, bei Bedarf korrigieren und mit der Checkbox **„Geometrie geprüft“** speichern oder auf der Detailseite **„Geometrie bestätigen“** drücken. Erst dann wird eine solche Vollsperre bei der Umfahrung verwendet. Die Übersicht zählt die Sperren mit ungeprüfter Geometrie und filtert sie über **„Geometrie prüfen“**.

Ist der OSM-Dienst gerade überlastet, wird – wenn möglich – eine Näherung über Hausnummern erstellt; sonst die Ermittlung später erneut auslösen.

Eine nicht mehr geltende Sperre wird mit Grund **deaktiviert**, nicht gelöscht; sie kann wieder reaktiviert werden. Eigene Sperren können an als Partner hinterlegte Nachbarwehren freigegeben werden. Diese sehen die Freigabe, dürfen sie aber nicht ändern.

### Verlängerungen und neue Verordnungen

Beim Anlegen erkennt Einsatzcockpit wahrscheinliche Verlängerungen oder Änderungen desselben Straßenabschnitts. Das Formular bietet dann an, die bestehende Sperre mit dem vorgeschlagenen Ende zu bearbeiten, die neue Verordnung als Ersatz anzulegen oder die neue Sperre bewusst zusätzlich anzulegen. Ein Ersatz deaktiviert die ältere Sperre und verlinkt beide Einträge nachvollziehbar miteinander.

### Zusatzangaben aus der Verordnung

Neben Straße und Zeitraum können **Ort**, **Aktenzeichen**, **Behörde** und **Ausnahmen** (z. B. „Anrainer frei“) erfasst werden. Der Ort hilft bei der Suche der Straße, wenn die Sperre in einer Nachbargemeinde liegt.

### Verordnung als PDF

Im Detail einer Sperre kann die offizielle Verordnung als PDF hochgeladen und von berechtigten Partnerorganisationen geöffnet werden. Der MCP-Workflow lautet: PDF → `strassensperre_entwurf_aus_pdf` → Felder prüfen → `strassensperre_anlegen` → `strassensperre_dokument_uebergeben`.

Der MCP-Assistent kennt alle erlaubten Werte über `strassensperren_kataloge`. Einschränkungstypen akzeptieren in der Oberfläche und im Service auch gebräuchliche Aliase, etwa `vollsperre` für `closed`, `teilsperre` für `partial` oder `hoehe` für `height_limit`.

## Im Einsatz

Wenn Routing aktiviert und ein Startpunkt konfiguriert ist, prüft das System aktive Einsätze im Hintergrund. Bei einer relevanten Sperre erscheint in Einsatzinfo und Alarm-Infoscreen die Warnbox **„ANFAHRT BEEINTRÄCHTIGT“** mit betroffener Sperre, möglicher Umfahrung und Mehrweg. Ohne relevante Sperre ändert sich die Einsatzansicht nicht.

Die Anzeige aktualisiert sich live. Mit **„Route neu berechnen“** kann die gespeicherte Prüfung erneut angestoßen werden. Die reguläre Alarmierung und Einsatzanlage bleiben davon unabhängig.
