# Zentraler Dokumentenviewer – Phase 2

## Bestand und Entscheidung

Die bisherige `media-viewer.js` öffnete PDFs in einem Browser-iframe und Bilder
als einzelnes Bild. `openImageLightbox` und die Planverwaltung in
`katplan_editor.js` enthielten zusätzlich eigene Bildgalerien. Der Objektviewer
in `templates/objekt/viewer.html` nutzt serverseitig gerasterte Seiten mit
objektspezifischen Such-, Rotations- und Bearbeitungsfunktionen.

Für Einsatzwissen, Einsatz-/Katastrophenpläne und Maßnahmen gibt es nun eine
gemeinsame Komponente in `static/js/dokumentviewer.js` und
`static/css/dokumentviewer.css`. Die bisherigen PDF-/Bildgalerien sind durch
Adapter auf diese Komponente ersetzt. Video bleibt eine Medienfunktion.
Die bestehenden Aufrufe `openMediaViewer`, `closeMediaViewer`,
`openImageLightbox` und `printImage` bleiben verfügbar.

PDFs werden mit dem lokal eingebundenen **PDF.js 6.4.299 Legacy-Build** gerendert.
Bibliothek, Worker, Zeichenabbildungen, Schriften, Farbprofile und Bilddecoder
liegen mit Lizenz unter `static/vendor/pdfjs/6.4.299/`. Es gibt keine CDN-Abhängigkeit.
Die versionierten Assetpfade verhindern, dass der Service Worker bei einem
späteren Versionswechsel alte Bibliotheken mit neuen Workern mischt.
Die ESM-Dateien werden unter `.js` ausgeliefert, damit auch bestehende nginx-
Konfigurationen den JavaScript-MIME-Typ verwenden. Die Dateien sind bytegleich
mit den npm-Artefakten; `npm run vendor:check` prüft dies.
Die unveränderten Fremdlizenzen sind gezielt von Gits Whitespace-Prüfung ausgenommen.

