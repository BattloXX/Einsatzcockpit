# Icon-System

Tabler Outline wird lokal vendort. Verfügbare Namen stehen in
[`app/static/icons/tabler-auswahl.txt`](https://github.com/BattloXX/Einsatzcockpit/blob/main/app/static/icons/tabler-auswahl.txt).
Neue Namen dort ergänzen und `npm run vendor` ausführen; `npm run vendor:check`
prüft SVGs, Lizenz und das generierte `icons.js`. Diese Datei nicht von Hand ändern.

## Nutzung und Barrierefreiheit

```jinja
{{ icon("printer") }}
{{ icon("alert-triangle", size="sm", cls="ecp-icon--warning") }}
<button aria-label="Schließen">{{ icon("x") }}</button>
{{ icon("info-circle", label="Information") }}
```

Dekorative Icons sind für Screenreader verborgen. `label=` erzeugt `role="img"`,
`aria-label` und einen SVG-Titel. Icon-only-Buttons brauchen `aria-label` oder
`label=`. Die Helfer stehen auch in den weiteren Jinja-Environments zur Verfügung.

## Größen und Farben

`xs`, `sm`, `md` (Standard), `lg` entsprechen 16, 20, 24, 32 px.
Standardfarbe ist die geerbte Textfarbe. Farbklassen: `ecp-icon--muted`,
`ecp-icon--danger`, `ecp-icon--warning`, `ecp-icon--success`, `ecp-icon--info`,
`ecp-icon--on-primary`; sie verwenden die Theme-Tokens. Buttons und Navigation
verwenden `gap` für den Abstand zum Text.

## Statuspunkte

```jinja
{{ status_dot("gruen", "Einsatzbereit") }}
```

Statuswerte: `rot`, `orange`, `gelb`, `gruen`, `blau`, `grau`, `schwarz`, `weiss`.
Das Label liefert Textalternative und Tooltip. JS: `ecpStatusDot('gruen', 'Einsatzbereit')`.

## JavaScript und Karte

```javascript
ecpIcon('printer', {size: 'sm', cls: 'ecp-icon--muted', label: 'Drucken'});
ecpMarkerIcon('firetruck', {color: '#d42225', size: 36, cls: ''});
```

`ecpIcon` gibt einen SVG-String zurück. Unbekannte Namen verwenden `help-square`
und warnen einmal je Name; Python wirft mit `ICONS_STRICT=true` einen `KeyError`.
Unbekannte Größen oder Statuswerte sind Fehler.

`ecpMarkerIcon` liefert mit Leaflet ein `L.divIcon`, sonst `null`. Standard:
32 px, Farbe `#d42225`. Erlaubt sind Hexfarben und `var(--token)`; ungültige Farben
fallen auf den Standard zurück. `icons.js` wird im Service Worker precacht.

Weitere Optionen: `text` zeigt statt eines Icons ein (escaptes) Fachzeichen im
Marker, `border` einen Rahmen (nur einfache CSS-Werte). Der Marker ist ein Kreis
und mittig verankert – keine Pin-Anker (`[x, 2x]`) übernehmen.

## Weitere Helfer

- `modul_icon("archiv")` – einheitliches Icon je Modul (`MODUL_ICONS` in `app/core/icons.py`).
- `merkmal_icon(merkmal)` – Objektmerkmal: Tabler-Icon aus `icon_name`, sonst Altwert
  aus `icon`, sonst `tag`. In der Katalogpflege wird das Icon ausgewählt.
- PDF-Templates binden `pdf/_icon_styles.html` ein (Größen in pt). Der xhtml2pdf-Fallback
  entfernt System-Icons über `strip_icons_for_xhtml2pdf`.

## Fallstricke

- In Flex-Containern fällt das Leerzeichen zwischen Icon und Text weg – `gap` setzen.
- `x-text`/`textContent` nie mit SVG-Strings füllen; Icon statisch daneben, bei
  `x-show` mit `x-cloak`.
- `<option>` kann kein SVG – dort keine Icons.

## Ausnahmen

UI-Emojis und weitere Icon-Bibliotheken/CDNs sind ausgeschlossen. Bewusst als Emoji
bleiben: externe Textkanäle (SMS/Teams/Mail/Push), Gefahren-Piktogramme (fachliche
Information), Feuerwehr-Fachzeichen ohne Tabler-Entsprechung (Mure, Damm, Brücke,
Verklausung, Kartenlegenden, taktische Zeichen), Kompatibilitätsfelder in JSON-Ausgaben.
Jede verbliebene Stelle steht mit Begründung in `tests/data/icon_emoji_baseline.txt`
(Gründe: `AUSNAHME_GRUENDE` in `scripts/icon_inventar.py`).

Siehe [Plan](https://github.com/BattloXX/Einsatzcockpit/blob/main/docs/plans/icon-system-plan.md) und [Inventar](https://github.com/BattloXX/Einsatzcockpit/blob/main/docs/icon-inventar.md).
