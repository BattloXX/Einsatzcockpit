# GSL-Sektoren und Planung

← [Zurück zur Großschadenslage](Anwender-Grosschadenslage)

> Abschnitte: `/lage/{lage_id}/sektoren`
> Lesen: `readonly` und höher; verwalten: `recorder`, `incident_leader`, `admin`, `org_admin`

Abschnitte strukturieren Einsatzstellen geografisch oder taktisch. Die Sektorenansicht bündelt pro Abschnitt Einsatzstellen, Prioritäten, Phasen, aktive Einheiten und fällige Lagemeldungen. Die Planungsübersicht hilft, verfügbare und zugeteilte Kräfte nebeneinander zu beurteilen.

---

## Abschnitt anlegen und bearbeiten

Über **+ Neuer Abschnitt** legen Sie einen Abschnitt an.

| Feld | Bedeutung |
|---|---|
| **Bezeichnung** | Name, z. B. „Abschnitt Ost“ |
| **Abschnittsleiter (optional)** | Angezeigte Führung für den Abschnitt |
| **Farbe** | Kennfarbe für Karte, Karteikopf und Zuordnung |

1. Bezeichnung eingeben und bei Bedarf Abschnittsleiter ergänzen.
2. Eine Farbe wählen.
3. **Anlegen** klicken. Der Abschnitt erscheint unmittelbar in der Übersicht.

Mit **✏️ Bearbeiten** ändern Sie die Angaben. **✕** löscht den Abschnitt nach Bestätigung. Ordnen Sie Einsatzstellen vor dem Löschen bewusst um, damit die geografische Führung klar bleibt.

## Einsatzstellen im Abschnitt

Jede Abschnittskarte zeigt die zugehörigen Einsatzstellen. Eine Stellenkarte enthält die Phasenanzeige, Priorität, aktive Einheiten und gegebenenfalls den Hinweis auf eine überfällige Lagemeldung. **Auf dem Board zeigen** öffnet die Stelle unmittelbar im Board.

Die Zuordnung lässt sich im Stellen-Detail ändern. Sind Koordinaten und Abschnittspolygone vorhanden, kann die Anwendung die Zuordnung geografisch bestimmen; manuell zugewiesene Stellen bleiben davon ausgenommen. Zeichnen oder bearbeiten Sie Polygone auf der [Lagekarte der Großschadenslage](Anwender-Grosschadenslage-Karte).

## Phasenübersicht

Der Kartenkopf fasst die Stellen als **neu**, **in Arbeit** und **erledigt** zusammen. Die detaillierten Board-Phasen bleiben sichtbar, etwa **Eingegangen**, **Erkundung**, **Bewertet**, **Disponiert**, **In Arbeit**, **Fertig gemeldet** und **Abgeschlossen**. Die Sektorenansicht ist damit eine Führungsübersicht; Detailaktionen erfolgen im Stellen-Detail.

## Planungsübersicht

Die Planungsübersicht gruppiert Einsatzstellen und die dazu disponierten Einheiten. Über die Phasen-Chips oben legen Sie fest, welche Einsatzstellen einbezogen werden.

| Anzeige | Bedeutung |
|---|---|
| **🚒** | Einheit **Vor Ort** an einer Einsatzstelle |
| **↗** | Alarmierte bzw. disponierte Einheit |
| **Pool / Reserve** | Nicht zugewiesene Einheit |
| Konflikt | Einheit oder Disposition benötigt Aufmerksamkeit |

1. Relevante Phasen über die Chips auswählen.
2. Einsatzstellen mit ihren Vor-Ort- und disponierten Einheiten vergleichen.
3. Pool/Reserve prüfen und Disposition im Board oder in der Ressourcenverwaltung anpassen.
4. Konflikte zuerst klären, bevor weitere Kräfte eingeplant werden.

Die Ansicht disponiert keine Einheit selbst. Sie ist die Entscheidungsgrundlage; das Zuweisen und der Statuswechsel erfolgen im Stellen-Detail bzw. in der [GSL-Ressourcenverwaltung](Anwender-GSL-Ressourcenverwaltung).

## Mobil arbeiten

Die Abschnittskarten sind als große Schaltflächen bedienbar. Öffnen Sie eine Einsatzstelle direkt aus der Karte; die zugehörige Detailansicht zeigt die verfügbaren Aktionen. Für das Zeichnen von Polygonen eignet sich wegen der präziseren Eckpunkte in der Regel ein größerer Bildschirm.

## Siehe auch

- [Großschadenslage](Anwender-Grosschadenslage)
- [Lagekarte der Großschadenslage](Anwender-Grosschadenslage-Karte)
- [GSL-Stab, Journal und Lagebericht](Anwender-GSL-Stab-und-Journal)
- [GSL-Ressourcenverwaltung](Anwender-GSL-Ressourcenverwaltung)
