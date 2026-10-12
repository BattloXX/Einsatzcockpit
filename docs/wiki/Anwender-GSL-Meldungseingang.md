# GSL-Meldungseingang

← [Zurück zur Großschadenslage](Anwender-Grosschadenslage)

> URL: `/lage/{lage_id}/meldungen`
> Lesen: `readonly` und höher; Portal erzeugen: `incident_leader`, `admin`, `org_admin`; Meldungen bearbeiten: zusätzlich `recorder`

Der Meldungseingang sammelt Bürgermeldungen für eine konkrete Großschadenslage. Neue Meldungen können als Einsatzstelle oder als übergreifende Lageinfo übernommen werden.

---

## Bürgerportal und QR-Code

Im Meldungseingang erzeugen berechtigte Benutzer den Portal-Link. Danach stehen **📋 Kopieren** zum Weitergeben und **QR** zum Anzeigen des QR-Codes bereit. Der Link gehört zu dieser Lage und bleibt ohne Ablauf gültig, bis ein neuer Link erzeugt oder die Lage beendet wird.

Das öffentliche Formular **Schaden / Gefahr melden** enthält folgende Eingaben:

| Feld | Pflicht | Zweck |
|---|---|---|
| **Beschreibung der Situation** | ja | Was gesehen wurde oder passiert ist |
| **Straße / Adresse** und **Ort** | nein | Ortsangabe in Textform |
| **📍 Standort ermitteln** | nein | Kartenposition nach Freigabe im Browser |
| **Ihr Name** | nein | Rückfragekontakt |
| **Telefonnummer** | ja | Bestätigung und Rückfragen |
| **Foto** | nein | Foto auswählen oder direkt aufnehmen |

Bei akuter Lebensgefahr verweist das Formular sichtbar auf den Notruf **122**. Nach **✈ Meldung absenden** bestätigt die meldende Person den vierstelligen Code unter **Telefonnummer bestätigen**. Schlägt der SMS-Versand fehl, führt **Erneut versuchen** zurück zum Formular.

## Neue Meldungen prüfen

Der Meldungseingang zeigt neue Meldungen sowie die Zahl der offenen Einsatzstellen. Öffnen Sie zuerst Beschreibung, Adresse, Karte und Foto und entscheiden Sie anschließend über die Übernahme.

1. Meldung auf Plausibilität und Zuständigkeit prüfen.
2. Bei einer einzelnen Einsatzstelle **annehmen** wählen.
3. Bei einer lageweiten Information **als Lageinfo** wählen.
4. Nicht verwertbare oder doppelte Meldungen mit **ablehnen** abschließen.

| Aktion | Ergebnis |
|---|---|
| **annehmen** | Legt eine Einsatzstelle aus Beschreibung, Adresse, Koordinaten und Foto an; die Quelle ist Bürger |
| **als Lageinfo** | Erstellt eine [übergreifende Meldung](Anwender-Uebergreifende-Meldungen) mit Status **Unbestätigt** |
| **ablehnen** | Kennzeichnet die Bürgermeldung als abgelehnt |

Beim Annehmen werden vorhandene Koordinaten übernommen und eine Abschnittszuordnung kann anhand dieser Koordinaten erfolgen. Meldungen mit Koordinaten lassen sich auf der [Lagekarte der Großschadenslage](Anwender-Grosschadenslage-Karte) einblenden.

## Datenschutz und Rückfragen

Name und Telefonnummer dienen der Einsatzführung und Rückfrage. Zeigen oder dokumentieren Sie diese Angaben nur dort, wo sie für die Bearbeitung erforderlich sind. Das Foto einer angenommenen Meldung wird zur Einsatzstelle übernommen; prüfen Sie es vor einer weiteren Weitergabe auf schutzwürdige Inhalte.

## Mobil arbeiten

Das öffentliche Formular ist für Smartphone-Kamera und Standortfreigabe ausgelegt. Standort und Kamera werden erst nach der jeweiligen Aktion im Browser abgefragt. In der Führungsansicht sind die Schaltflächen zum Annehmen, Ablehnen und Übernehmen direkt an der Meldung verfügbar.

## Abgrenzung Mail-Eingang

Der Mail-Eingang ist ein organisationsweiter Eingang für reguläre aktive Einsätze, nicht eine direkte GSL-Funktion. Freigegebene beziehungsweise authentifizierte E-Mails können abhängig von der Konfiguration automatisch oder manuell als Einsatzmeldung übernommen werden; Betreff, Inhalt und verfügbare Anhänge werden dabei zur Meldung. Übungseinsätze sind von dieser Übernahme ausgeschlossen.

## Siehe auch

- [Großschadenslage](Anwender-Grosschadenslage)
- [Übergreifende Meldungen](Anwender-Uebergreifende-Meldungen)
- [GSL-Stab, Journal und Lagebericht](Anwender-GSL-Stab-und-Journal)
- [GSL-Ressourcenverwaltung](Anwender-GSL-Ressourcenverwaltung)
