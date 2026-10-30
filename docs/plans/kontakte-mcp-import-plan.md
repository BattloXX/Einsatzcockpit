# Kontaktverwaltung und MCP-Import

## Bestandsanalyse

Die bestehende Verwaltung speichert einen zentralen `Kontakt` mit Legacy-Freitextfeldern, mehreren Telefonnummern, Kategorien, Objektzuordnungen und einer Version für Optimistic Locking. Externe Referenzen sind bereits quellen-namespaced. CSV/XLSX-Import und MCP-CRUD existieren, arbeiten aber noch mit dem flachen Format.

## Umgesetzte Grundlage (Migration 0260)

- Additive Personenstammdaten sowie Herkunfts- und Prüfmetadaten.
- Strukturierte E-Mail-Adressen, Adressen, Organisationen und Organisationsfunktionen.
- Migration bestehender `kontakt.email`-Werte nach `kontakt_email`; das Legacy-Feld bleibt unverändert für alte UI/API-Clients.
- Erweiterte Telefonnummern (`typ`, `verwendung`, WhatsApp-Eignung, aktiv), ohne Label oder Sortierung zu verlieren.
- MCP-Tools für Organisationssuche/-upsert, Funktionszuordnung, strukturierte Bulk-Upserts sowie Vorschau/Ausführung/Status von Importen.
- Importmodus `merge` ist Standard: fehlende Werte und leere Listen löschen nichts. Externe IDs werden über die vorhandene Kombination aus Quelle, Namespace und ID abgeglichen.

## Importablauf

1. `kontakt_import_vorschau` klassifiziert jeden Satz als `NEW`, `UPDATE`, `UNCHANGED` oder `DUPLICATE_CANDIDATE`, ohne Fachdaten zu ändern.
2. `kontakt_import_ausfuehren` prüft ID und Version des vorgeschauten Kontakts erneut.
3. Bei einem Versionswechsel oder unsicherer Dublette wird der Satz übersprungen statt automatisch zusammengeführt.
4. Das Ergebnis wird zusammen mit Quelle, Anfrage und optionalem Idempotency-Key als Batch protokolliert.

## Nächste Ausbaustufen

1. Die vorhandene Kontakt-UI auf die neuen 1:n-Bereiche erweitern und private Werte mit feingranularen Berechtigungen maskieren.
2. CSV/XLSX/vCard/JSON auf dieselbe strukturierte Import-Pipeline umstellen; PDF nur über eine explizit bestätigte, unsicherheitsmarkierte Extraktion.
3. Feldgenaue Diffs, explizite Konfliktentscheidungen und reversible Änderungs-Snapshots für einen sicheren Rollback ergänzen.
4. `kontakt_aktualisieren` um elementweise `add`, `update`, `remove`, `set_preferred`-Operationen mit stabilen IDs erweitern.
5. Migration in einer Staging-Datenbank und Datenschutz-/Berechtigungstests vor einem Produktions-Rollout durchführen.

## Nicht automatisch ausgelöst

Kontaktimporte senden keine SMS, E-Mails oder andere Benachrichtigungen aus.