Grundlage: [offizielle PDF.js-Beispiele](https://mozilla.github.io/pdf.js/examples/)
und [Mozilla-Hinweise zur Einbindung](https://github.com/mozilla/pdf.js/wiki/Setup-pdf.js-in-a-website).

## Bedienung

Ein Klick auf einen PDF-/Bildanhang öffnet einen einzigen Dialog. Die sichtbaren
PDFs und Bilder derselben Maßnahme werden als Dokumentfolge übernommen. Auch
Plan-/Kapitelanhänge bilden jeweils eine Folge. Externe Links und Office-Dateien
behalten ihre bisherigen Öffnungs-/Downloadfunktionen.

Im Viewer stehen zur Verfügung:

- Dokumenttitel und „Dokument X von Y“, vorheriges/nächstes Dokument und direkte
  Auswahl über die Dokumentenliste.
- Vorherige/nächste Seite, Seitensprung und „Seite X von Y“.
- Zoom, Einpassen, temporäres Drehen, Vollbild und bei Bedarf geladene Miniaturen.
- Hoch-/Querformat, native Scrollbereiche zum Verschieben vergrößerter Seiten,
  Wischen zum Seitenwechsel und Zwei-Finger-Zoom.
- Original öffnen, Download und Seitenauswahl mit Drucksymbol.

Alle wesentlichen Bedienelemente sind mindestens 44 Pixel groß. Pfeiltasten
wechseln die Seite, Alt+Pfeiltasten das Dokument. Escape und der Schließen-Button
schließen den Viewer; der Fokus kehrt zum Auslöser zurück.

Die zuletzt geöffnete Seite wird pro Benutzer-/Organisationskontext und
Dokument-URL einschließlich Versions-/Auszugsparametern in `sessionStorage`
gespeichert. Es werden nur Seitennummern gespeichert, keine Dokumentinhalte.
Ein ausdrücklich angeforderter Seitensprung hat beim Öffnen Vorrang vor dem Merker.
Zoom, Drehung und Seitenauswahl werden während des offenen Dialogs pro Dokument
gemerkt. Schnelle Dokumentwechsel brechen alte Lade-/Renderaufträge ab.

Die PDF-Darstellung wird bei Zoom und Drehung aus den Original-PDF-Daten neu
gerendert, einschließlich Bildschirm-Pixeldichte. Für Tablets ist die Größe
eines einzelnen Canvas auf 16 Millionen Pixel begrenzt. Originaldateien und
serverseitige Seitenrotationen werden nicht verändert.

## Seitenauswahl, Auszüge und Druck

Die Auswahl unterstützt die aktuelle Seite, eine andere einzelne Seite, Bereiche,
freie Kombinationen und das ganze Dokument. Beispiele: `5`, `5–8`, `2,5,9` oder
`3–5,12–15`. Leerzeichen, „Seite(n)“ und „und“ werden ebenfalls verstanden.
Doppelte Seiten werden entfernt und die Auswahl in Dokumentreihenfolge sortiert.
Ungültige Auswahl sperrt die Druckaktion und zeigt eine Meldung. Die Grenze liegt
bei 2.000 Seiten je Auswahl.

Die vorhandenen geschützten Dateirouten unterstützen optional:

- `seiten=3-5`: fester Auszug aus dem Original.
- `auswahl=1,3`: Auswahl bezogen auf die Seiten des angezeigten Dokuments/Auszugs.
- Bei Objektdokument-Anhängen aktiviert `auszug=1` den bereits am Anhang
  hinterlegten Seitenbereich. Leser verwenden den Bereich aus dem jeweiligen
  Freigabesnapshot, auch wenn der Arbeitsstand inzwischen geändert wurde.

Beispiel: `original?seiten=3-5&auswahl=1,3` liefert Originalseiten 3 und 5.
`dokumentviewer_service` erstellt diese PDFs mit dem vorhandenen pypdf. Text,
Vektoren und Seitenformate bleiben erhalten; das Original wird ausschließlich
gelesen. Antworten mit Auszügen verwenden `Cache-Control: no-store`.

Das Drucksymbol öffnet die Auswahl im Druck-iframe. „Auswahl als PDF öffnen“
bleibt als Alternative für Browser verfügbar, die keinen direkten PDF-Druck
aus einem iframe unterstützen, beispielsweise bestimmte Android-WebViews.
Die tatsächliche Druckerauswahl übernimmt das Betriebssystem. Bilder nutzen die
vorhandene Bilddruckfunktion.

Das Symbol **„Zum Einsatz hinzufügen“** ist in dieser Phase sichtbar, aber ohne
angebundenen Übernahmeadapter deaktiviert. Die neue Seitenauswahl wird noch in
keinen Einsatz geschrieben. Die bisherige Maßnahmenübernahme funktioniert weiter.
Phase 3 ist nicht Bestandteil dieser Umsetzung.

## Wiederverwendung und technische Schnittstelle

```js
openDocumentViewer([
  {
    id: 'optionale-stabile-referenz',
    url: '/plaene/anhang/UUID?version_id=123',
    kind: 'pdf', // oder 'image'
    title: 'Maßnahmenblatt',
    page: 5,
    selectionUrl: '/plaene/anhang/UUID?version_id=123',
    originalUrl: '/plaene/anhang/UUID?version_id=123'
  }
], 0);
```

`selectionUrl` muss einen geschützten Endpunkt bezeichnen, der `auswahl` auswertet.
Ohne diesen Adapter kann der Viewer PDFs anzeigen und das gesamte Original
drucken; ein Teil-PDF-Druck wird nicht vorgetäuscht. Für Bilder ist keine
`selectionUrl` notwendig. `edit` kann einen bestehenden Bildeditor verlinken.

`documentViewerSelection()` liefert `{document, pages, page}`. Dokumentreferenz
und Versions-/Auszugsparameter bleiben dabei erhalten; `pages` ist relativ zum
angezeigten Dokument. Optional kann ein späterer Integrator `options.onAdd`
angeben. Das Ereignis `documentviewer:print` enthält denselben Kontext und ist
abbrechbar, um eine bestehende Druckintegration anzuschließen.

Die deklarative Integration verwendet `data-dv-document`, `data-dv-kind`,
`data-dv-title`, `data-dv-page`, `data-dv-selection-url` und optional
`data-dv-original`. `data-dv-group` grenzt eine Dokumentfolge ab. Dies funktioniert
auch nach HTMX-Aktualisierungen über einen einzigen delegierten Click-Handler.

Die Original-Dateien-Liste der Objektverwaltung öffnet ihre Dateien als gemeinsame
Dokumentfolge mit dem Dateinamen als Titel. „Neue Datei ansehen“ in der Pflegeprüfung
und der PDF-Button im Objekt-Seitenviewer verwenden ebenfalls den zentralen Viewer.
Der PDF-Button ermittelt die aktuelle Seiten-ID beim Alpine-Klick; die gerasterte
Objektansicht mit Suche, Rotation und Bearbeitung bleibt bestehen. Viewer-JS und CSS
werden bereits über `base.html` geladen. Objekt-URLs erfüllen die Same-Origin-Prüfung
von `localUrl`; Änderungen am PDF.js-Rendering sind dafür nicht nötig.
Bestehende Medienaufrufe im Board
verwenden bereits über die kompatiblen Adapter dieselbe PDF-/Bildkomponente;
neue Board- oder Einsatzübernahmeabläufe wurden nicht ergänzt.

### Downloads in der Android-App

Original-Dateien haben zusätzlich einen sichtbaren Download-Link; die Dokumentliste
bietet einen Sammel-PDF-Download. Die Seiten-PDF lässt sich im zentralen Viewer über
„Download“ speichern. Original-, Seiten- und Sammel-PDF liefern bei `?download=1`
`Content-Disposition: attachment`, sonst weiterhin `inline`.

`native-bridge.js` fängt ausschließlich in Capacitor Klicks auf `a[download]` und
`a[data-dv-download]` zusammen mit bestehenden `target="_blank"`-Links ab und öffnet
sie über `Browser.open`. Auch absolute Same-Origin-URLs (wie `a.href` und die
Viewer-Download-URL) erhalten nun einen Handoff. Fremde Origins erhalten kein Token.
Der bestehende Endpunkt `/api/v1/device/native-link` bewahrt die Query einschließlich
`download=1`; nur `pathname + search` wird übergeben. Das Token bleibt exakt
pfadgebunden und 120 Sekunden gültig; Query-Parameter gehören wie bisher nicht zur
Signatur. Es gibt keine zusätzliche Pfad-Allowlist; Modul-, Rollen- und Tenantprüfungen
gelten weiterhin für den authentifizierten Dateiaufruf. Der Handoff setzt kein
allgemeines Session-Cookie im Custom Tab.

Der Service Worker umgeht für explizite Downloads und `nt`-Handoffs seinen Cache;
die bestehende Offline-Behandlung normaler Objektmedien bleibt erhalten.
Automatisierte Tests prüfen Attachment/Inline, Viewer-Einstiege, Touch-Klick und
PDF.js-Darstellung, Query-Erhalt, Handoff ohne Cookies, Pfadbindung sowie die native
Bridge und den Cache-Bypass. Die tatsächliche Übergabe eines Attachments vom Custom
Tab an den Android-Download-Manager muss auf einem Gerät geprüft werden; ein
JavaScript-Mock oder Desktop-Chromium kann diesen Betriebssystemablauf nicht belegen.

Gezielte Prüfbefehle für diese Ergänzung:

```sh
.venv/bin/python -m pytest tests/test_objekt_dokument_download.py
.venv/bin/python -m pytest tests/test_native_link.py
.venv/bin/python -m pytest tests/test_sw_static_cache_busting.py
node --test tests/js/native_bridge.test.mjs
node --test tests/js/sw_download.test.mjs
```

## Sicherheit, Kompatibilität und Prüfung

Jeder Dateiaufruf und jeder Auszug durchläuft weiterhin dieselben serverseitigen
Prüfungen auf Organisation, Modul, Rolle, Vertraulichkeit und Freigabeversion.
Es gibt keinen generischen URL-Proxy oder beliebigen serverseitigen Dateizugriff.
Die Komponente akzeptiert eigene HTTP(S)-Dateiadressen. PDF-JavaScript/XFA wird
nicht als interaktiver Inhalt ausgeführt. Fehlende, beschädigte oder nicht
zugängliche Dokumente zeigen eine Meldung, ohne die restliche Folge zu blockieren.

Es werden weder Datenbanktabellen noch Dokumentzuordnungen geändert. Eine
Datenmigration ist nicht notwendig. Bestehende Original-, Seitenbild-, Einzel-PDF-,
Download- und Plan-PDF-Adressen bleiben erhalten. Deployment/Produktivbereitstellung
ist nicht Teil dieser Arbeit.

Automatisierte Prüfung:

- Python: Auswahlfälle, PDF-Seitenformate und weiterhin extrahierbarer Text,
  unveränderte Originaldateien, Rollen-/Tenantgrenzen, vertrauliche Anhänge,
  historische Freigaben und verschachtelte Quellen-/Objektauszüge.
- JavaScript: freie Auswahl, Bereichsvalidierung und Auswahlgrenzen; Vendor-Abgleich.
- Chromium: vier Dokumente ohne Dialogwechsel, direkte Dokumentwahl, Seitenmerker,
  PDF/Bild, Seitenwechsel/-sprung, Miniaturen, Zoom, Drehung, Druckkontext,
  Vollbild, echte Touch-Ereignisse und schnelle Dokumentwechsel.
- Ansichten: 1440×900, 1024×768 und 390×844, mit Touch-Unterstützung und doppelter
  Pixeldichte. Ein physisches Android-Gerät und ein realer Drucker standen für
  diese automatisierten Tests nicht zur Verfügung.
- Die Viewer-Browsertests sind im vorhandenen Playwright-CI-Schritt enthalten.

Prüfbefehle:

```sh
.venv/bin/pytest -q tests/test_dokumentviewer.py tests/test_dokumentviewer_ui.py
npm run test:js
npm run vendor:check
```

Lokal kann `ECP_PLAYWRIGHT_EXECUTABLE` auf ein vorhandenes Chromium zeigen.

Ergebnis: **106 Tests im abschließenden Python-/Chromium-Lauf bestanden**, darunter
alle 26 neuen Viewer-Prüffälle, bestehende Anhangs-/Quellendokumenttests und beide
Icon-Wächter. **134 JavaScript-Tests bestanden**. `vendor:check` bestätigt alle
259 eingebundenen Dateien; Ruff, JavaScript-Syntaxprüfung, CI-YAML und
`git diff --check` sind erfolgreich.

In der breiten Regression bestanden zusätzlich 928 Prüfungen für Einsatzwissen,
Pläne/Freigaben/MCP, Objektdokumente, PDF-Erstellung, Annotationen, Berechtigungen,
CSP und Service-Worker-Kompatibilität. Die dort beanstandete Erfassung von
PDF.js-internen Schriftzeichen als eigene UI-Symbole wurde durch die korrekte
Kennzeichnung der unveränderten Bibliotheken als `.min.js` behoben und mit den
Icon-Wächtern im abschließenden Lauf geprüft. Ein bestehender Anhangstest grenzt
seine Protokollabfrage jetzt auf den geprüften Plan ein, damit vorausgehende
Dokumentverknüpfungstests seine Ergebnisse nicht verfälschen.
