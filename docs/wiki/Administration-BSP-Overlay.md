# BSP-Overlay (Administration)

← [Zurück zur Startseite](Home)

Einrichtung und Pflege georeferenzierter Planausschnitte: Ein Ausschnitt aus einem Lage- oder Brandschutzplan wird lagegenau als Ebene in der Einsatzkarte angezeigt. Anwender-Doku: [BSP-Overlay](Anwender-BSP-Overlay).

Das Overlay nutzt die bestehende Georeferenzierung des Plans (Referenzpunkte). Die Referenzpunkte werden nicht kopiert: Ein Overlay verweist auf die gespeicherte Georeferenz.

## Rechte

Overlays anlegen, ändern, freigeben und zurückziehen darf nur die Rolle **Objektverwalter** (sowie Org-Admin und Admin). Alle Änderungen werden im Änderungsprotokoll des Objekts festgehalten. Leserollen sehen freigegebene Overlays in den Einsatzansichten.

## Voraussetzung

Die Planseite muss **georeferenziert** sein (gültige gespeicherte Georeferenz) und das Dokument die **aktuelle, freigegebene Version** sein. Fehlt das, zeigt der Bereich eine Hinweiskarte „Zuerst Plan georeferenzieren" mit Verweis auf den Georeferenz-Editor weiter oben.

## Overlay erstellen

Der Bereich **Plan auf der Karte anzeigen (BSP-Overlay)** steht im Georeferenz-Editor einer Planseite (Dokumentenseite → Georeferenz). Er führt in drei Schritten:

### 1 Ausschnitt wählen

- **Ganze Seite:** der gesamte Plan.
- **Rechteck:** im Plan oben ein Rechteck aufziehen (Maus oder Finger).
- **Polygon:** Punkte im Plan nacheinander setzen und mit **Polygon abschließen** schließen. **Letzten Punkt entfernen** und **Neu beginnen** korrigieren, fertige Eckpunkte lassen sich ziehen. Erlaubt sind 3 bis 100 Punkte; sich selbst überschneidende Polygone werden abgelehnt und der Grund wird angezeigt.

Der Ausschnitt gilt auf der **gedrehten** Seite, so wie du sie im Editor siehst. Außerhalb des Ausschnitts bleibt die Karte sichtbar.

### 2 Angaben

- **Name** (Pflicht) und **Planart** (Lageplan, Hallenbereich, Gefahrenzone, Geschoss, Sonstiger Plan), optional Geschoss und Bereich. Der Name erscheint bei der Planauswahl im Einsatz.
- **Anzeige im Einsatz:** „Standardmäßig einblenden" und Deckkraft. Einsatzkräfte können das jederzeit ausblenden.
- **Als empfohlenen Plan markieren:** höchstens ein Overlay je Objekt.
- **Druck-Einstellungen** (eingeklappt): „Bei Alarm mitdrucken" und Druck-Deckkraft.

### 3 Prüfen und freigeben

1. **Speichern und Vorschau** speichert den Entwurf und erzeugt das Raster. Die Vorschau erscheint auf der Karte oben, die Vorschau-Deckkraft ändert nur diese Ansicht.
2. Prüfe, ob Plan und Umgebung richtig übereinanderliegen. Die **Lagegenauigkeit** steht in Klartext (z. B. „gut – Abweichung 0,4 m, 5 Referenzpunkte"); die technischen Werte sind eingeklappt.
3. **Freigeben:** Der Dialog fasst Plan, Ausschnitt, Anzeige und Genauigkeit zusammen. Warnungen (z. B. nur drei Referenzpunkte, nur frei gesetzte Punkte) musst du **einzeln bestätigen**.

Ungespeicherte Änderungen sind mit „Nicht gespeichert" gekennzeichnet; freigegeben werden kann nur ein gespeicherter Entwurf. Eine Änderung an einem freigegebenen Overlay hebt die Freigabe auf.

## Status

| Status | Bedeutung |
|--------|-----------|
| **Entwurf** | noch nicht freigegeben, im Einsatz nicht sichtbar |
| **Freigegeben** | im Einsatz verfügbar |
| **Veraltet** | freigegeben, aber Plan oder Georeferenz wurden geändert: wird **nicht mehr angezeigt oder gedruckt**, bis neu geprüft und freigegeben |
| **Zurückgezogen** | bewusst entfernt, nicht mehr angezeigt |

Ersetzt du ein Dokument durch eine neue Version, wird ein bestehendes Overlay **nicht** automatisch übertragen: Lege es für die neue Version neu an und gib es frei.

## Anzeige auf der Objektkarte

Auch auf der Objektkarte (Tab und Editor) lässt sich das Overlay ein- und ausschalten. Das Bedienfeld liegt einklappbar unter der Karte und verdeckt weder Palette noch Zeichenwerkzeuge. Gibt es Entwürfe oder veraltete Freigaben, weist ein Hinweis darauf hin.

## Automatischer Ausdruck bei Alarm

Unter **Druckregeln** kann eine Regel das Objekt-Element **BSP-Ausschnitt (freigegeben)** enthalten. Gedruckt wird nur, wenn

- die Regel das Element ausdrücklich enthält,
- das Overlay **Bei Alarm mitdrucken** gesetzt hat,
- es freigegeben und aktuell ist und das Objekt dem Einsatz bestätigt zugeordnet ist.

Jede Freigabe druckt einmal; Wiederholungen derselben Freigabe werden nicht doppelt ausgelöst, eine neue Freigabe druckt bewusst neu. Ein automatischer Ausdruck der **Karte** mit Plan bei Alarm existiert nicht, die Kartenmodi sind manuell im Druckdialog der Lageführung (→ [Anwender-Doku](Anwender-BSP-Overlay#drucken)).

## KI-Anbindung (MCP)

Das MCP-Tool `objekt_bsp_overlay_entwurf` kann Overlays nur als **Entwurf** anlegen oder ändern; die Lage berechnet der Server aus der gespeicherten Georeferenz. Freigeben, Zurückziehen oder Löschen ist per MCP nicht möglich: Die Freigabe erfolgt immer durch einen Menschen im Editor. Details: [MCP-Server](Administration-MCP-Server).

Siehe auch: [Objektverwaltung](Administration-Objektverwaltung), [Objekte](Anwender-Objekte).
