# BSP-Overlay: georeferenzierte Planausschnitte in der Einsatzkarte

Ein freigegebener Ausschnitt eines Lage- oder Brandschutzplans wird lagegenau als Raster-Ebene
unter den Objektsymbolen der Einsatzkarte angezeigt. Umsetzungsplan und Begruendung:
[bsp-overlay-plan.md](../plans/bsp-overlay-plan.md). Die Georeferenz selbst (Referenzpunkte, Modell,
RMSE) bleibt die einzige Quelle in `objekt_dokument_georef`; ein Overlay referenziert sie nur.

## Datenmodell (Migration 0336)

- `objekt_bsp_overlay`: Objekt, Dokument, Seite, Ausschnitt (normiert 0..1 auf der gedrehten Seite),
  Georef-Referenz (`georef_id`, `georef_revision`, `dokument_hash`), Status `entwurf` / `freigegeben` /
  `zurueckgezogen`, `freigabe_nr`, Anzeige- und Druckvorgaben, Rasterdatei samt Pruefsumme und Bounds.
- `bsp_overlay_einsatz`: einsatzbezogene Sichtbarkeit `INHERIT` / `ON` / `OFF` und Deckkraft.

## Auslieferbar

Alle Pfade (Einsatzkarte, Druck, MCP-Lesefelder) nutzen `overlay_ist_auslieferbar`: freigegeben, aktuelle
Dokumentversion, aktuelle Georef gueltig und identisch (id, Revision, Hash), Rasterdatei mit passender
Pruefsumme. Wird das PDF ersetzt oder die Georef korrigiert, verschwindet das Overlay aus Einsatz und
Druck, bis es neu geprueft und freigegeben ist. Keine stille Uebernahme auf neue Planversionen.

## Verwaltung

Im Georef-Editor erklärt "Plan auf der Karte anzeigen (BSP-Overlay)" die Voraussetzung:
eine gültige gespeicherte Georeferenz und das aktuelle freigegebene Dokument. Fehlt sie,
erscheint eine Hinweiskarte mit Link nach oben statt eines gesperrten Formulars.
Overlay-Karten zeigen Name, Planart, Geschoss/Bereich, Freigabenummer und Status mit Icon:
Entwurf (grau), Freigegeben (grün), Veraltet (orange mit Prüfhinweis), Zurückgezogen (grau, durchgestrichen).

Drei Schritte führen durch Ausschnitt, Angaben und Prüfung/Freigabe. Neben ganzer Seite und Rechteck
kann ein Polygon mit 3 bis 100 Punkten gezeichnet werden: Antippen setzt Punkte, der erste Punkt
oder "Polygon abschließen" schließt die Auswahl. Eckpunkte sind danach über 44-px-Griffe
verschiebbar. Selbstüberschneidungen, Berührungen und zu kleine Flächen sperren Abschluss und Speichern.
Die Ausschnittauswahl hebt
den Plan hervor und scrollt ihn ins Bild. Anzeigevorgaben und standardmäßig eingeklappte
Druck-Einstellungen sind getrennt. "Speichern und Vorschau" speichert über die bestehenden
Endpunkte zuerst den Entwurf und rastert danach; Freigaben erfordern weiterhin die menschliche
Bestätigung aller serverseitigen Warnungen. Ungespeicherte Änderungen werden gekennzeichnet.

Die reine JS-Funktion `bspLagegenauigkeit` bewertet ausschließlich die Darstellung: RMSE bis
0,5 m "gut", bis 2 m "prüfen", darüber "ungenau"; eine ungültige Georef heißt "neu prüfen".
Diese Anzeige ersetzt keine serverseitige Validierung. Rohwerte stehen in technischen Details
und im Tooltip; wenige Punkte und freie Kartenklicks werden als Warnsätze angezeigt.
Der Freigabedialog fasst Plan, Ausschnitt, Anzeige und Lagegenauigkeit zusammen.
Mobil bis 760 px sind Felder und Aktionen einspaltig mit mindestens 44 px großen Touch-Zielen;
die Schritte bleiben im Dokumentfluss über dem vorhandenen Abstand zum Android-Dock.

## Rasterung

