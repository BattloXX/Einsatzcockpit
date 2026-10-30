# Android: Pull-to-Refresh

## Befund aus dem Code

- `_formDirty()` verglich bisher jeden Feldwert mit dem HTML-Default, auch ohne
  Nutzereingabe. Damit sperrten etwa Alpine-Vorbelegung und Such-/Filteränderungen.
  HTML-Vorbelegung mit identischem `defaultValue` allein war kein Fehler.
  Vorhandener Contenteditable-Text sperrte immer; das Löschen eines Textfelds
  wurde dagegen nicht geschützt.
- Der globale Scrolltest und jeder beliebige `scrollTop` eines Vorfahren sperrten,
  ohne tatsächliche Scrollbarkeit zu prüfen. Der Zustand wurde nur bei Touchstart
  und Fokus aktualisiert. Ein innerer Scrollbereich am Anfang konnte deshalb
  durch die äußere Seite gesperrt bleiben. Androids zusätzliches `getScrollY() > 0`
  überstimmte ebenfalls die Entscheidung des inneren Scrollbereichs.
- Nur `base.html` lud `native-bridge.js`. Admin-, UAS- und GSL-Lage-Layouts erben
  diese Basis und waren damit versorgt. `public/base.html` und
  `objektpflege/_layout.html` waren eigenständig und unversorgt. Ebenso:
  `login.html`, `auth/*.html`, `errors/fehler.html`, `gk/einloesen.html`,
  `foerderstrecke/maschinist.html`, `incident/{dashboard,pin_entry,screensaver}.html`,
  `incident_major/{lage_qr,lage_qr_name,lage_qr_pin,public_danke,public_report,public_sms_error,public_verify}.html`,
  `infoscreen/statistik.html`, `objekt/infoscreen_alarm.html`,
  `probenplanung/public_plan.html`, `public/{alarm_login,alarm_summary,page}.html`,
  `road_closure/{infoscreen,public_detail}.html`, `weather/infoscreen.html` und
  eigenständige HTML-Druckansichten. Diese laden jetzt den kleinen gemeinsamen
  PTR-Teil. PDF-Renderer und E-Mail-Templates benötigen ihn nicht.
- `overscroll-behavior:contain` in `tailwind.input.css` und generiertem `app.css`
  betrifft nur das mobile Untermenü und die Hydrantenliste. Kein globaler
  CSS-Blocker nachgewiesen; CSS bleibt unverändert. Die Wirkung der nativen
  Gestenerkennung muss am Gerät geprüft werden.
- `incidentBoard()` behandelte `ec:resync` auch im Funkjournal als Board-Refresh.
  Der Journalinhalt wurde dabei nicht aktualisiert. Ohne Kanban wird das Event
  jetzt nicht beansprucht; PTR verwendet seinen Seiten-Fallback. Bei Boards
  werden die Lade-Promises an PTR übergeben, beim Lage-Board einschließlich
  aller Phasenspalten und übergreifenden Meldungen. Fehler oder ein Timeout
  melden keinen Erfolg mehr.

## Verhalten

`native-ptr.js` bietet auf allen Browser-Dokumenten `ELNative.refresh()` und
`ELNative.ptrBlocked()`; `native-bridge.js` verwendet dieselben Funktionen.
Echte `input`-Events markieren bearbeitete Felder. Suchfelder, GET-/HTMX-GET-Formulare
und explizite `data-no-dirty`-Bereiche zählen nicht als ungespeicherte Eingaben.
Weitere Filter ohne GET-Formular müssen `data-no-dirty` erhalten.

Der Touchpunkt bestimmt den nächsten vertikal scrollbaren Vorfahren.
`scrollTop <= 0` erlaubt PTR, sofern keine Eingaben, Karten, Editorbereiche oder
Dialoge sperren. Touchmove und Scroll aktualisieren den nativen Zustand.
Scrollpositionen blockieren still, auch bei einem direkten `refresh()`-Aufruf
(Spinner-Ende über `ECPtr.done`). Dialoge, Karte/Editor und ungespeicherte Eingaben
werden höchstens einmal je Geste und alle 30 Sekunden je Seite und Grund über den
nativen Toast (`ECPtr.failed`) erklärt. Vor dem Refresh wird die Sperre nochmals geprüft;
bei unterdrücktem Hinweis beendet `ECPtr.done` den Spinner.
`pagehide` gibt den nativen Zustand frei.

## Android-Patch

Das Android-Repository liegt außerhalb der für diesen Auftrag beschreibbaren
Sandbox-Pfade. Daher liegt seine Änderung als Patch in
`docs/patches/android-pull-to-refresh.patch`, ohne Änderung am Android-Repository.
Er lässt die aktuelle JS-Scrollentscheidung gelten und verwendet ohne passenden
Seitenzustand `canScrollVertically(-1)`. Damit wirken Sperren einer anderen URL
nicht weiter. Die Bridge-Zustandsänderung erfolgt auf dem UI-Thread.

Anwenden im Android-Repository:

```sh
git apply /pfad/zur/web-working-copy/docs/patches/android-pull-to-refresh.patch
```

## Verifikation

Automatisiert: JS-Tests mit Mock-DOM für Eingaben, leere und vorbelegte Felder,
Such-/Filterausnahmen, Checkbox/Select/Contenteditable, Scrollhierarchie,
Touchpunkt/Touchmove/Scroll, Sperrhinweis, Standalone-Seiten, Refresh-Fallback,
Resync-Wartezeit und Fehler. Regressionstests für den Handler auf Unterseiten
und die Ladeaufträge des Lage-Boards. pytest prüft zusätzlich, dass jedes
Browser-Dokument den PTR-Teil einbindet. Das prüft keine echten WebView-Gesten.

Offen: Android-Build und Gerätetest mit kurzer/langer Liste, innerem
100dvh-Scrollbereich, Karte, Dialog, bearbeitetem Formular, Filtern, Login,
Public-Seite und Infoscreen; jeweils auch nach Navigation und im Offline-Modus.
