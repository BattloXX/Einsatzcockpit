# GSL-Stab, Journal und Lagebericht

← [Zurück zur Großschadenslage](Anwender-Grosschadenslage)

> Stab: `/lage/{lage_id}/stab` · Funkjournal: `/lage/{lage_id}/funkjournal`
> Lesen: `readonly` und höher; Einträge und Besetzungen: `recorder`, `incident_leader`, `admin`, `org_admin`

Der Bereich **Stab** dokumentiert Führung, Besetzung und Entscheidungen einer Großschadenslage. Die Angaben bleiben der Lage zugeordnet und ergänzen die Protokolle der einzelnen Einsatzstellen.

---

## Einsatzjournal

Im Tab **Einsatzjournal** erfassen Sie Führungsentscheidungen, Anweisungen und Meldungen. Wählen Sie zuerst die Kategorie, geben Sie den Betreff ein und klicken Sie auf **Eintragen**. Bei Bedarf ergänzen Sie **Von**, **An**, **Veranlassung/Folgemaßnahmen** und Bilder.

| Funktion | Bedienung |
|---|---|
| Kategorie | Gliedert den Journaleintrag nach der in der Lage eingerichteten Kategorie |
| **Suchen (Einheit, Inhalt)…** | Filtert die angezeigten Einträge |
| ↗ | Öffnet den vollständigen Eintrag |
| 🖨 | Druckt einen einzelnen Journaleintrag über Stationsdrucker oder dieses Gerät |
| **🖨 Journal drucken** | Öffnet den Gesamtbericht beim Abschnitt Lage-Journal |

Einträge lassen sich nur mit entsprechender Führungsberechtigung löschen. Verwenden Sie Löschen daher nur für versehentliche oder doppelte Einträge; fachliche Korrekturen gehören als neuer, nachvollziehbarer Eintrag ins Journal.

## Besetzungstafel

Der Tab **Besetzungstafel** zeigt die Stabsfunktionen und deren aktuelle Besetzung. Über **+ Besetzen** oder **Jetzt besetzen** wird eine Person/Rufname eingetragen.

1. Stabsfunktion auswählen und **+ Besetzen** anklicken.
2. **Person / Rufname** eintragen; der Zeitpunkt kann leer bleiben und wird dann auf jetzt gesetzt.
3. Bei Bedarf **Leiter** und eine **Notiz** setzen, dann **Besetzen** wählen.
4. Bei Übergabe **↕ Ablösen** verwenden und ablösende Person, Zeitpunkt und Übergabe-Hinweis erfassen.
5. Mit **✕ Beenden** endet eine laufende Besetzung.

Die Tafel unterscheidet Leitung und Mitarbeitende und macht unbesetzte Funktionen sichtbar. Sie dient damit als aktuelle Führungsübersicht, nicht als Ersatz für die Personalverwaltung.

## Personenjournal

Der Tab **Personenjournal** führt alle Besetzungen chronologisch. Die Tabelle enthält **Funktion**, **Person**, **Von**, **Bis**, **Dauer** und **Abgelöst von**. Der Zeitstrahl darunter zeigt die Besetzung je Funktion; auch bereits beendete oder abgelöste Einträge bleiben dort dokumentiert.

## Funkjournal

Das Funkjournal erfasst Funkverkehr lageweit. Es kann einer Einsatzstelle zugeordnet werden, muss es aber nicht. So dokumentieren Sie auch Kommunikation an Abschnitte oder externe Stellen.

| Feld | Auswahl bzw. Zweck |
|---|---|
| **Richtung** | **↓ Ein**, **↑ Aus** oder **↔ Int** |
| **Art** | **Meldung**, **Auftrag** oder **Spruch** |
| **Kanal / Frequenz** | z. B. Kanal oder Funkgruppe |
| Gegenstelle | Bei Ein: Absender und Empfänger; bei Aus: Empfänger und Absender |
| **Einsatzstelle** | Optionaler Bezug zu einer Einsatzstelle |
| Nachrichteninhalt | Dokumentierter Funktext |

1. Richtung und Art auswählen.
2. Kanal, Gegenstelle und gegebenenfalls Einsatzstelle erfassen.
3. Text eingeben und den Eintrag speichern.
4. Mit **Suchen (Einheit, Kanal, Inhalt)…** gezielt filtern; **Nur offene** blendet erledigte Aufträge aus.

Einträge der Art **Auftrag** lassen sich in der Tabelle als erledigt oder wieder offen markieren. Über **Lagemeldung erfassen** kann eine Lagemeldung direkt für die gewählte Einsatzstelle angelegt werden.

## Lagebericht und Lagedokument

**📝 Lagebericht** öffnet das gemeinsame Lagedokument. Schreiben Sie den Lagebericht im Editor und klicken Sie auf **Speichern**. **🖨 Drucken** bietet die Druckausgabe; **✨ KI-Entwurf** beziehungsweise **✨ KI-Vorschlag anzeigen** erscheint, wenn die Funktion für die Organisation verfügbar ist. Die Anzeige von Verbindung und anwesenden Bearbeitenden hilft bei paralleler Bearbeitung.

Die Druckauswahl unter `/lage/{lage_id}/druck` filtert Einsatzstellen nach Priorität, Phase, Abschnitt und Straße sowie übergreifende Meldungen nach Status und Typ. Der **GSL-Gesamtbericht** enthält Stabsbesetzung, aktive und abgeschlossene Einsatzstellen, übergreifende Meldungen, Funkjournal und Lage-Journal. Der Druckdialog entscheidet dabei zwischen einem verbundenen **Stationsdrucker** und **dieses Gerät**.

## Mobil arbeiten

Auf kleinen Bildschirmen bleiben die Tabs und Aktionen erreichbar; erfassen Sie besonders lange Journaleinträge besser über den Volltext-Editor. Für Fotoanhänge stehen Kamera und Galerie des Geräts zur Verfügung. Prüfen Sie vor dem Verlassen der Seite, dass der Eintrag gespeichert wurde.

## Siehe auch

- [Großschadenslage](Anwender-Grosschadenslage)
- [GSL-Sektoren und Planung](Anwender-GSL-Sektoren-und-Planung)
- [GSL-Meldungseingang](Anwender-GSL-Meldungseingang)
- [GSL-Ressourcenverwaltung](Anwender-GSL-Ressourcenverwaltung)
