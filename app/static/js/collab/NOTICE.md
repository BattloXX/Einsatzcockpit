# Vendorte Third-Party-JS (Lagedokument-Kollaboration)

Diese Dateien sind unveraendert von jsdelivr.net (npm-CDN) geladene Distributionsdateien der
folgenden Pakete, committet fuer einen bausteinfreien Deploy (kein Node.js zur Laufzeit,
Konvention wie `quill.min.js`). Eingebunden per Browser-Import-Map, siehe
`app/templates/incident_major/lagedokument.html`.

| Paket | Version | Lizenz | Quelle |
|---|---|---|---|
| yjs | 13.6.33 | MIT | https://www.npmjs.com/package/yjs |
| lib0 | 0.2.119 | MIT | https://www.npmjs.com/package/lib0 |
| y-protocols | 1.0.7 | MIT | https://www.npmjs.com/package/y-protocols |
| y-websocket | 3.1.0 | MIT | https://www.npmjs.com/package/y-websocket |
| y-quill | 1.0.0 | MIT | https://www.npmjs.com/package/y-quill |
| quill-cursors | 5.0.0 | MIT | https://www.npmjs.com/package/quill-cursors (UMD-Bundle, daher NICHT hier sondern als `app/static/js/quill-cursors.js` + `app/static/css/quill-cursors.min.css` abgelegt, gleiche Konvention wie `quill.min.js`) |

Aktualisierung jetzt über `package.json` + `npm run vendor`; Versionen in dieser Tabelle entsprechend anpassen.
