# GSL-Einheitenansicht

← [Zurück zur Ressourcenverwaltung](Anwender-GSL-Ressourcenverwaltung)

> URL: `/einheit` · für Geräteprofil **Einheit**, persönlichen GK-Zugang oder QR-Zugang.

Die Einheitenansicht ist die Arbeitsansicht für genau eine disponierte Einheit. Sie zeigt deren Aufträge, Einsatzorte und Rückmeldungen; die Führungs- und Ressourcenverwaltung bleibt davon getrennt.

## Zugang und Geräte

| Zugang | Verwendung |
|---|---|
| Persönlicher GK-Link | Der Gruppenkommandant öffnet den Link aus SMS oder kopierter Nachricht. Ein SMS-Bestätigungscode kann in den [GSL-Einstellungen](Administration-GSL-Einstellungen) vorgeschrieben sein. |
| QR-Zugang | Der QR-Code vom Ausdruck öffnet die eigene Einheit; eine PIN kann erforderlich sein. |
| Geräteprofil **Einheit** | Ein angemeldetes Fahrzeug- oder Tabletgerät arbeitet im Einheitenmodus. |
| Gesamtansicht auf dem Einheiten-Tablet | Die Gesamtansicht ist nur lesend. |

GK-Link und QR-Zugang sehen und bearbeiten ausschließlich die eigene Einheit. Personal und Ausstattung darf ein GK nur pflegen, wenn dies in den GSL-Einstellungen erlaubt ist; der QR-Zugang darf es nie.

## Bereiche und Ansicht

Die Bereiche heißen **Übersicht**, **Karte** und **Aufträge**. Auf dem Smartphone werden sie über die Tab-Leiste gewechselt; auf breiteren Tabletansichten stehen Liste und Karte nebeneinander.

| Bereich | Inhalt |
|---|---|
| **Übersicht** | Aktueller und nächster Auftrag, Meldungen und die Daten der eigenen Einheit. |
| **Karte** | Aufträge der eigenen Einheit, Standortfunktion und Navigation. |
| **Aufträge** | Auftragsgruppen **Aktuell**, **Nächster**, **Weitere offene**, **Fertig** und **Zurückgezogen**. |

Neue Aufträge tragen **NEU**, geänderte **GEÄNDERT**. Im Detail weist „Auftrag wurde geändert – bitte prüfen“ auf einen geänderten Auftragstext hin. Bei einer neuen Reihenfolge erscheint ein Hinweisbanner; Priorität und von der Leitstelle vorgegebene Reihenfolge bleiben maßgeblich.

## Aufträge bearbeiten

Ein Auftrag ist entweder **Erkundung** oder **Abarbeitung**. Die verfügbaren Status folgen der Auftragsart und dem aktuellen Status.

| Auftragsart | Primäre Aktionen | Weitere Aktionen |
|---|---|---|
| Erkundung | **Erkundung abschließen** | **Bestätigt**, **Anfahrt**, **Vor Ort**, Lagemeldung, Foto und Navigation |
| Abarbeitung | **In Arbeit setzen**, anschließend **Fertig melden** | **Bestätigt**, **Anfahrt**, **Vor Ort**, Lagemeldung, Foto und Navigation |

Bei Bedarf kann ein Auftrag als **Nicht durchführbar** gemeldet werden; dafür ist ein Grund anzugeben. Zu jedem Auftrag stehen außerdem der Auftragstext der Einsatzleitung, Adresse, weitere Einheiten an der Stelle, bekannte Gefahren und Sperren am Ziel zur Verfügung.

## Lagemeldung, Fotos und Material

Über die Auftragsansicht können Lagemeldungen und Fotos übermittelt werden. Ist der Geräteverleih für die Lage aktiviert, lässt sich dort auch Material für die Einsatzstelle erfassen und zurückmelden; Materialfotos gehören zu diesem Vorgang.

## Karte und Navigation

| Kartenelement | Bedeutung |
|---|---|
| **● In Arbeit** | Laufender Auftrag der Einheit. |
| **▶ Nächster** | Vom System als nächster Auftrag geführter Auftrag. |
| Nummern | Weitere offene Aufträge in der angezeigten Reihenfolge. |
| Gestrichelte Verbindung / Kennzeichnung | Erkundungsauftrag. |

Mit **Fertige einblenden** werden abgeschlossene Aufträge auf der Karte ergänzt. **Mein Standort** fragt die Position erst nach einem Klick im Browser ab; sie wird weder gespeichert noch an den Server gesendet. **Zentrieren** richtet die Karte auf diesen Standort aus. **Navigation starten** öffnet die für den Auftrag hinterlegte Navigation beziehungsweise Google Maps oder die Zieladresse.

## Offline arbeiten

Statuswechsel, Meldungen, Fotos und Materialvorgänge werden zunächst in einer Offline-Outbox auf diesem Gerät gespeichert. Nicht bestätigte Einträge sind mit **„nur auf diesem Gerät“** gekennzeichnet. Eine Erfolgsmeldung bedeutet erst dann eine Übermittlung, wenn der Server den Vorgang bestätigt hat.

Unter **Übermittlungen** können Sie **Jetzt synchronisieren**, erneut senden, bei Fehlern oder Konflikten kopieren sowie Einträge verwerfen. Offline zeigt die Seite den zuletzt geladenen Stand.

## Simulation

Administratoren können eine Einheit simuliert öffnen. In einer Übung ist die Simulation schreibbar; in einer Echtlage bleibt sie nur lesend. Simulationen werden als solche im Journal/Audit kenntlich gemacht.
