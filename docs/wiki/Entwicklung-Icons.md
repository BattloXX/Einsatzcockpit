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

UI-Emojis und weitere Icon-Bibliotheken/CDNs sind ausgeschlossen; externe
Textkanäle (SMS/Teams/Mail) bleiben ausgenommen. P1 stellt das Fundament bereit,
bestehende Templates werden in späteren Phasen migriert.

Siehe [Plan](https://github.com/BattloXX/Einsatzcockpit/blob/main/docs/plans/icon-system-plan.md) und [Inventar](https://github.com/BattloXX/Einsatzcockpit/blob/main/docs/icon-inventar.md).
