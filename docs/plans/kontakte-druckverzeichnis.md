# Einsatzkontakte filtern und drucken

## Repositoryanalyse

- `Kontakt` ist mandantenbezogen; Kategorien sind bereits über `KontaktKategorieZuordnung` mehrfach zuweisbar. Keine zweite Kontakttabelle erforderlich.
- `KontaktTelefon` enthält Bezeichnung, Verwendung (dienst/privat) und Aktivstatus. `KontaktEmail`, `KontaktOrganisationFunktion` sowie `erreichbarkeit` werden wiederverwendet.
- Die Kontaktübersicht hat Suche, Typ- und Kategoriefilter; Kategorien werden bislang nur per Name angelegt.
- Verzeichnisrechte (`require_kontakt_directory`, `KONTAKT_LESE_ROLLEN`) sind strenger als Einsatzfreigaben. Druck erfordert dieselben Verzeichnisrechte; Geräte-/QR-Zugänge erhalten keinen Export.
- PDF-Ausgabe verwendet Jinja und WeasyPrint. Diese Infrastruktur wird mit einer gemeinsamen Vorlage für Vorschau und PDF weiterverwendet.
- MCP benötigt sowohl Registry-Werkzeuge als auch FastMCP-Wrapper in `app/mcp/server.py`.

## Phasen

1. **Datenbasis:** Migration 0293 ergänzt Kontakt-Druckkennzeichnungen, Reihenfolge/Aktivstatus/Standarddruck/Objektkennzeichnung der bestehenden Kategorien und eine Tabelle für persönliche mandantenbezogene Druckprofile. Bestehende Kontakte werden nicht automatisch freigegeben. Acht Kategorien pro bestehendem Mandanten werden ergänzt, gleichnamige Kategorien wiederverwendet. Erreichbarkeit bleibt unverändert.
2. **Gemeinsamer Service:** validierte Einstellungen, fünf vordefinierte Profile, persönliche Profile, Kategoriepflege, ungeblätterte Filterung einschließlich strukturierter Organisationsfunktionen. Kontakte erscheinen genau einmal unter der ersten passenden Kategorie. Objektzuordnungen und Objektkategorien werden standardmäßig ausgeschlossen. Private oder inaktive Telefonnummern sowie private E-Mails erscheinen nie; allgemeine Notizen werden nicht exportiert. Hinweise blockieren den Druck nicht.
3. **Benutzeroberfläche/PDF:** Drucken-Einstieg übernimmt aktuelle Listenfilter. Eigene Druckansicht im ECP-Design mit Profilen, Kategorien, Kontaktfiltern, Feldauswahl, Anzahl, Hinweisen und Vorschau. Zwei A4-Layouts, wählbares Format/Ausrichtung, Tabellenkopf-Wiederholung, Datum und PDF-Seitenzahlen. PDF nur authentifiziert und ohne Cache, keine dauerhaft gespeicherten Ausgaben. Kontakteditor und Detailansicht erhalten Druckkennzeichnungen, Kategorienverwaltung bleibt im Kontaktmodul.
4. **MCP:** vorhandene Lese-/Schreibfunktionen um Kennzeichnungen erweitern; Kategoriepflege, persönliche Profilpflege und gefilterte Drucklisten über denselben Service. PDF bleibt über authentifizierten UI-Endpunkt verfügbar (MCP-PDF optional).
5. **Prüfung:** Migration mit Bestandsdaten, Standardfilter/Objektausschluss, Mehrfachkategorien, Privatdaten, sämtliche Filter, Profileigentum/Mandantenisolation, Rechte, UI und echte mehrseitige PDF-Ausgabe; bestehende Kontakttests ausführen.

## Entscheidungen

- „Im Druckverzeichnis anzeigen“ ist eine ausdrückliche Freigabe und standardmäßig aus. „Alle Kontakte“ kann diese Auswahl bewusst erweitern; private Kommunikationswerte bleiben ausgeschlossen.
- Einsatzrelevant und Druckfreigabe sind unabhängige Filter.
- Kategorien werden deaktiviert statt gelöscht. Zugeordnete inaktive Kategorien bleiben in Kontaktdetails sichtbar.
- Profile enthalten ausschließlich Einstellungen, keine Kontaktkopien. Jeder Abruf liest den aktuellen Datenbestand.
- Organisationsname wird immer ausgegeben. Das vorhandene Organisationslogo erscheint standardmäßig in beiden Layouts; „Organisationslogo anzeigen“ lässt sich deaktivieren und wird im Druckprofil gespeichert. Ohne gültige Logodatei wird der Kopf ohne Bild ausgegeben.