`objekt_bsp_overlay_service.raster_erzeugen` rechnet die Ausschnittsecken mit der Georef-Transformation auf
Lat/Lng und warpt das gedrehte Seitenbild per PIL (affin bzw. perspektivisch) in ein nordgerichtetes
RGBA-PNG. Polygonpunkte gelten normiert auf der gedrehten Seite; die umschließende Box
`x0,y0,x1,y1` berechnet ausschließlich der Server. Die Georef-/Bounds-Rechnung nutzt diese Box,
die Alphamaske die Polygonpunkte (2-fache Maskenauflösung, außerhalb transparent).
Die Raster-Langseite ist auf 2048 px begrenzt. Ablage unter
`OBJEKT_MEDIA_DIR/bsp_overlay/<org>/`, nie unter `static`. Auslieferung nur ueber
`/objekt-medien/bsp-overlay/{id}/raster` mit Login, Org-Pruefung, `private`-Cache und ETag.

## Einsatzansicht und Objektkarte

- Lageführung: `GET /einsatz/{id}/lagefuehrung/bsp-overlays.json` und
  `POST .../bsp-overlays/{overlay_id}/einstellung` (nur Bearbeitungsrecht; Event-Log, WebSocket
  `lagefuehrung.bsp.changed`). Nur bestaetigt zugeordnete Objekte.
- Objekt-Einsatzansicht `/objekte/{id}/einsatz`: Die per HTMX eingebettete Readonly-Karte
  (`objekt/_karte_readonly.html`) zeigt die Ebene lesend mit lokalem Schalter ueber
  `GET /objekte/{id}/bsp-overlays.json` (nur Overlays dieses Objekts, nur auslieferbar, Objekt-Vorgabe
  statt Einsatz-Einstellung).
- Objektverwaltung: Karten-Tab und Vollbild-Editor `/objekte/{id}/karte` nutzen dieselbe lokale Ebene.
  Das einklappbare Panel unter der Karte verdeckt auch mobil keine Palette oder Zeichenwerkzeuge.
  `dataset.initialisiert` verhindert doppelte Karten/Ebenen beim Tab-Wechsel. Geoman ignoriert das Raster.
  Die JSON-Liste enthaelt weiterhin ausschliesslich auslieferbare Overlays. Nur Verwalter erhalten im
  Antwort-Header `X-BSP-Pruefen` die Anzahl der Entwuerfe und veralteten Freigaben dieses Objekts
  (ohne Raster-URL); das Panel weist auch ohne auslieferbare Ebene auf die Pruefung im Georef-Editor hin.
  Zurueckgezogene Overlays werden nicht mitgezaehlt.
- Einsatz-Info, GSL-Karte und Einheitenansicht: lesend mit lokalem Schalter (localStorage). QR- und
  Token-Zugaenge erhalten kein Overlay.
- Leaflet-Modul `app/static/js/bsp_overlay_layer.js`: eigene Pane `bsp` (zIndex 250), nicht interaktiv,
  Ladefehler entfernen die Ebene still. Schalter "fuer alle im Einsatz" und "nur fuer mich" sind getrennt.
- Der Service Worker nimmt Raster und Overlay-JSON vom Offline-Cache aus.

## Druck

Bildschirm und Druck sind getrennt. Der manuelle Druck der Lageführungskarte kennt `MAP_ONLY` (Default),
`MAP_WITH_BSP`, `BSP_EXCERPT_ONLY` und `MAP_AND_BSP_SEPARATE`; nicht verfuegbare Overlays erscheinen als
Hinweisblatt, nie als stilles Weglassen. Regelbasiert gibt es nur den Ausschnitt: Dokumenttyp `bsp_overlay`
und Objekt-Element `bsp_overlay` in der bestehenden `PrintRule`, nur bei `autodruck` und auslieferbarem
Overlay, Idempotenz ueber `artifact_ref = "<overlay_id>:<freigabe_nr>"`. Einen automatischen Karten-Einsatzdruck
gibt es nicht.

Beide Druckwege verwenden `original_ausschnitt_png`: Das gedrehte Original wird unverzerrt
auf die Ausschnittbox zugeschnitten. Bei Polygonen wird der Bereich außerhalb weiß maskiert.

## MCP

`objekt_karte_lesen` und `objekt_karte_vorschau` liefern `bsp_overlays`. `objekt_bsp_overlay_entwurf`
(`anlegen` / `aendern` / `status`, Trockenlauf Standard) speichert nur Entwuerfe. Freigabe, Zurueckziehen und
Loeschen gibt es per MCP nicht. Regeln: [KI-Symbolpflege](../objektkarte-ki-symbolpflege.md).

## Bewusst nicht enthalten

Kachelpyramide, Overlay im Lagebericht-PDF (`staticmap`), Infoscreen,
automatische Plan-Registrierung.
