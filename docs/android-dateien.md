# Dateien in der Android-App

`ELNative.downloadFile(url, filename?)` und `ELNative.openFile(url)` verwenden
`Capacitor.Plugins.NativeFiles`. Die Bridge löst relative URLs gegen die aktuelle
Seite auf, akzeptiert für Dateiaktionen nur HTTP(S) der gleichen Origin und entfernt
`nt`. Das Android-Plugin beschränkt sich zusätzlich auf HTTPS der eigenen Hosts.
Es übernimmt die WebView-Cookies; es gibt hier keinen native-link-Handoff.
Downloads gehen über den DownloadManager nach „Downloads“, Öffnen lädt in den
Cache und startet den ACTION_VIEW-Chooser. Meldet `openFile` „Keine App …“, lädt
die Bridge stattdessen mit `download` herunter. Andere Fehler werden angezeigt.

Für alte Apps ohne NativeFiles bleibt `Browser.open` mit dem bestehenden
`/api/v1/device/native-link`-Handoff erhalten. Ohne beide Plugins erscheint über
den vorhandenen Alpine-Toast (sonst `alert`) der Hinweis:
„Bitte die App aktualisieren, um Dateien herunterzuladen.“
Nur bei serverseitig bestätigten Attachment-Routen navigiert ein Download danach
mit `location.assign` und `download=1`: Objekt-Einzelseiten/Originale/Sammel-PDF,
`/druck/viewer/pdf` und Medien-Dateien. Unbekannte oder reine Inline-Routen wie
`/druck/dokument.pdf` bekommen ausschließlich den Hinweis; das WebView-Verhalten
bei Attachment-Navigation hängt von der alten Android-Version ab.

Der globale Capture-Klickhandler verteilt `download`, `data-dv-download` und
`download=1` auf `downloadFile`. Same-Origin-PDFs, Originale und Auszüge gehen an
`openFile`, andere neue Tabs an `openUrl`. Der Viewer prüft anschließend
`defaultPrevented`, sodass dieselbe Aktion nicht doppelt verarbeitet wird.
PDF.js-Rendering und normale Viewer-Einstiegslinks bleiben unverändert.
`openUrl` erkennt ebenfalls `.pdf`, `/pdf` (auch mit Unterpfad), `/original` und
`/sammel-pdf`; damit nutzt der lokale Druckdialog für PDF-URLs NativeFiles.
Seine HTML-Druckseiten und `localFn` behalten ihre bisherige Behandlung.
Der direkte PDF-Aufruf der Mediengalerie verwendet explizit `openFile`.
Die übrigen untersuchten `window.open`-Aufrufer öffnen HTML-Druckseiten,
Archivseiten oder externe Karten, keine direkten PDF-Dateien.

Der Service Worker verwendet `ec-v25`. In `base.html` lädt die Bridge bereits mit
`?v={{ ASSET_VERSION }}`. Diese Version stammt automatisch aus dem maximalen
Änderungszeitpunkt der JS-Dateien (`app/core/templating.py`); eine manuelle
Asset-Versionsänderung ist daher nicht nötig. `login.html` lädt die Bridge ohne
Versionsquery; dort greift die neue Service-Worker-Cache-Version.

Gezielte Prüfung: `node --test tests/js/native_bridge.test.mjs`,
`node --test tests/js/dokumentviewer.test.mjs` und
`.venv/bin/python -m pytest tests/test_sw_live_notification.py tests/test_sw_static_cache_busting.py`.
Der echte DownloadManager, WebView-Cookies und der Android-Chooser müssen nach
Einbau des Plugins auf einem Gerät geprüft werden (inklusive fehlender PDF-App
und einer alten App ohne Plugins).
