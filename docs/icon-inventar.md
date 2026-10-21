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
| markup | 234 |
| kommentar | 208 |
| extern | 60 |
| js | 16 |
| option | 4 |
| css | 1 |

## Zuordnung

| Zeichen | Ziel | Treffer | migrieren* | Dateien (Auszug) |
|---|---|---:|---:|---|
| → | `=text` | 477 | 275 | `cli.py`, `config.py`, `core/crypto.py`, `core/dependencies.py` … (+163) |
| ⚠ | `alert-triangle` | 46 | 38 | `models/objekt.py`, `routers/ui_incident.py`, `routers/ui_infoscreen_alarm.py`, `routers/ui_lagefuehrung.py` … (+20) |
| ← | `=text` | 29 | 27 | `static/js/katplan_editor.js`, `templates/archive/detail.html`, `templates/auth/forgot.html`, `templates/auth/pin_login.html` … (+23) |
| ↔ | `=text` | 16 | 14 | `models/major_incident.py`, `models/master.py`, `models/objekt.py`, `routers/ui_major_incident.py` … (+11) |
| ✓ | `check` | 15 | 5 | `cli.py`, `models/sms.py`, `routers/ui_termin.py`, `seed_data.py` … (+2) |
| ✗ | `x` | 12 | 0 | `cli.py`, `services/sms_inbox_service.py` |
| ⇒ | `=text` | 11 | 4 | `config.py`, `models/master.py`, `routers/api_weather.py`, `routers/ui_settings.py` … (+4) |
| 🖨 | `printer` | 11 | 11 | `templates/incident/lagefuehrung_druck.html`, `templates/incident_major/_cross_marker_druck.html`, `templates/incident_major/_journal_entry_druck.html`, `templates/incident_major/_lagedokument_druck.html` … (+6) |
| ● | `=status` | 9 | 9 | `templates/incident_major/karte.html`, `templates/pdf/foerderstrecke.html` |
| ↓ | `=text` | 8 | 8 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/fahrtenbuch/neu.html`, `templates/fahrtenbuch/verwaltung/korrektur.html` … (+4) |
| 🚒 | `firetruck` | 8 | 4 | `routers/ui_teams_bot.py`, `services/alarm_outbox.py`, `services/objekt_service.py`, `services/teams_card.py` … (+4) |
| 🔥 | `flame` | 7 | 6 | `models/objekt.py`, `services/objekt_service.py`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| ➜ | `=fw` | 6 | 6 | `services/objekt_symbol_service.py`, `static/js/lagefuehrung.js`, `static/js/objekt_karte.js` |
| ⚡ | `bolt` | 5 | 5 | `models/major_incident.py`, `models/objekt.py`, `services/objekt_service.py`, `templates/incident_major/_journal_entry_druck.html` … (+1) |
| ↑ | `=text` | 5 | 5 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/incident_major/_funkjournal_rows.html`, `templates/incident_major/druck_bericht.html` … (+1) |
| 💧 | `droplet` | 4 | 3 | `models/major_incident.py`, `services/objekt_service.py`, `templates/foerderstrecke/wizard.html`, `templates/pdf/foerderstrecke.html` |
| 🚧 | `barrier-block` | 4 | 1 | `models/major_incident.py`, `services/road_closure_notify_service.py` |
| 📍 | `map-pin` | 4 | 4 | `models/major_incident.py`, `services/katplan_typen.py`, `templates/incident/lagefuehrung_druck.html` |
| 📄 | `file-text` | 4 | 4 | `services/katplan_typen.py`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/incident_report.html` |
| 🗺 | `map` | 4 | 3 | `services/teams_card.py`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/lagebericht.html`, `templates/pdf/objektblatt.html` |
| ☀ | `sun` | 3 | 3 | `models/objekt.py`, `services/objekt_service.py` |
| 📋 | `clipboard-list` | 3 | 3 | `services/incident_service.py`, `templates/incident_major/druck.html` |
| ✅ | `circle-check` | 3 | 1 | `services/katplan_typen.py`, `services/road_closure_notify_service.py` |
| 🏷 | `tag` | 3 | 2 | `templates/pdf/_emoji_font_face.html`, `templates/pdf/lagebericht.html`, `templates/pdf/objektblatt.html` |
| 🎬 | `movie` | 3 | 3 | `templates/pdf/_emoji_font_face.html`, `templates/pdf/incident_report.html` |
| 💥 | `bomb` | 2 | 2 | `models/objekt.py`, `services/objekt_service.py` |
| 🧪 | `flask` | 2 | 2 | `models/objekt.py`, `services/objekt_service.py` |
| ❄ | `snowflake` | 2 | 2 | `models/objekt.py`, `services/objekt_service.py` |
| 🚨 | `urgent` | 2 | 1 | `routers/api_v1.py`, `services/teams_card.py` |
| 🏢 | `building` | 2 | 2 | `services/katplan_typen.py`, `templates/incident/lagefuehrung_druck.html` |
| 🅿 | `parking` | 2 | 2 | `services/objekt_service.py`, `templates/pdf/_emoji_font_face.html` |
| 🖥 | `device-desktop` | 2 | 0 | `services/teams_card.py` |
| ② | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| ① | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🎯 | `target` | 2 | 1 | `templates/foerderstrecke/wizard.html`, `templates/pdf/foerderstrecke.html` |
| ✕ | `x` | 2 | 2 | `templates/incident_major/_journal_entry_druck.html`, `templates/incident_major/_lagedokument_druck.html` |
| △ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| ◇ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| 📎 | `paperclip` | 2 | 1 | `templates/mailing/campaign_detail.html`, `templates/pdf/incident_report.html` |
| 📞 | `phone` | 2 | 1 | `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| 🏘 | `buildings` | 2 | 2 | `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| 🚪 | `door` | 2 | 2 | `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
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
| 📢 | `speakerphone` | 1 | 1 | `templates/incident/lagefuehrung_druck.html` |
| ▣ | `=fw` | 1 | 1 | `templates/incident_major/karte.html` |
| ▭ | `square` | 1 | 1 | `templates/incident_major/karte.html` |
| 📨 | `mail-forward` | 1 | 0 | `templates/mailing/lists_index.html` |
| ▬ | `=fw` | 1 | 1 | `templates/pdf/foerderstrecke.html` |
| ↳ | `=text` | 1 | 1 | `templates/pdf/incident_report.html` |
| ☐ | `square` | 1 | 1 | `templates/pdf/katplan.html` |
| 📜 | `history` | 1 | 1 | `templates/pdf/lagebericht.html` |
| ↗ | `external-link` | 1 | 1 | `templates/weather/_data_section.html` |

\* ohne Kommentare und externe Kanäle.

## Dateien mit den meisten migrierbaren Treffern

| Datei | Treffer |
|---|---:|
| `app/services/objekt_service.py` | 18 |
| `app/models/major_incident.py` | 11 |
| `app/templates/pdf/objektblatt.html` | 10 |
| `app/models/objekt.py` | 8 |
| `app/services/katplan_typen.py` | 8 |
| `app/templates/pdf/incident_report.html` | 7 |
| `app/templates/incident_major/karte.html` | 7 |
| `app/templates/incident/lagefuehrung_druck.html` | 6 |
| `app/templates/pdf/lagebericht.html` | 6 |
| `app/templates/pdf/_emoji_font_face.html` | 6 |
| `app/templates/pdf/foerderstrecke.html` | 5 |
| `app/templates/incident_major/druck_bericht.html` | 4 |
| `app/static/js/einsatz_info.js` | 4 |
| `app/templates/incident_major/_stellen_multi_druck.html` | 4 |
| `app/templates/incident_major/druck.html` | 4 |
| `app/routers/ui_termin.py` | 3 |
| `app/templates/incident_major/_journal_entry_druck.html` | 3 |
| `app/templates/objekt/_gefahren.html` | 3 |
| `app/services/incident_service.py` | 3 |
| `app/routers/ui_incident.py` | 2 |
| `app/routers/ui_lagefuehrung.py` | 2 |
| `app/templates/incident_major/_cross_marker_druck.html` | 2 |
| `app/templates/incident_major/_site_druck.html` | 2 |
| `app/templates/incident_major/karte_druck.html` | 2 |
| `app/templates/objekt/_einsatz_inhalt.html` | 2 |
| `app/templates/pdf/troop_protocol.html` | 2 |
| `app/templates/incident_major/_lagedokument_druck.html` | 2 |
| `app/seed_data.py` | 1 |
| `app/templates/pdf/_troop_block.html` | 1 |
| `app/routers/ui_infoscreen_alarm.py` | 1 |
| `app/static/js/lagefuehrung.js` | 1 |
| `app/templates/objekt/infoscreen_alarm.html` | 1 |
| `app/routers/api_v1.py` | 1 |
| `app/templates/termin/druck.html` | 1 |
| `app/templates/pdf/katplan.html` | 1 |
| `app/templates/weather/_data_section.html` | 1 |
