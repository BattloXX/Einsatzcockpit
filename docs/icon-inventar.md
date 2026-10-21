# Icon-Inventar

Automatisch erzeugt von `scripts/icon_inventar.py` (Tabler Icons 3.49.0).
Plan und Regeln: [plans/icon-system-plan.md](plans/icon-system-plan.md).

Ziel-Spalte: Tabler-Icon-Name, oder `=status` (Statuspunkt → `status_dot`), `=text`
(Typografie, bleibt), `=fw` (Feuerwehr-/Fachzeichen, bleibt unverändert).
Kontexte: `markup`, `js`, `python`, `css`, `option` (Emoji in `<option>` → entfernen),
`kommentar` (bleibt), `extern` (SMS/Teams/Mail/MCP/Logs, bleibt).

## Summen nach Kontext

| Kontext | Treffer |
|---|---:|
| python | 245 |
| kommentar | 208 |
| markup | 175 |
| extern | 60 |
| js | 16 |
| option | 4 |

## Zuordnung

| Zeichen | Ziel | Treffer | migrieren* | Dateien (Auszug) |
|---|---|---:|---:|---|
| → | `=text` | 477 | 275 | `cli.py`, `config.py`, `core/crypto.py`, `core/dependencies.py` … (+163) |
| ⚠ | `alert-triangle` | 29 | 21 | `models/objekt.py`, `routers/ui_incident.py`, `routers/ui_infoscreen_alarm.py`, `routers/ui_lagefuehrung.py` … (+11) |
| ← | `=text` | 29 | 27 | `static/js/katplan_editor.js`, `templates/archive/detail.html`, `templates/auth/forgot.html`, `templates/auth/pin_login.html` … (+23) |
| ↔ | `=text` | 16 | 14 | `models/major_incident.py`, `models/master.py`, `models/objekt.py`, `routers/ui_major_incident.py` … (+11) |
| ✓ | `check` | 14 | 4 | `cli.py`, `models/sms.py`, `routers/ui_termin.py`, `seed_data.py` … (+1) |
| ✗ | `x` | 12 | 0 | `cli.py`, `services/sms_inbox_service.py` |
| ⇒ | `=text` | 11 | 4 | `config.py`, `models/master.py`, `routers/api_weather.py`, `routers/ui_settings.py` … (+4) |
| ↓ | `=text` | 8 | 8 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/fahrtenbuch/neu.html`, `templates/fahrtenbuch/verwaltung/korrektur.html` … (+4) |
| 🔥 | `flame` | 6 | 5 | `models/objekt.py`, `services/objekt_service.py`, `templates/pdf/_emoji_font_face.html` |
| ➜ | `=fw` | 6 | 6 | `services/objekt_symbol_service.py`, `static/js/lagefuehrung.js`, `static/js/objekt_karte.js` |
| ● | `=status` | 6 | 6 | `templates/incident_major/karte.html` |
| ↑ | `=text` | 5 | 5 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/incident_major/_funkjournal_rows.html`, `templates/incident_major/druck_bericht.html` … (+1) |
| 🚒 | `firetruck` | 5 | 1 | `routers/ui_teams_bot.py`, `services/alarm_outbox.py`, `services/objekt_service.py`, `services/teams_card.py` … (+1) |
| 🚧 | `barrier-block` | 4 | 1 | `models/major_incident.py`, `services/road_closure_notify_service.py` |
| 💧 | `droplet` | 3 | 2 | `models/major_incident.py`, `services/objekt_service.py`, `templates/foerderstrecke/wizard.html` |
| ⚡ | `bolt` | 3 | 3 | `models/major_incident.py`, `models/objekt.py`, `services/objekt_service.py` |
| 📍 | `map-pin` | 3 | 3 | `models/major_incident.py`, `services/katplan_typen.py` |
| ☀ | `sun` | 3 | 3 | `models/objekt.py`, `services/objekt_service.py` |
| ✅ | `circle-check` | 3 | 1 | `services/katplan_typen.py`, `services/road_closure_notify_service.py` |
| 💥 | `bomb` | 2 | 2 | `models/objekt.py`, `services/objekt_service.py` |
| 🧪 | `flask` | 2 | 2 | `models/objekt.py`, `services/objekt_service.py` |
| ❄ | `snowflake` | 2 | 2 | `models/objekt.py`, `services/objekt_service.py` |
| 🚨 | `urgent` | 2 | 1 | `routers/api_v1.py`, `services/teams_card.py` |
| 📋 | `clipboard-list` | 2 | 2 | `services/incident_service.py` |
| 📄 | `file-text` | 2 | 2 | `services/katplan_typen.py`, `templates/pdf/_emoji_font_face.html` |
| 🅿 | `parking` | 2 | 2 | `services/objekt_service.py`, `templates/pdf/_emoji_font_face.html` |
| 🗺 | `map` | 2 | 1 | `services/teams_card.py`, `templates/pdf/_emoji_font_face.html` |
| 🖥 | `device-desktop` | 2 | 0 | `services/teams_card.py` |
| ② | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| ① | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| △ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| ◇ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| 🌊 | `ripple` | 1 | 1 | `models/major_incident.py` |
| ⛰ | `mountain` | 1 | 1 | `models/major_incident.py` |
| 🪨 | `=fw` | 1 | 1 | `models/major_incident.py` |
| 🌲 | `tree` | 1 | 1 | `models/major_incident.py` |
| 🏚 | `home-x` | 1 | 1 | `models/major_incident.py` |
| 🧱 | `=fw` | 1 | 1 | `models/major_incident.py` |
| 🌉 | `=fw` | 1 | 1 | `models/major_incident.py` |
| 📈 | `chart-line` | 1 | 1 | `models/major_incident.py` |
| ☣ | `biohazard` | 1 | 1 | `models/major_incident.py` |
| 🪵 | `=fw` | 1 | 1 | `models/major_incident.py` |
| ⇄ | `=text` | 1 | 0 | `models/objekt.py` |
| 📩 | `mail-opened` | 1 | 1 | `services/incident_service.py` |
| 🔀 | `arrows-shuffle` | 1 | 1 | `services/katplan_typen.py` |
| 📦 | `package` | 1 | 1 | `services/katplan_typen.py` |
| 👤 | `user` | 1 | 1 | `services/katplan_typen.py` |
| 🏢 | `building` | 1 | 1 | `services/katplan_typen.py` |
| 🔑 | `key` | 1 | 1 | `services/objekt_service.py` |
| 📕 | `book` | 1 | 1 | `services/objekt_service.py` |
| 📻 | `radio` | 1 | 1 | `services/objekt_service.py` |
| 🛗 | `elevator` | 1 | 1 | `services/objekt_service.py` |
| 🚻 | `users-group` | 1 | 1 | `services/objekt_service.py` |
| 🌀 | `=fw` | 1 | 1 | `services/objekt_service.py` |
| ✏ | `pencil` | 1 | 0 | `services/road_closure_notify_service.py` |
| ⏩ | `player-track-next` | 1 | 0 | `services/road_closure_notify_service.py` |
| 🔁 | `repeat` | 1 | 0 | `services/road_closure_notify_service.py` |
| ↩ | `arrow-back-up` | 1 | 0 | `services/road_closure_notify_service.py` |
| 🎯 | `target` | 1 | 0 | `templates/foerderstrecke/wizard.html` |
| ▣ | `=fw` | 1 | 1 | `templates/incident_major/karte.html` |
| ▭ | `square` | 1 | 1 | `templates/incident_major/karte.html` |
| 📎 | `paperclip` | 1 | 0 | `templates/mailing/campaign_detail.html` |
| 📨 | `mail-forward` | 1 | 0 | `templates/mailing/lists_index.html` |
| 📞 | `phone` | 1 | 0 | `templates/pdf/_emoji_font_face.html` |
| 🏷 | `tag` | 1 | 0 | `templates/pdf/_emoji_font_face.html` |
| 🏘 | `buildings` | 1 | 1 | `templates/pdf/_emoji_font_face.html` |
| 🚪 | `door` | 1 | 1 | `templates/pdf/_emoji_font_face.html` |
| 🎬 | `movie` | 1 | 1 | `templates/pdf/_emoji_font_face.html` |
| ▬ | `=fw` | 1 | 1 | `templates/pdf/foerderstrecke.html` |
| ↳ | `=text` | 1 | 1 | `templates/pdf/incident_report.html` |
| ↗ | `external-link` | 1 | 1 | `templates/weather/_data_section.html` |

