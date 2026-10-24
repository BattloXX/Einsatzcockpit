# ECP – Phase 4 / Auftrag 4.2: Vereinfachter Meldungsverlauf

Stand: 2026-10-24. Erweiterung von [Rückmeldungen 4.1](gsl-einheitenansicht-rueckmeldungen-2026-10-24.md). Keine neuen Tabellen, Migrationen oder Nachrichten-Schreibwege.

## Bedienung

Im Auftrag zeigt **Verlauf der Einsatzstelle** die berechtigten Meldungen und Fotos mit Datum/Uhrzeit, Absender, Inhalt und Übermittlungsstand. Ältere Ereignisse stehen oben, neuere unten. **Ältere Meldungen laden** ergänzt den Verlauf; Aktualisieren und Nachladen ändern weder Auftrag noch Seite automatisch.

Unter **Meldungen** steht die zentrale Übersicht **Wichtige Meldungen der eigenen Einheit**, neueste zuerst. Sie zeigt eigene Meldungen, Fotos, neue/geänderte/zurückgezogene Aufträge und berechtigte Mitteilungen der Einsatzleitung zu den eigenen Einsatzstellen. Normale Statusmeldungen stehen kompakt im Einsatzstellenverlauf. Fehlgeschlagene lokale Statusaktionen bleiben auch zentral sichtbar.

- Neuer Auftrag, Änderung und Rückzug erhalten Klartext und Hervorhebung.
- Rückfragen werden bei einer Führungsmitteilung mit Fragezeichen oder dem Anfang „Rückfrage“ gekennzeichnet. Es gibt im Bestand keinen gesonderten Rückfragenzustand; die Kennzeichnung behauptet keine ausstehende Antwort.
- Gefahren und Unterstützung verwenden die vorhandenen Schnellmeldungsüberschriften und Hervorhebungen aus 4.1.
- Eigene Meldungen zeigen **Übermittelt**, **Noch nicht übermittelt · nur auf diesem Gerät**, Netzrückstand oder einen verständlichen Fehler. Vorläufige Zustände werden nicht als Serverbestätigung dargestellt.
- Fotos erscheinen direkt im Verlauf; noch nicht versandte Fotos erhalten eine lokale Vorschau aus der bisherigen Outbox. Foto-Dateinamen und zusätzliche „Foto hochgeladen“-Systemzeilen entfallen im Verlauf.
- Technische IDs, unbekannte System-/Debug-Typen und automatische Abzugs-Doppelmeldungen erscheinen nicht im normalen Verlauf. Übermittlungsfehler enthalten keine rohen HTTP-/Servercodes. **Übermittlungen** behält die vorhandenen Aktionen zum Wiederholen, Kopieren und bewussten Verwerfen.

## Daten und eindeutiger Bezug

`einheit_verlauf_service.py` projiziert bestehende `EinheitSiteDispatch`, `SiteLogEntry`, `SiteMedia` und `EinheitAktion`. Die zusätzliche lesende API `/einheit/api/meldungen` verwendet denselben geprüften Einheitenkontext. `dispatch_id` grenzt auf die Einsatzstelle eines eigenen Auftrags ein; ohne diese Auswahl entsteht die zentrale Übersicht. Die Seite umfasst standardmäßig 30 Einträge und lässt sich über einen stabilen Cursor nachladen. Zeit, Quellrang und Quell-ID unterscheiden auch identische Zeitpunkte. Erfassungszeit geht bei offline aufgenommenen Meldungen/Fotos der Empfangszeit vor; die vorhandene Zeitkorrektur der Schreib-API bleibt maßgeblich.

Der genaue Auftrag einer Meldung oder eines Fotos wird aus der bestehenden erfolgreichen Aktion ermittelt, nicht aus dem gerade geöffneten Auftrag. Eigene Statuszeilen können über den identischen Erfassungszeitpunkt einer eindeutigen Statusaktion zugeordnet werden. Bei Mehrdeutigkeit bleibt der Eintrag eine **Meldung zur Einsatzstelle**. Dieselbe Stelle darf mehrere eigene Aufträge haben. **Auftrag öffnen** führt zum nachgewiesenen Auftrag, **Einsatzstelle öffnen** öffnet einen verfügbaren eigenen Auftrag dieser Stelle, ohne dem historischen Eintrag eine Herkunft zuzuschreiben.

Bei neuen Notizen/Maßnahmen/Fotos wird die Objekt-ID vor dem Eintragen der bestehenden Aktionsreferenz geflusht. Das behebt fehlende Referenzen ohne Migration. Alte Fotoaktionen, deren ID nur in `antwort_json` steht, verwenden diese vorhandene Antwort als Nachweis. Alteinträge ohne belastbare Verbindung bleiben stellenbezogen.

Zuweisung, letzter Änderungsstand und Rückzug stammen aus den vorhandenen Zeitfeldern der eigenen Disposition. Frühere Auftragsfassungen und vollständige Änderungsschnappschüsse sind darin nicht gespeichert und werden nicht nachträglich erfunden. Die allgemeine technische Boardchronik bleibt im Bestand erhalten.

## Rechte

Alle Quellen sind auf eigene Dispositionen, die aktuelle Lage und Organisation begrenzt. Meldungen anderer Einheiten werden in dieser Übersicht nicht übernommen. Nicht zugewiesene Stellen und fremde Auftrags-IDs bleiben verborgen.

