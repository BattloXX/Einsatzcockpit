# ECP – Phase 3 / Auftrag 3.1: Mehrfachübernahme

Stand: 2026-10-24. Ausgangspunkt: `c2ef2454` (Phase 2, PR #718).

## Modell und fachliche Entscheidung

`EinheitSiteDispatch` enthält bereits `einheit_status`, `bestaetigt_at`, `vor_ort_at`, `beendet_at`, `withdrawn_at`, `version` und `reihenfolge`. `LageEinheit.incident_site_id` bezeichnet die primäre Einsatzstelle. Die vorhandene Statusmatrix reicht aus:

| Fachlicher Stand | Bestehende Werte |
|---|---|
| Zugewiesen | `zugewiesen` |
| Übernommen / bestätigt | `bestaetigt` und vorhandener Bestätigungszeitpunkt |
| Aktiv | `anfahrt`, `vor_ort`, `in_arbeit` (`EINHEIT_STATUS_AKTIV`) |
| Fertig gemeldet / Erkundet | `abgeschlossen` mit `beendet_at`, abhängig von `auftragsart` |

Es gibt **keine neuen Datenfelder oder Migrationen**. Mehrfachübernahme bedeutet mehrere einzelne Empfangsbestätigungen; keine Sammelaktivierung. Öffnen/Lesen, Bestätigen und Aktivieren sind getrennt. Führungssortierung, Priorität und Auftragsversion werden durch eine Bestätigung oder einen Wechsel nicht verändert. Erkundungen erhalten weiterhin keinen Zustand `in_arbeit`; bestehende Phasensperren bleiben wirksam.

## Bedienung und Vorschau

- Alle eigenen Zuweisungen bleiben vollständig lesbar. Die Hauptaktion führt von **Auftrag übernehmen** über **Anfahrt melden** und **Vor Ort melden** zu **In Arbeit setzen** beziehungsweise **Erkundung abschließen** und **Fertig melden**. Erlaubte direkte Sprünge bleiben unter **Weitere Status** erreichbar.
- Mehrere Bestätigungen verdrängen keinen laufenden Auftrag, auch bei zwei Dispositionen an derselben Einsatzstelle. Lokale Statusmeldungen werden von Serverbestätigungen unterschieden; erneute Statusaktionen sind während der ausstehenden Übermittlung gesperrt.
- Vor einem bekannten aktiven Wechsel werden bisherige und neue Einsatzstelle genannt. Die ausdrückliche Entscheidung **Unterbrechen und wechseln** setzt den bisherigen Auftrag auf `bestaetigt`. Ein neuer dringender Auftrag löst keinen automatischen Wechsel aus.
- Die Detailansicht zeigt vollen Auftragstext, Adresse/Karte, Gefahren, Sperren, Zufahrt, Objekthinweise einschließlich BMA/Merkmalen, Bilder, Pläne, berechtigte weitere Einheiten und Meldungsverlauf. Verspätete Detailantworten ersetzen keine neuere Auswahl. Polling und Outbox-Rückmeldungen aktualisieren auch geöffnete Details; offene Meldungseingaben bleiben erhalten.
- Objektinformationen sind auf bestätigte Verknüpfungen zum zugewiesenen Einsatz, eigene Organisation, aktives Objektmodul und produktive freigegebene beziehungsweise in Überarbeitung befindliche Objekte beschränkt. Pläne stammen nur aus aktuellen freigegebenen fertig verarbeiteten Dokumenten; Entwürfe und Arbeitskopien bleiben ausgeschlossen. Medien laufen über eine an die eigene Disposition gebundene Route, ohne allgemeinen Zugriff auf die Objektverwaltung oder Kontakte.

## Konflikte, Berechtigungen und Board

`erlaubte_status` kommt aus einem gemeinsamen Service in Liste und Detail; damit kann auch der letzte Listenstand eine Offline-Bestätigung ermöglichen. Die bestehenden Zustände und Übergänge bleiben Grundlage.

Neue Clients schicken optional die gelesene `auftrag_version`, den `ausgangsstatus` sowie beim bewussten Unterbrechen die konkret erwartete `aktiver_dispatch_id`. Bestehende Clients ohne diese Angaben bleiben kompatibel. Neue Clients erhalten bei Führungsänderung `auftrag_geaendert`, bei überholtem Status `status_geaendert` und bei einer anderen aktiven Disposition `aktiver_auftrag`. Zurückgezogene Aufträge haben keine Statusaktionen. Konflikte werden nicht automatisch bestätigt; **Auftrag prüfen** und eine neue bewusste Aktion verwenden den aktuellen Stand. Ein abgelehnter Wechsel bewahrt Originalauftrag und Originalversion in der Outbox.

Statusschreiber der Einheitenansicht und des Boards sperren dieselbe Einheitenzeile bis zum Commit. Bestehende aktive Dispositionen werden anschließend frisch geprüft. MariaDB-Snapshot-Konflikte und Deadlocks wiederholen die gesamte Status-Transaktion höchstens dreimal mit Rollback; externe Effekte und Broadcasts erfolgen danach. Bei anhaltender Belegung erhält der Client HTTP 503 und kann mit derselben UUID wiederholen. Der vorhandene Idempotenznachweis verhindert doppelte Journaleinträge.

Hintergrund: [MariaDB-Dokumentation zu Snapshot-Isolation](https://github.com/mariadb-corporation/mariadb-docs/blob/main/server/server-usage/storage-engines/innodb/innodb-system-variables.md#innodb_snapshot_isolation). Der reale Paralleltest läuft mit aktivierter Snapshot-Isolation; ein reiner SQLite-Test würde dieses Verhalten nicht abdecken.

Nach einem Wechsel werden sowohl die neue als auch die unterbrochene Disposition/Einsatzstelle über die vorhandenen `einheit:changed`- und `site:card_changed`-Ereignisse aktualisiert. Phasenautomatik, Live-Benachrichtigung und bestehende Abschlusslogik bleiben erhalten. Ein nicht aktiver Abschluss oder eine Bestätigung derselben Stelle darf den anderen aktiven Auftrag nicht aus der primären Anzeige entfernen.

GK und QR bleiben auf eigene Dispositionen beschränkt. Die bisherige Einschränkung auf eigene Stellenmeldungen und Fotos bleibt erhalten und gilt nun auch für direkte Fotoaufrufe. Andere Einheiten werden über diese Links weiterhin nicht offengelegt; Geräte-/Benutzerrechte bleiben maßgeblich. Die Beschränkung des Meldungsverlaufs erfolgt vor dem 50-Einträge-Limit. Rückzug, Dokumentfreigabe und Modulfreigabe werden bei jedem Planaufruf erneut geprüft.

## Regression und Grenzen

| Fall | Prüfung |
|---|---|
| Drei Zuweisungen vor Übernahme vollständig lesen | API und Chromium, einschließlich langem Auftragstext, Objekthinweisen und Plan |
| Zwei bestätigt, ein dritter aktiv | API und Browser bei 360/1024 px; Reihenfolge und primärer Auftrag bleiben erhalten |
| Neuer dringender Auftrag | API und Browser: Warnhinweis und Erreichbarkeit, kein automatischer Wechsel |
| Bewusster Wechsel / inzwischen anderer aktiver Auftrag | API, Browser und Benachrichtigungen beider Einsatzstellen |
| Geänderter oder zurückgezogener Auftrag | API und geöffnetes Detail, stale Offline-Aktion wird abgelehnt |
| Zwei Offline-Bestätigungen, Reload, spätere Synchronisation | Browser mit echter IndexedDB und API; kein impliziter Einsatzbeginn |
| GK-/QR-Rechte, Medien, Dokumentfreigaben | API einschließlich nicht verknüpfter, alter und wartender Pläne |
| Gleichzeitige Aktivierung / gleichzeitige Bestätigung | Drei echte MariaDB-Paralleltests: Tablet/Tablet, Tablet/Board, Bestätigung/Bestätigung |
| Vollständiger Rollback bei Wiederholung | Nach Mutation simulierter Snapshot-Konflikt erzeugt genau einen Status-/Journaleintrag |

Tests: `tests/test_einheit_mehrfachuebernahme.py`, `tests/test_einheit_vorschau.py`, `tests/test_einheit_uebersicht.py`, `tests/mariadb/test_einheit_parallel.py` und bestehende GSL-/Outbox-Regressionsgruppen. Die Browserfälle laufen über die bereits bestehende CI-UI-Prüfung; die MariaDB-Fälle über den vorhandenen MariaDB-Job.

Offline bleiben vollständige Details verfügbar, soweit zuvor vom bestehenden Service Worker geladen. Ohne diese Daten stehen nur die gespeicherten Listeninformationen bereit; fehlende Sperrendaten werden ausdrücklich als nicht verfügbar bezeichnet. Neue automatische Downloads aller Pläne sind nicht Bestandteil dieses Auftrags. Safari und echte Geräte benötigen weiterhin eine gesonderte Abnahme. Die vorangegangene Bestandsanalyse bleibt für übrige, hier nicht behandelte Befunde maßgeblich.