\* ohne Kommentare und externe Kanäle.

## Dateien mit den meisten migrierbaren Treffern

| Datei | Treffer |
|---|---:|
| `app/services/objekt_service.py` | 18 |
| `app/models/major_incident.py` | 11 |
| `app/models/objekt.py` | 8 |
| `app/services/katplan_typen.py` | 8 |
| `app/templates/incident_major/karte.html` | 7 |
| `app/templates/pdf/_emoji_font_face.html` | 6 |
| `app/static/js/einsatz_info.js` | 4 |
| `app/routers/ui_termin.py` | 3 |
| `app/templates/objekt/_gefahren.html` | 3 |
| `app/services/incident_service.py` | 3 |
| `app/routers/ui_incident.py` | 2 |
| `app/routers/ui_lagefuehrung.py` | 2 |
| `app/templates/objekt/_einsatz_inhalt.html` | 2 |
| `app/templates/pdf/objektblatt.html` | 2 |
| `app/seed_data.py` | 1 |
| `app/routers/ui_infoscreen_alarm.py` | 1 |
| `app/static/js/lagefuehrung.js` | 1 |
| `app/templates/objekt/infoscreen_alarm.html` | 1 |
| `app/routers/api_v1.py` | 1 |
| `app/templates/weather/_data_section.html` | 1 |