## Umsetzung und Abnahme

Alle fünf Phasen sind implementiert. Die Standardkategorien haben stabile interne Schlüssel, damit vordefinierte Profile auch nach Umbenennungen funktionieren. Bei Kategorieänderungen über MCP bleiben nicht übergebene Kennzeichnungen erhalten. Die einfache Kontaktbearbeitung erhält bereits vorhandene Privat-/Aktivkennzeichnungen der Telefonnummern.

- Kontaktverwaltung → **Drucken** übernimmt Suche, Typ und gewählte Kategorie.
- **Kategorien verwalten** ermöglicht Pflege und Einrichtung fehlender Standardkategorien für neue Organisationen.
- Standardauswahl: aktiv, explizit druckfreigegeben, drei Standardkategorien, ohne Objektkontakte.
- Druckprofile werden pro Benutzer und Organisation gespeichert; gleicher Profilname ersetzt die Einstellungen.
- Browserdruck lädt die Vorschau direkt vor dem Druck neu. PDF, Vorschau und MCP-Druckliste laden ebenfalls aktuelle Daten.
- Migration `0293` liegt bereit. Beim regulären Deployment mit `.venv/bin/alembic upgrade head` ausführen; während der Entwicklung wurde keine Bestands-/Produktivdatenbank migriert.

Automatisierte Prüfung: Kontaktregressionen, MCP einschließlich HTTP-Berechtigungen, Filter, Datenschutz, Profilisolierung, Migration mit Bestandsdaten und Downgrade sowie echte mehrseitige PDFs beider Layouts. Die separate Chromium-Abnahme prüft Filterübernahme, Layoutwechsel, Vorschau, PDF-Download und Browserdruck. Aktivierung mit `ECP_BROWSER_TEST=1`; bei Bedarf kann `ECP_BROWSER_EXECUTABLE` einen vorhandenen Chromium-Pfad setzen. Ohne diese Aktivierung wird nur die Browserabnahme übersprungen.

Optional weiterhin ausbaubar: ein eigener MCP-PDF-Export. PDF-Download und Browserdruck sind über die authentifizierte Kontaktverwaltung verfügbar. Die physische Ausgabe auf einem konkreten Druckermodell ist nicht automatisiert prüfbar.


## Ergänzung: Organisationslogo

Die Ausgabe verwendet das über **Administration → Einstellungen → Logo** hochgeladene Logo derselben Organisation (`OrgSettings.logo_path`, ersatzweise `FireDept.logo_path`). Unterstützt werden PNG, JPG/JPEG, SVG und WebP bis 2 MB. Die Darstellung erfolgt proportional rechts im Dokumentkopf, maximal 26 × 22 mm. Das Logo ist in Vorschau, Browserdruck und serverseitigem PDF identisch eingebettet.

`DruckEinstellungen.logo_anzeigen` ist standardmäßig `true`; bestehende Profile ohne das neue Feld verwenden ebenfalls diesen Standard. Über die Druckansicht oder die bestehenden MCP-Profilwerkzeuge kann die Auswahl gespeichert werden. Es ist keine zusätzliche Datenbankmigration erforderlich.

Der Server liest ausschließlich die eigene Logodatei aus dem vorhandenen Uploadverzeichnis. Fremde Organisationsdateien, externe URLs und Pfade außerhalb dieses Verzeichnisses werden nicht geladen. Fehlende, beschädigte oder zu große Dateien führen zu einer Ausgabe ohne Logo. WeasyPrint darf ausschließlich die konkret eingebettete Logo-Daten-URI laden; weitere Netzwerk- oder Dateizugriffe bleiben gesperrt.

Die Tests prüfen alle vier Bildformate in beiden Layouts, eingebettete Rasterbilder im tatsächlichen PDF, Ein-/Ausblenden, Profilkompatibilität, fehlende/beschädigte Dateien und die Mandantentrennung. Die Browserabnahme prüft zusätzlich, dass das Logo tatsächlich geladen und nach dem Abschalten entfernt wird.
