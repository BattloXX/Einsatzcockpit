# ECP – Einheitliches Icon-System (Tabler Icons)

Stand: 2026-10-21 · Status: umgesetzt (P0–P11), Gerätetests offen

## Ziel

Alle Icons des Einsatzcockpits werden durch ein konsistentes, geräteunabhängiges
SVG-Icon-System auf Basis von [Tabler Icons](https://tabler.io/icons) (MIT) ersetzt.
Feuerwehrspezifische Symbole ohne Tabler-Entsprechung bleiben unverändert. Funktionen, Statuslogik und Barrierefreiheit bleiben unverändert.

## 0. Umsetzungsstand (2026-10-21)

| Phase | Inhalt | PR |
|---|---|---|
| P0 | Inventar + Zuordnung (`scripts/icon_inventar.py`, `docs/konzepte/icon-inventar.md`) | #651 |
| P1+P2 | Fundament (Tabler 3.49.0, `icon()`, `icons.js`, Tokens) + Wächter | #652 |
| P3 | Rahmen: Navigation, Übersicht, Admin-Menü, Fehlerseite, `modul_icon` | #653 |
| P4a/P4b | Einsatzboard, Einsatzinfo, Funk, Lageführung, Atemschutz | #654, #655 |
| P5a/P5b | GSL-Board, Tabs, Stab, Journale, Einheitenmodus | #656, #657 |
| P6a/P6b | Kartenmarker (`ecpMarkerIcon`, Cross-Marker `type_icon_name`), übrige Karten | #658, #659 |
| P7a–P7d | Objektverwaltung, Admin-Rest, Gateway/Förderstrecke/Termine, Katastrophenplan u. a. | #660, #661, #662 |
| P8 | Öffentliche Seiten, Infoscreens, Wetter; Tailwind-CDN der Infoscreens durch lokalen Build ersetzt | #663 |
| P9 | PDF/Druck, xhtml2pdf-Fallback ohne System-Icons, PDF-Tests | #664 |
| P10 | Merkmal-Symbole: Migration 0301 `merkmal_katalog.icon_name`, Icon-Auswahl | #665 |
| P11 | Aufräumen: CSS-Glyphe, begründete Ausnahmen, Doku | #665 |

Ergebnis: Emoji-Baseline 1466 → 85 Treffer, alle mit Begründung (`AUSNAHME_GRUENDE`).
Bewusst geblieben: Gefahren-Piktogramme (fachliche Information, inkl. Noto-Emoji-Schrift im
Objektblatt-PDF), Feuerwehr-Fachzeichen ohne Tabler-Entsprechung, Kompatibilitätsfelder
(`type_icon`, `typ_icon`, Merkmal-`icon`), Push-/API-/CLI-/XLSX-Texte.

Abweichungen vom Entwurf: keine eigenen ECP-SVGs (User-Entscheid 2026-10-20); die DB-Umstellung
betrifft nur Objektmerkmale (Kontakt-/Dokumentarten haben keine Symbole, Piktogramme bleiben).

Offen: Gerätetest auf Android-App, iPad/iPhone Safari, Windows Edge/Chrome, Desktop Firefox
(Board, GSL, Karten, Einheitenmodus, Infoscreens, PDF), Merkmal-Altwerte in Produktion nach
Migration sichten (Katalogpflege, Spalte Icon).

## 1. Bestandsaufnahme (Messung 2026-10-20, `origin/main` 83fc795d)

### 1.1 Emojis und Unicode-Symbole

Gezählt wurden Emoji- und Symbolzeichen (U+2190–27BF, U+2B00–2BFF, U+1F000–1FAFF) in `app/`,
ohne die vendorten Libraries.

| Bereich | Treffer | Bemerkung |
|---|---:|---|
| `app/templates` | 1572 | 394 Dateien insgesamt, Schwerpunkt siehe unten |
| `app/services` | 241 | gemischt: UI-Daten **und** SMS/Teams/Mail-Texte |
| `app/static/js` | 115 | Leaflet-`divIcon`s, dynamisch erzeugtes HTML |
| `app/routers` | 86 | HTML-Fragmente, Flash-Texte |
| `app/models` | 62 | Konstanten (z. B. `CROSS_MARKER_TYPE_ICON`) |
| `app/cli.py`, `core`, `main.py`, `config.py`, `middleware` | ~70 | Konsole/Logs, **nicht** UI |

Templates nach Modul: `incident` 249, `incident_major` 227, `admin` 183, `objekt` 153,
Wurzel (`base.html`, `index.html`, `hilfe.html` …) 87, `gateway` 62, `pdf` 40,
`foerderstrecke` 33, `breathing` 23, `archive` 19, `probenplanung` 16, `termin` 16,
`weather` 14, `katplan` 12, `nachschlagewerke` 12, `einheit` 12, Rest < 10.

Am häufigsten betroffene Dateien: `base.html` (61), `admin/_layout.html` (58),
`foerderstrecke/wizard.html` (56), `objekt/infoscreen_alarm.html` (54),
`incident_major/karte.html` (42), `incident/board.html` (39),
`incident_major/_lage_layout.html` (36), `admin/device_tokens.html` (36),
`incident/_dashboard_body.html` (35), `breathing/board.html` (31).

216 verschiedene Zeichen. Die häufigsten: `→` 489, `⚠` 131, `✓` 103, `📍` 56, `←` 54,
`🖨` 53, `📄` 41, `✕` 39, `🚒` 38, `📋` 33, `✨` 28, `✗` 27, `🗺` 22, `✅` 21, `👥` 20,
`🚨` 19, `↗` 19, `↔` 19, `●` 19, `🔗` 18, `☰` 18, `ℹ` 17, `🔴` 17, `🔥` 17.

Sonderfälle:
- **Typografische Pfeile** (`→`, `←`, `↔`) stehen zum großen Teil im Fließtext
  („A → B“, „Zurück“). Sie bleiben Text. Ersetzt werden nur Pfeile, die als Bedienelement-Icon
  dienen, also Buttons, Links mit Pfeil-Glyphe und CSS-`content` (`app.css`, `.objekt-viewer__back::before`).
- **Statuspunkte** (`🔴 🟢 🟡 🟠 ⚪ ⚫ ●`) tragen fachliche Statusinformation. Sie werden
  **kein** Tabler-Icon, sondern eine zentrale `status-dot`-Komponente mit Farb-Token und
  Textalternative (siehe 4.6).
- **`<option>`-Texte**: 22 Stellen mit Emoji. SVG ist in `<option>` nicht möglich. Das Emoji
  wird ersatzlos entfernt, ein Icon erscheint nur außerhalb des Selects, falls nötig.
- **CSS-Glyphen**: `.ec-chip-toggle … ::before { content: "\2713" }` und
  `.objekt-viewer__back::before`. Diese werden auf `mask-image` mit dem Tabler-SVG umgestellt
  oder durch ein Icon im Markup ersetzt.

### 1.2 Bestehende Icon-Lösungen

| Lösung | Ort | Umgang |
|---|---|---|
| Emoji/Unicode im Markup | überall | migrieren |
| Inline-SVG-Makro `icon()` (15 eigene Icons, Strich 1.75) | `app/templates/public/_icons.html` | auf zentrale Komponente umstellen, danach löschen |
| Inline-SVGs Wetter (Sparklines, Kompass) | `weather/infoscreen.html` (7), `_station_sparkline.html`, `_data_section.html`, `incident_major/_weather_panel.html` | **Diagramme, keine Icons**: bleiben; nur echte Icon-SVGs darin ersetzen |
| Server-Diagramme | `app/services/chart_svg.py`, `app/utils/barcode.py` | keine Icons, bleiben |
| Inline-SVG in `incident_major/karte.html` (4), `probenplanung/public_plan.html` (3), `_detail_sekundaeraktionen.html`, `breathing/board.html`, `routers/ui_settings.py` | einzeln prüfen | Icons ersetzen, Grafiken behalten |
| Noto-Emoji-Subset als PDF-Font | `pdf/_emoji_font_face.html`, `static/fonts/noto-emoji-pdf-subset.ttf` | nach PDF-Umstellung entfernen |
| Material Symbols (CSS-Rest) | `tailwind.input.css` Z. 136–147 `.material-symbols-outlined` | toter Code, laut Kommentar nicht referenziert, entfernen |
| Leaflet `L.divIcon` mit Emoji/HTML | 56 Aufrufe: `einsatz_info.js`, `lagefuehrung.js`, `objekt_karte.js`, `einheit_karte.js`, `stats_karte.js`, `wasserstellen_admin.js`, `incident_major/karte.html`, `karte_druck.html`, `lagefuehrung_druck.html`, `infoscreen_alarm.html`, `public/alarm_summary.html`, `foerderstrecke/wizard.html` | migrieren (Abschnitt 4.5) |
| Hydranten-Icons per CSS | `app.css` / `einsatz_info.js` (`WASSERSTELLE_ICON_KAT`: ueberflur/unterflur/loeschwasser) | bleibt unverändert (Feuerwehrsymbol) |
| Objekt-Kartensymbole (Text-Boxen „BMZ“, „FSD“, Dreiecke, Bild-Upload) | `objekt_symbol_service.py`, `objekt_karte.js` | **fachliche Planzeichen**, bleiben unverändert in eigener Logik |
| Leaflet-/Geoman-Standard-Assets | `static/img/leaflet`, `leaflet-geoman.min.css` | Vendor, unverändert |
| Statische Karten-PNGs | `staticmap_service.py` (CircleMarker) | keine Icons, bleiben |

### 1.3 In Datenbank oder Konstanten gespeicherte Emojis

| Feld/Konstante | Art | Bewertung |
|---|---|---|
| `ObjektAuswahl.icon` (String 40, Typen kontaktart/dokumentart/piktogramm) | frei pflegbar je Org, wird dem Label vorangestellt (`objekt.py:502`) | **frei wählbares Symbol** → neues Feld `icon_name` (Tabler), Altwert als Fallback-Text, siehe Phase 10 |
| `MerkmalKatalog.icon` (String 40) | frei pflegbar je Org | wie oben |
| `CROSS_MARKER_TYPE_ICON` (`major_incident.py:1030`) | Code-Konstante, Schlüssel `marker_type` | **fachlich**, keine Migration nötig, nur Mapping im Code |
| `katplan_typen.py` `icon=` | Code-Konstante | fachlich, im Code umstellen |
| `weather_service.py` Wettersymbole | Code, abgeleitet aus Wettercode | im Code auf `cloud-rain`, `sun` … umstellen |
| `site_pages.py` Landing-Features | Code | im Code umstellen |
| `GefahrenKatalog.piktogramm_typ` | Code als Schlüssel, Rendering über feste Palette | unverändert (GHS-/Gefahrenpiktogramme sind Normzeichen) |

Vor Phase 10 läuft eine Abfrage auf Produktion, die belegt, welche Werte tatsächlich in
`objekt_auswahl.icon` und `merkmal_katalog.icon` stehen (alle Orgs, `GROUP BY icon`).

### 1.4 Nicht betroffen (bewusst ausgenommen)

Emojis in **externen Kanälen** bleiben Text, weil dort kein SVG möglich bzw. sinnvoll ist:
SMS (`sms_*`, `alarm_outbox`), Teams-Karten (`teams_card.py`, `road_closure_notify_service.py`,
`ui_teams_bot.py`), E-Mails (`templates/email`, `templates/mailing`), MCP-Antworttexte,
Push-Notification-Titel, Log-Meldungen, `cli.py`. Diese Dateien landen auf der Allowlist
des Wächter-Tests (Abschnitt 5). Die native Android-Widget-Oberfläche
(`Einsatzcockpit-Android`) ist nicht Teil dieses Plans; die Capacitor-WebView nutzt das neue System automatisch.

## 2. Zuordnung Emoji → Icon (Entwurf)

Die verbindliche, vollständige Tabelle erzeugt Phase 0 als `docs/konzepte/icon-inventar.md`
(alle 216 Zeichen mit Fundstellen und Ziel). Jeder Name wird dort gegen die gepinnte
Tabler-Version geprüft. Entwurf für die häufigsten:

| Zeichen | Bedeutung im ECP | Ziel-Icon |
|---|---|---|
| ⚠ | Warnung/Hinweis | `alert-triangle` |
| ✓ ✅ ✔ ☑ | erledigt/bestätigt | `check` / `circle-check` |
| ✕ ✗ ✖ ❌ | schließen/ablehnen | `x` / `circle-x` |
| 📍 | Ort/Einsatzstelle | `map-pin` |
| 🗺 | Karte | `map` |
| 🖨 | Drucken | `printer` |
| 📄 📜 🧾 | Dokument/PDF | `file-text` |
| 📋 | Auftrag/Liste | `clipboard-list` |
| 🚒 | Fahrzeug/Einheit | `firetruck` |
| 🚑 | Rettung | `ambulance` |
| 🚁 | Hubschrauber/Drohne | `helicopter` / `drone` |
| 🚨 📢 📣 | Alarm/Durchsage | `urgent` / `speakerphone` |
| 🔥 | Brand | `flame` |
| 💧 🌊 | Wasser/Überflutung | `droplet` / `ripple` |
| 🚰 | Hydrant/Wasserstelle | bleibt (Feuerwehrsymbol) |
| 🫁 | Atemschutz | `lungs` |
| 👥 👤 | Personen/Person | `users` / `user` |
| 📞 📱 📲 | Telefon/Mobil | `phone` / `device-mobile` |
| 📻 📡 📟 | Funk/Antenne/Pager | `radio` / `antenna` / `device-mobile-message` |
| ✉ 📧 📨 📥 | Mail/Posteingang | `mail` / `inbox` |
| 🔗 ↗ | Link/extern | `link` / `external-link` |
| ☰ | Menü | `menu-2` |
| ℹ | Info | `info-circle` |
| 🔍 🔎 | Suche | `search` |
| ✏ ✎ 📝 | Bearbeiten/Notiz | `pencil` / `note` |
| 📷 📸 🖼 | Foto/Bild | `camera` / `photo` |
| 🎬 | Video | `movie` |
| 📊 📈 | Statistik/Pegel | `chart-bar` / `chart-line` |
| ⚙ 🛠 🔧 | Einstellungen/Werkzeug | `settings` / `tool` |
| ✨ | KI/automatisch | `sparkles` |
| 🔑 🔒 🔐 | Schlüssel/gesperrt | `key` / `lock` |
| 🏢 🏠 🏘 🏛 | Objekt/Gebäude | `building` / `home` / `buildings` |
| 📦 🧰 | Material | `package` / `box` |
| 🗑 | Löschen | `trash` |
| 📎 | Anhang | `paperclip` |
| ⏰ 🕐 ⏱ ⏳ | Zeit/Timer | `alarm` / `clock` / `stopwatch` / `hourglass` |
| 📅 🗓 | Termin | `calendar` |
| 🚧 ⛔ | Sperre | `barrier-block` / `ban` |
| ⚡ 🔌 | Strom | `bolt` / `plug` |
| ☣ | Gefahrstoff | `biohazard` |
| 🌧 🌦 ⛈ ❄ ☀ 🌫 💨 | Wetter | `cloud-rain`, `cloud-storm`, `snowflake`, `sun`, `mist`, `wind` |
| 🎯 | Ziel/Auftragsziel | `target` |
| 💡 | Hinweis/Tipp | `bulb` |
| 🔄 ↻ ↺ 🔁 | aktualisieren | `refresh` / `rotate` |
| ▶ ⏸ ⏹ ⏺ | Player/Aufnahme | `player-play`, `player-pause`, `player-stop`, `player-record` |
| ▼ ▾ ▲ ▸ ⌄ | auf-/zuklappen | `chevron-down` / `chevron-up` / `chevron-right` |
| ⬇ ⬆ | Download/Upload | `download` / `upload` |
| 💾 | Speichern | `device-floppy` |
| 🧭 | Navigation | `compass` |
| 🌐 | Web/öffentlich | `world` |
| 🛣 | Route | `route` |
| 🔴 🟢 🟡 🟠 ⚪ ⚫ ● | Status | **kein Icon** → `status-dot` (4.6) |

### Feuerwehrsymbole bleiben unverändert (Entscheidung 2026-10-20)

Für fachspezifische Feuerwehrsymbole ohne passendes Tabler-Icon werden **keine eigenen SVGs**
gezeichnet. Ihre bisherige Darstellung bleibt, wie sie ist:
Hydranten-/Löschwasser-Icons per CSS (`WASSERSTELLE_ICON_KAT`), Objekt-Planzeichen
(`objekt_symbol_service.py`, inkl. `➜` für Zugänge), taktische Zeichen, GHS-/Gefahrenpiktogramme
sowie Emojis ohne Tabler-Entsprechung wie `🚰` (Hydrant), `🪵` (Verklausung), `🧱` (Damm/Deich).
Die verbleibenden Zeichen stehen mit Begründung auf der Ausnahmeliste des Wächter-Tests
(`tests/data/icon_emoji_ausnahmen.txt`). Wo Tabler ein passendes Icon hat (`firetruck`,
`flame`, `lungs`, `ambulance`, `fire-extinguisher` …), wird normal migriert.

## 3. Architekturentscheidungen

1. **Bezugsquelle**: npm-Paket `@tabler/icons` mit exakt gepinnter Version in
   `package.json` (`3.49.0`, veröffentlicht 2026-10-05).
   `scripts/vendor-js.mjs` wird um einen Icon-Schritt erweitert. Kopiert werden **nur die
   verwendeten** Outline-SVGs nach `app/static/icons/tabler/` plus `LICENSE`. `npm run vendor:check`
   in der CI prüft die Übereinstimmung wie bei den JS-Libs. Keine CDN-Zugriffe, kein Node auf
   dem Server.
2. **Eine Registry, drei Ausgaben**: `app/core/icons.py` lädt beim Start
   `app/static/icons/tabler/*.svg` und hält die
   SVG-Innenteile (`<path>…`) im Speicher. Daraus entstehen:
   - **Jinja**: globale Funktion/Makro `icon(name, size="md", cls="", label=None)` rendert
     **Inline-SVG**. Inline (statt Sprite mit `<use href>`) funktioniert in HTMX-Partials,
     WebSocket-Fragmenten, WeasyPrint-PDFs, Offline-Cache und unter der heutigen CSP ohne
     Sonderfälle.
   - **JS**: generiertes `app/static/js/icons.js` (`window.ecpIcon(name, opts)` liefert einen
     SVG-String) für Leaflet, `incident-board.js`, `lagefuehrung.js` usw. Erzeugt von
     `python -m app.cli icons-build` bzw. im Vendor-Schritt. Ein Test prüft, dass die Datei aktuell ist.
   - **CSS**: für die wenigen Pseudo-Element-Fälle `mask-image: url(/static/icons/…svg)`.
3. **Unbekannter Name** ist ein Fehler: In Tests/Dev wirft er eine Exception, in Prod loggt er
   eine Warnung und rendert das Fallback-Icon `help-square`, damit nie ein leeres Icon erscheint.
4. **Service Worker**: `icons.js` kommt in `CORE_PRECACHE` (`app/static/sw.js`). Inline-SVGs
   brauchen sonst keine Netzwerkzugriffe, wodurch die Offline-Fähigkeit automatisch gegeben ist.
5. **Größe/Strich**: Standard 24 px, `stroke-width="2"`. Varianten `xs` 16, `sm` 20,
   `md` 24 (Standard), `lg` 32 als CSS-Klassen `ecp-icon--xs/sm/lg`. Die Linienstärke bleibt
   in Viewbox-Einheiten bei 2 (Tabler-Standard), sodass kleine Varianten proportional dünner
   wirken. Eine eigene Strichstärke pro Aufruf ist nicht vorgesehen.

## 4. Komponenten im Detail

### 4.1 Jinja-Komponente

```jinja
{{ icon("printer") }}                              {# dekorativ, aria-hidden #}
{{ icon("alert-triangle", size="sm", cls="ecp-icon--warning") }}
{{ icon("x", label="Schließen") }}                 {# role="img" + <title> #}
```

Ausgabe: `<svg class="ecp-icon ecp-icon--sm …" width="20" height="20" viewBox="0 0 24 24"
fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
stroke-linejoin="round" aria-hidden="true" focusable="false">…</svg>`.
Registrierung als Jinja-Global in `app/core/templating.py`, damit kein `{% import %}` in
582 Templates nötig ist.

### 4.2 Farben und Tokens

Neue Tokens in `tailwind.input.css` (`:root`, alle über vorhandene Farbvariablen):
`--icon-default: currentColor`, `--icon-muted: var(--text-muted)`,
`--icon-danger: var(--danger)`, `--icon-warning: var(--yellow)`,
`--icon-success: var(--green)`, `--icon-info: var(--accent-light)`,
`--icon-on-primary: #fff`. Klassen `.ecp-icon--muted/danger/warning/success/info`.
Für Druck/PDF gibt es einen eigenen `@media print`-Block mit dunklen Varianten auf weißem Grund.
Standard bleibt `currentColor`, ein Icon erbt also die Textfarbe des Buttons. Das Theme ist
aktuell dunkel (`data-theme="dark"`); weil alles über Tokens läuft, ist ein späteres
helles Theme ohne Icon-Änderungen möglich.

### 4.3 Barrierefreiheit

- Dekorative Icons neben Text: `aria-hidden="true"`, kein Alt-Text.
- Reine Icon-Buttons: `aria-label` am Button **oder** `label=` am Icon. Ein Test prüft
  Buttons/Links, deren einziger Inhalt `icon(...)` ist.
- Bestehende `title`-Tooltips (`tooltips.js`) bleiben erhalten.
- Wird ein Emoji ersetzt, das bisher die einzige Bedeutungsträgerin war (z. B. `🔴` ohne Text),
  bekommt die Stelle eine sichtbare oder `sr-only`-Textalternative.

### 4.4 HTMX und JavaScript

- Partials rendern über dieselbe Jinja-Funktion, daher ist keine Sonderbehandlung nötig.
- JS-erzeugtes HTML (`incident-board.js`, `sortable-glue.js`, `lagefuehrung.js`,
  `einsatz_info.js`, `einheit_*.js`, `katplan_*.js`, `suche.js`, `media-*.js`) nutzt
  `ecpIcon()`. `icons.js` wird in `base.html` vor allen Modulen geladen.
- Offline-Outbox/IndexedDB-Ansichten (`einheit_outbox.js`, `objekt_offline_sync.js`)
  funktionieren mit dem precachten `icons.js` ohne Server.

### 4.5 Leaflet-Karten

- Gemeinsame Hilfsfunktion `ecpMarkerIcon(name, {color, size, badge})` in `icons.js`
  erzeugt ein `L.divIcon` mit runder/tropfenförmiger Marker-Hülle (CSS
  `.ecp-map-marker`) und dem Icon in Weiß. Größen 28/36 px, `iconAnchor` einheitlich.
- Status-Farbe des Markers kommt weiter aus der bestehenden Logik (z. B.
  `CROSS_MARKER_STATUS_COLOR`), nur das Symbol wechselt.
- Betroffen: Einsatzinfo/Route (`einsatz_info.js`, Startmarker `🚒`), Lageführung,
  GSL-Karte und Querschnittsmarker (`incident_major/karte.html`, `_cross_marker_panel.html`),
  Einheitenkarte, Objektkarte (nur Emoji-Anteile), Wasserstellen-Admin, Statistik-Karte,
  Infoscreen-Alarm, öffentliche Alarmzusammenfassung, Förderstrecke, Druckkarten.
- Cluster-Icons (`MarkerCluster.Default.css`) bleiben unverändert.

### 4.6 Statuspunkte

Neue Komponente `status_dot(status, label)` (Jinja + JS) mit Farben aus den vorhandenen
`--ampel-*`-Tokens. Damit sind Status unabhängig von der Emoji-Darstellung des Betriebssystems
(heute sieht `🟡` auf Windows, Android und iOS verschieden aus). Die Statuslogik bleibt
unverändert, es ändert sich nur die Darstellung.

### 4.7 PDF und Druck

- WeasyPrint rendert Inline-SVG zuverlässig. Alle PDF-Templates (`templates/pdf/*`,
  `druck_bericht.html`, `lagefuehrung_druck.html`, `karte_druck.html`, Förderstrecke,
  Katplan, Objektblatt) nutzen `icon()`. Der Noto-Emoji-Font-Face (`pdf/_emoji_font_face.html`)
  fällt danach weg; der Vorfall vom 2026-07-06 (leere `.notdef`-Kästchen) ist damit strukturell
  ausgeschlossen.
- Der xhtml2pdf-Fallback kann SVG nur eingeschränkt. Dort rendert `icon()` im PDF-Kontext
  (Flag `pdf_engine="xhtml2pdf"`) nichts bzw. den Textlabel-Fallback. Das ist akzeptabel,
  weil die Modernisierung Phase 5 den Fallback ohnehin entfernt.
- Browser-Druck (`@media print`): Icons in Schwarz/Grau über die Print-Tokens.
- Ein Test rendert jedes PDF-Template mit WeasyPrint und prüft, dass kein Emoji und kein
  `.notdef` übrig ist (Text-Extraktion mit `pypdf`).

## 5. Automatisierte Prüfungen

Neue Datei `tests/test_icon_system.py`:

1. **Emoji-Wächter mit Baseline** (Muster wie `tests/test_async_routes_sync_db.py`):
   Er scannt `app/templates/**/*.html`, `app/static/js/*.js` (ohne vendorte Dateien) und
   UI-relevante Python-Dateien auf Emoji-Bereiche. Die erlaubte Restmenge steht pro Datei in
   `tests/data/icon_emoji_baseline.txt`. Neue Treffer lassen den Test fehlschlagen,
   und die Baseline darf nur schrumpfen. Typografische Pfeile `← → ↔` sind im Fließtext erlaubt.
   Die Allowlist umfasst die externen Kanäle aus 1.4.
2. **Keine fremden Icon-Bibliotheken**: Treffer auf `material-symbols`, `font-awesome`,
   `fa-`, `bootstrap-icons`, `lucide`, `heroicons`, `ionicons` oder auf CDN-URLs
   (`unpkg`, `jsdelivr`, `cdnjs`, `fonts.googleapis`) in Templates/JS/CSS lassen den Test fehlschlagen.
3. **Keine leeren Icons**: Alle `icon("…")`- und `ecpIcon('…')`-Aufrufe werden per Regex/AST
   gesammelt. Jeder Name muss in der Registry existieren, jedes SVG muss gültig sein
   (parsebar, `viewBox="0 0 24 24"`, mindestens ein Pfad).
4. **Vendor-Abgleich**: `icons.js` und `app/static/icons/tabler/` entsprechen der gepinnten
   Version und der Liste der verwendeten Icons (`npm run vendor:check` in der CI).
5. **Ausnahmeliste**: Jede Zeile in `tests/data/icon_emoji_ausnahmen.txt` (Zeichen + Begründung)
   wird noch verwendet, sonst muss sie raus.
6. **A11y**: Icon-only-Buttons ohne `aria-label`/`label=` lassen den Test fehlschlagen.
7. **PDF**: siehe 4.7.
8. JS-Test `tests/js/icons.test.mjs`: `ecpIcon` liefert für bekannte Namen SVG und für
   unbekannte das Fallback-Icon, außerdem prüft er `ecpMarkerIcon`.

Regeln dazu in `CLAUDE.md` („UI-Icons nur über `icon()`/`ecpIcon()`, keine Emojis im UI“).
Die Entwicklerdoku kommt nach `docs/wiki` (Icon-Katalog-Seite mit allen verfügbaren Namen).

## 6. Phasen (je Phase ein PR, Codex-Pakete klein halten)

| Phase | Inhalt | Ergebnis |
|---|---|---|
| **P0 Inventar** | Skript `scripts/icon_inventar.py` (Fundstellen je Zeichen mit Kontext: Markup, `<option>`, JS-String, Python, extern), generiert `docs/konzepte/icon-inventar.md` inkl. Ziel-Icon-Spalte; Tabler-Namen gegen gepinnte Version geprüft; Ausnahmeliste Feuerwehrsymbole final; Prod-Abfrage `objekt_auswahl.icon`/`merkmal_katalog.icon` | vollständige Migrationsübersicht |
| **P1 Fundament** | `@tabler/icons` pinnen + Vendor-Schritt, `app/core/icons.py`, Jinja-Global `icon()`, `status_dot()`, `icons.js` + `ecpMarkerIcon`, CSS-Klassen/Tokens, SW-Precache, Fallback-Icon, Tests 3–5 + 8, Wiki-Katalogseite | System nutzbar, noch nichts migriert |
| **P2 Wächter** | Test 1, 2, 6 mit Baseline aus Ist-Stand, CLAUDE.md-Regel | keine neuen Emojis ab jetzt |
| **P3 Rahmen** | `base.html`, `admin/_layout.html`, Navigation, `index.html`, `hilfe.html`, Flash/Toasts, gemeinsame Buttons (Drucken, Schließen, Bearbeiten, Löschen) | einheitliche Grundbedienelemente |
| **P4 Einsatzboard** | `incident/*` (board, `_task_card`, `_message_card`, `_message_modal`, `_dashboard_body`, `lagefuehrung`, `funkjournal`, `info`), `incident-board.js`, `sortable-glue.js`, `breathing/*`; mobil ≤ 760 px prüfen | Board fertig |
| **P5 GSL** | `incident_major/*` ohne Karte, `einheit/*`, `gsl`, `lage_board.js`, Einheitenmodus | GSL fertig |
| **P6 Karten** | alle `L.divIcon`-Stellen (4.5), `CROSS_MARKER_TYPE_ICON` → Icon-Namen, Förderstrecke | Karten fertig |
| **P7 Fachmodule** | `objekt/*` (inkl. `objekt_service.py`), `katplan/*` + `katplan_typen.py`, `gateway/*`, `archive`, `termin`, `probenplanung`, `verleih`, `nachschlagewerke`, `annotate`, `media`, `stats`, `fahrtenbuch`, restliches `admin/*` (z. B. `device_tokens.html`) | Innenbereich fertig |
| **P8 Öffentlich/Infoscreen** | `public/*` (altes `_icons.html` ablösen, Strich 1.75 → 2), Landing (`site_pages.py`), `weather/*` + `weather_service.py`, `objekt/infoscreen_alarm.html`, Road-Closure-Infoscreen, Probe-Appell | öffentliche Seiten fertig |
| **P9 PDF/Druck** | alle PDF- und Druck-Templates, xhtml2pdf-Verhalten, PDF-Test (4.7) | PDF fertig |
| **P10 DB-Felder** | Migration: `icon_name` (String 64) an `objekt_auswahl` und `merkmal_katalog`, Datenmigration bekannter Emojis → Namen (Mapping aus P0), unbekannte Werte bleiben in `icon` als Legacy-Anzeige; Admin-Formular mit Icon-Picker (Auswahl aus Registry statt Freitext); `label`-Property zeigt künftig Icon + Name ohne Emoji-Präfix; MCP-Werkzeuge liefern `icon_name` | DB-Emojis migriert |
| **P11 Aufräumen** | Noto-Font + `_emoji_font_face.html`, `public/_icons.html`, Material-Symbols-CSS, CSS-Glyphen entfernen; Baseline auf 0 (bis auf begründete Ausnahmen); Legacy-`icon`-Spalte erst nach Prüfung leerer Restwerte droppen (eigene Migration, später Release) | alte Lösungen weg |

Jede Phase entfernt alte Lösungen nur dort, wo alle Verwendungen umgestellt sind. Globale
Altlasten werden erst in P11 gelöscht.

Je Phase vor Merge: `ruff`, `mypy`, `pytest -n auto`, `npm run test:js`,
`npm run vendor:check`, Tailwind-Build (`npm run build`, `app.css` mitcommitten).
Gestützt auf die Inventarliste werden die Codex-Aufträge pro Phase in 1–3 Pakete geteilt
(Richtwert ≤ 15 Templates je Paket).

## 7. Abnahme

| Kriterium | Nachweis |
|---|---|
| Einheitlich auf Android, iOS, Windows, Desktop | Inline-SVG statt System-Emoji; manueller Gerätetest mit Checkliste (Board, GSL, Karte, Einheitenmodus, Infoscreen, PDF) auf Android-App, iPad/iPhone Safari, Windows Edge/Chrome, Desktop Firefox |
| Vollständig offline | Keine externen URLs (Test 2), `icons.js` im SW-Precache, Offline-Test Einheitenmodus/Objekt-Offline |
| Keine leeren/fehlenden Icons | Test 3 + Fallback-Icon + Prod-Warnlog `einsatzleiter.icons` |
| Gleiche Symbole/Größen in vergleichbaren Elementen | Zentrales Mapping in `docs/konzepte/icon-inventar.md`, gemeinsame Button-Makros, Größenvarianten |
| Dark Mode, Karten, PDF korrekt | Token-Farben, Marker-Hülle mit Kontrast, PDF-Test 4.7; Playwright-Screenshots der Kernseiten (nur auf Anforderung lokal) |
| Bestehende Tests/CI grün | CI je PR, danach CI auf `main` bestätigen |
| Funktionen, Statuslogik, A11y erhalten | keine Änderungen an Status-Konstanten/-Logik; A11y-Test 6; `status_dot` mit Textalternative |

## 8. Risiken und offene Punkte

- **Seitengröße**: Inline-SVG kostet ca. 200–400 Byte pro Icon. Auf dem Board mit vielen
  Karten sollte nachgemessen werden. Falls nötig, kommt für Wiederholungs-Icons (Kartenaktionen)
  ein Sprite `<svg><symbol>` in `base.html` mit `<use href="#…">` dazu. Das wäre nur ein
  Rendermodus der gleichen Registry und kein Bruch im Template-API.
- **Emojis in Nutzerdaten** (Meldungstexte, Notizen, Funkjournal) bleiben unangetastet:
  Das sind Inhalte, keine UI. Der Wächter scannt nur Code.
- **Strichstärke öffentlich**: Die öffentlichen Seiten wechseln von 1.75 auf 2, das ist eine
  kleine optische Änderung.
- **Tabler-Namensänderungen** bei Major-Updates: Version exakt gepinnt, Dependabot-Updates
  laufen über `npm run vendor`, Test 3 schlägt bei verschwundenen Namen an.
- **xhtml2pdf**: Icons fehlen dort bewusst, Abhängigkeit zur Modernisierung Phase 5.