Die bisherigen GK-/QR-Grenzen bleiben erhalten: eigene Meldungen/Fotos und eigene Auftragsereignisse; allgemeine Führungsnotizen an einer Einsatzstelle werden für diese Zugänge weiterhin nicht freigegeben. Geräte-/berechtigte Benutzerkontexte sehen zusätzlich die bislang berechtigten allgemeinen Führungsmitteilungen. Ein Empfänger wird nicht aus einem Namen oder Freitext geraten. Für individuelle Rückfragen an GK/QR fehlt im Bestand eine eindeutige Empfängerfreigabe; diese Änderung führt keine neue Berechtigung ein.

Zurückgezogene Aufträge behalten ihre eigenen Ereignisse und Meldungen. Allgemeine Führungsinhalte und nicht mehr freigegebene Serverfotos werden dafür nicht zusätzlich zugänglich. Die vorhandene Medienprüfung gilt unverändert.

## Offline und Live-Aktualisierung

`einheit_verlauf.js` vereinigt die Serverprojektion mit vorhandenen offenen Outbox-Einträgen und bestätigten Receipts. UUID und vorhandene Entity-Referenz vermeiden Doppelanzeigen beim Übergang vom lokalen zum bestätigten Eintrag. Bestätigte Meldungs-/Foto-/Status-Receipts behalten dafür Erfassungszeit, Einheit und ursprüngliche Nutzdaten im bisherigen Verlaufsspeicher; Binärbilder werden nicht kopiert. Alte reine Empfangsquittungen ohne nachweisbaren Nachrichtenbezug werden nicht als zweite historische Nachricht erzeugt.

Der letzte geladene Verlauf wird im bestehenden Outbox-Metaspeicher je Lage/Einheit und Ansicht abgelegt. Es gelten die vorhandenen getrennten Outbox-Namensräume der Zugangsprofile. Auch der gespeicherte Gesamtzustand wird vor einer Offline-Übernahme gegen Lage und Einheit des aktuellen Seitenkontexts geprüft; ein alter Zustand eines zuvor anders zugewiesenen Geräts wird nicht übernommen. Bereits nachgeladene ältere Einträge bleiben bei einem Live-Refresh erhalten. Der Offline-Cache speichert bis zu 200 Einträge; bei größerem Verlauf wird eine vollständige aktuelle erste Seite samt passendem Cursor gesichert. Noch nie geladene Seiten werden offline nicht vorgetäuscht. Scheitert allein das lokale Zwischenspeichern, bleibt der erfolgreiche Serverabruf sichtbar und erhält einen Speicherhinweis; er wird nicht als Netzausfall dargestellt.

Ein erfolgreicher Aktualisierungsabruf, Polling und die vorhandenen Board-/Einheitenereignisse aktualisieren die geöffnete Ansicht. 401/403/404 verwenden keinen Offline-Fallback; verweigerte Inhalte werden aus Ansicht und Cache entfernt. Nach Zugangssperre darf eine verspätete Antwort den Verlauf nicht wieder füllen. Lokale Bild-URLs werden nach Versand beziehungsweise Zugangssperre freigegeben.

## Regression

- API: Chronologie und stabile Cursor bei identischen Zeiten und neuen Einträgen; falsche Cursor, fremde Aufträge/Lagen, GK-/QR-/Gerätesicht, Rückzug, Offline-Zeit und nachgewiesener Auftragsbezug einschließlich alter Fotoantworten.
- Browser: 360/768/1.024 px, sichtbare Hervorhebungen und Absender, Bilder, kompakte Status, Nachladen und Live-Aktualisierung ohne Dubletten, Offline-Neustart mit lokalem Bild und spätere Serverbestätigung, verständliche Fehler und gesperrter Cache.
- JavaScript: Server-/Outbox-/Receipt-Abgleich, fremde lokale Einheiten, gleicher Standort bei mehreren Aufträgen, Mikrosekunden und numerische Gleichstände, Nachladen/Refresh sowie verspätete Antworten nach Sperre.
- MariaDB: echte Keyset-Abfragen und JSON-Zuordnung einer historischen Fotoantwort. Bestehende Status-/Board-/Outbox- und Rechteprüfungen bleiben Regression.

Die neuen Browserfälle sind im vorhandenen Playwright-CI-Job eingetragen. Die automatisierte Darstellung verwendet Chromium; eine physische Geräteabnahme bleibt separat.

Validierung am 2026-10-24: **374 Fälle im breiten Python-/Browser-Regressionslauf** erfolgreich. Nach der ergänzten Kontextprüfung zusätzlich **52 Browserfälle** (Verlauf, Übersicht und Rückmeldungen) sowie **13 Verlaufs-API-Fälle** erfolgreich. **113 JavaScript-Tests**, **4 echte MariaDB-Tests**, Ruff, mypy, JavaScript-Syntaxprüfung und `git diff --check` ebenfalls erfolgreich. Die Läufe verwenden getrennte SQLite-Testdateien über den vorhandenen Worker-Namensraum. Die vorhandenen Migrationen wurden nur in einer eigenen MariaDB-Testinstanz angewandt; der temporäre Container wurde entfernt.
