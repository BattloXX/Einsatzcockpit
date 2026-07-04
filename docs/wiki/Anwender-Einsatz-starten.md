# Einsatz starten

← [Zurück zur Startseite](Home)

## Automatisch über das Alarmierungssystem

Das Alarmierungssystem sendet bei jedem Alarm automatisch eine Anfrage an die API. Der Einsatz wird sofort angelegt und auf allen verbundenen Geräten als Toast-Benachrichtigung angezeigt.

**Was passiert automatisch:**
1. Einsatz mit Adresse, Stichwort und Meldungstext wird angelegt
2. Fahrzeuge der Erstausrückung (oder Vollalarm je nach Stufe) werden eingetragen
3. Standard-Auftragsvorschläge für das Stichwort werden vorgeblendet
4. 5-Minuten- und 10-Minuten-Warnmeldungen werden geplant
5. Alle verbundenen Geräte erhalten eine Toast-Benachrichtigung
6. Push-Benachrichtigung an alle abonnierten Geräte

## Automatisch über die Leitstelle (LIS/IPR)

Ist die LIS-Anbindung für deine Organisation eingerichtet (siehe [LIS/IPR-Anbindung](Administration-LIS-Anbindung)), gleicht Einsatzcockpit im Hintergrund laufend mit dem Leitstellensystem ab:

- Neue Einsätze **und Übungseinsätze** aus LIS werden automatisch angelegt bzw. mit einem bereits über die API angelegten Einsatz verknüpft (kein doppelter Einsatz)
- Die **Leitstellen-Nummer** wird als führende Kennung angezeigt (Alarm-Kopfzeile, Archiv, Verlauf, PDF-Export) — statt der internen Einsatz-ID
- **Anrufer/Melder** (Name + Telefonnummer) werden angezeigt, sofern von der Leitstelle mitgeliefert; die Telefonnummer ist direkt anklickbar (Klick-zum-Anrufen)
- Fahrzeugstatus (S1–S6) und -position, Meldungen sowie Zu-/Absagen der Mannschaft werden laufend übernommen
- Wird der Einsatz in LIS abgeschlossen, schließt Einsatzcockpit den Einsatz automatisch mit (Archivierung, kein manuelles Abschließen nötig)

## Manuell starten

Auf der Startseite: **Neuer Einsatz** (oder `Strg + B`)

Formular ausfüllen:
- **Stichwort** (T1–T7, F1–F4): bestimmt Erstausrückung und Auftragsvorschläge
- **Adresse**: Straße, Hausnummer, Ort
- **Meldung**: Freitext aus dem Alarmierungssystem
- **Als Übung markieren**: Übungseinsätze haben einen gelb/schwarzen Banner und zählen nicht in der Statistik

## Erstausrückung vs. Vollalarm

| Stichwort | Erstausrückung | Vollalarm |
|-----------|---------------|-----------|
| T1, T2 | ✓ Erstausrückung | – |
| T3, T4, T5 | – | ✓ Vollalarm |
| F1, F2 | ✓ Erstausrückung | – |
| F3, F4 | – | ✓ Vollalarm |

Bei Vollalarm werden zusätzlich Fahrzeuge der Nachbarwehren vorgeblendet.

## Einsatz übernehmen

Wenn das Alarmierungssystem den Einsatz angelegt hat und du ihn als Einsatzleiter übernimmst:
- Im Einsatz-Board: **Einsatzleiter** → deinen Namen auswählen
- Der Name erscheint dann in der Kopfzeile und im PDF-Bericht

## Nachbarn alarmieren

Bei Stufen T3+/F3+ werden Nachbarwehren automatisch im Kanban-Board in der Spalte „Nachbarwehren" vorbereitet. Ob sie wirklich alarmiert werden, entscheidet der Einsatzleiter.

## Einsatz abschließen

Im Einsatz-Board oben rechts: **Einsatz abschließen** → Bestätigung → Einsatz wird archiviert und ist nur noch lesbar.  
Details: [Archiv und PDF-Export](Anwender-Archiv-und-PDF-Export)
