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
| markup | 389 |
| python | 275 |
| kommentar | 208 |
| extern | 60 |
| js | 18 |
| option | 4 |
| css | 2 |

## Zuordnung

| Zeichen | Ziel | Treffer | migrieren* | Dateien (Auszug) |
|---|---|---:|---:|---|
| → | `=text` | 482 | 280 | `cli.py`, `config.py`, `core/crypto.py`, `core/dependencies.py` … (+166) |
| ⚠ | `alert-triangle` | 60 | 52 | `models/objekt.py`, `routers/ui_incident.py`, `routers/ui_infoscreen_alarm.py`, `routers/ui_lagefuehrung.py` … (+25) |
| ← | `=text` | 38 | 36 | `static/js/katplan_editor.js`, `templates/archive/detail.html`, `templates/auth/forgot.html`, `templates/auth/pin_login.html` … (+31) |
| ✓ | `check` | 21 | 11 | `cli.py`, `models/sms.py`, `routers/ui_termin.py`, `seed_data.py` … (+7) |
| ↔ | `=text` | 16 | 14 | `models/major_incident.py`, `models/master.py`, `models/objekt.py`, `routers/ui_major_incident.py` … (+11) |
| 🖨 | `printer` | 15 | 15 | `routers/ui_druck.py`, `templates/_print_dialog.html`, `templates/incident/lagefuehrung_druck.html`, `templates/incident_major/_cross_marker_druck.html` … (+9) |
| 🚒 | `firetruck` | 14 | 10 | `routers/ui_teams_bot.py`, `services/alarm_outbox.py`, `services/gefahrgut_service.py`, `services/objekt_service.py` … (+8) |
| ✗ | `x` | 12 | 0 | `cli.py`, `services/sms_inbox_service.py` |
| 📍 | `map-pin` | 12 | 12 | `models/major_incident.py`, `services/katplan_qs_service.py`, `services/katplan_typen.py`, `templates/_macros/address_picker.html` … (+6) |
| 🌧 | `cloud-rain` | 12 | 12 | `templates/objekt/infoscreen_alarm.html`, `templates/weather/_data_section.html`, `templates/weather/index.html` |
| ⇒ | `=text` | 11 | 4 | `config.py`, `models/master.py`, `routers/api_weather.py`, `routers/ui_settings.py` … (+4) |
| 📄 | `file-text` | 11 | 11 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `services/site_pages.py`, `static/js/rettungskarten_katalog.js` … (+6) |
| ↓ | `=text` | 10 | 10 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/fahrtenbuch/neu.html`, `templates/fahrtenbuch/verwaltung/korrektur.html` … (+6) |
| ● | `=status` | 10 | 10 | `templates/incident_major/karte.html`, `templates/katplan/_design.html`, `templates/pdf/foerderstrecke.html` |
| ⚡ | `bolt` | 9 | 9 | `models/major_incident.py`, `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py` … (+3) |
| 🔥 | `flame` | 9 | 8 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `templates/pdf/_emoji_font_face.html` … (+1) |
| ❄ | `snowflake` | 7 | 7 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| ↑ | `=text` | 7 | 7 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/incident_major/_funkjournal_rows.html`, `templates/incident_major/druck_bericht.html` … (+3) |
| ✅ | `circle-check` | 7 | 5 | `services/katplan_typen.py`, `services/road_closure_notify_service.py`, `templates/incident_major/public_danke.html`, `templates/nachschlagewerke/_rettungskarten_result.html` … (+1) |
| ➜ | `=fw` | 6 | 6 | `services/objekt_symbol_service.py`, `static/js/lagefuehrung.js`, `static/js/objekt_karte.js` |
| 📞 | `phone` | 6 | 5 | `templates/incident_major/public_report.html`, `templates/incident_major/public_sms_error.html`, `templates/incident_major/public_verify.html`, `templates/pdf/_emoji_font_face.html` … (+2) |
| 🌦 | `cloud-rain` | 6 | 6 | `templates/objekt/infoscreen_alarm.html`, `templates/objekt/infoscreen_verwaltung.html`, `templates/weather/_data_section.html`, `templates/weather/index.html` |
| 💧 | `droplet` | 5 | 4 | `models/major_incident.py`, `services/objekt_service.py`, `templates/foerderstrecke/wizard.html`, `templates/katplan/_links_anzeige.html` … (+1) |
| 🚧 | `barrier-block` | 5 | 2 | `models/major_incident.py`, `services/road_closure_notify_service.py`, `templates/incident_major/public_report.html` |
| ☀ | `sun` | 5 | 5 | `models/objekt.py`, `services/objekt_service.py`, `templates/objekt/infoscreen_alarm.html`, `templates/weather/_data_section.html` |
| 📋 | `clipboard-list` | 5 | 5 | `services/incident_service.py`, `services/site_pages.py`, `templates/incident_major/druck.html`, `templates/objekt/infoscreen_alarm.html` |
| 🔗 | `link` | 5 | 5 | `services/katplan_qs_service.py`, `static/js/nachschlagewerke.js`, `templates/katplan/_anhaenge_anzeige.html`, `templates/nachschlagewerke/_rettungskarten_result.html` … (+1) |
| 🗺 | `map` | 5 | 4 | `services/teams_card.py`, `templates/objekt/infoscreen_alarm.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/lagebericht.html` … (+1) |
| ⛈ | `cloud-storm` | 5 | 5 | `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| ☣ | `biohazard` | 4 | 4 | `models/major_incident.py`, `templates/nachschlagewerke/gefahrgut.html`, `templates/nachschlagewerke/gefahrgut_detail.html`, `templates/nachschlagewerke/start.html` |
| 🏢 | `building` | 4 | 4 | `services/katplan_typen.py`, `templates/incident/lagefuehrung_druck.html`, `templates/katplan/_links_anzeige.html`, `templates/objekt/infoscreen_alarm.html` |
| 🎬 | `movie` | 4 | 4 | `templates/media/gallery.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/incident_report.html` |
| 🌊 | `ripple` | 3 | 3 | `models/major_incident.py`, `services/weather_service.py` |
| 🚨 | `urgent` | 3 | 2 | `routers/api_v1.py`, `services/teams_card.py`, `templates/public/alarm_summary.html` |
| 👤 | `user` | 3 | 3 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `templates/katplan/_links_anzeige.html` |
| 📎 | `paperclip` | 3 | 2 | `templates/katplan/_baum.html`, `templates/mailing/campaign_detail.html`, `templates/pdf/incident_report.html` |
| ▶ | `player-play` | 3 | 3 | `templates/media/gallery.html`, `templates/weather/index.html` |
| 🚑 | `ambulance` | 3 | 3 | `templates/nachschlagewerke/rettungskarten.html`, `templates/nachschlagewerke/start.html`, `templates/objekt/infoscreen_alarm.html` |
| 🌨 | `cloud-snow` | 3 | 3 | `templates/objekt/infoscreen_alarm.html` |
| 🏷 | `tag` | 3 | 2 | `templates/pdf/_emoji_font_face.html`, `templates/pdf/lagebericht.html`, `templates/pdf/objektblatt.html` |
| 💥 | `bomb` | 2 | 2 | `models/objekt.py`, `services/objekt_service.py` |
| 🧪 | `flask` | 2 | 2 | `models/objekt.py`, `services/objekt_service.py` |
| 🔑 | `key` | 2 | 2 | `services/objekt_service.py`, `templates/objekt/infoscreen_alarm.html` |
| 🅿 | `parking` | 2 | 2 | `services/objekt_service.py`, `templates/pdf/_emoji_font_face.html` |
| 📨 | `mail-forward` | 2 | 1 | `services/site_pages.py`, `templates/mailing/lists_index.html` |
| 🖥 | `device-desktop` | 2 | 0 | `services/teams_card.py` |
| ② | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| ① | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🎯 | `target` | 2 | 1 | `templates/foerderstrecke/wizard.html`, `templates/pdf/foerderstrecke.html` |
| ✕ | `x` | 2 | 2 | `templates/incident_major/_journal_entry_druck.html`, `templates/incident_major/_lagedokument_druck.html` |
| △ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| ◇ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| 📱 | `device-mobile` | 2 | 2 | `templates/incident_major/public_report.html`, `templates/incident_major/public_verify.html` |
| 📷 | `camera` | 2 | 2 | `templates/incident_major/public_report.html`, `templates/verleih/_ausleihe_detail.html` |
| 🔎 | `zoom-in` | 2 | 2 | `templates/nachschlagewerke/rettungskarten.html`, `templates/suche/_treffer.html` |
| ❌ | `circle-x` | 2 | 2 | `templates/objekt/infoscreen_alarm.html` |
| 🌫 | `mist` | 2 | 2 | `templates/objekt/infoscreen_alarm.html` |
| 🌡 | `temperature` | 2 | 2 | `templates/objekt/infoscreen_alarm.html` |
| 🏘 | `buildings` | 2 | 2 | `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| 🚪 | `door` | 2 | 2 | `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| 🔒 | `lock` | 2 | 2 | `templates/verleih/_ausleihe_detail.html`, `templates/verleih/_ausleihe_form.html` |
| ⏸ | `player-pause` | 2 | 2 | `templates/weather/index.html` |
| 🚗 | `car` | 1 | 1 | `middleware/csrf.py` |
| ⛰ | `mountain` | 1 | 1 | `models/major_incident.py` |
| 🪨 | `=fw` | 1 | 1 | `models/major_incident.py` |
| 🌲 | `tree` | 1 | 1 | `models/major_incident.py` |
| 🏚 | `home-x` | 1 | 1 | `models/major_incident.py` |
| 🧱 | `=fw` | 1 | 1 | `models/major_incident.py` |
| 🌉 | `=fw` | 1 | 1 | `models/major_incident.py` |
| 📈 | `chart-line` | 1 | 1 | `models/major_incident.py` |
| 🪵 | `=fw` | 1 | 1 | `models/major_incident.py` |
| ⇄ | `=text` | 1 | 0 | `models/objekt.py` |
| ✨ | `sparkles` | 1 | 1 | `routers/ui_incident.py` |
| 📩 | `mail-opened` | 1 | 1 | `services/incident_service.py` |
| ☎ | `phone` | 1 | 1 | `services/katplan_qs_service.py` |
| ❔ | `help-circle` | 1 | 1 | `services/katplan_qs_service.py` |
| ⏰ | `alarm` | 1 | 1 | `services/katplan_qs_service.py` |
| ⌛ | `hourglass` | 1 | 1 | `services/katplan_qs_service.py` |
| 🤖 | `robot` | 1 | 1 | `services/katplan_qs_service.py` |
| 🔀 | `arrows-shuffle` | 1 | 1 | `services/katplan_typen.py` |
| 📦 | `package` | 1 | 1 | `services/katplan_typen.py` |
| 📕 | `book` | 1 | 1 | `services/objekt_service.py` |
| 📻 | `radio` | 1 | 1 | `services/objekt_service.py` |
| 🛗 | `elevator` | 1 | 1 | `services/objekt_service.py` |
| 🚻 | `users-group` | 1 | 1 | `services/objekt_service.py` |
| 🌀 | `=fw` | 1 | 1 | `services/objekt_service.py` |
| ✏ | `pencil` | 1 | 0 | `services/road_closure_notify_service.py` |
| ⏩ | `player-track-next` | 1 | 0 | `services/road_closure_notify_service.py` |
| 🔁 | `repeat` | 1 | 0 | `services/road_closure_notify_service.py` |
| ↩ | `arrow-back-up` | 1 | 0 | `services/road_closure_notify_service.py` |
| 🫁 | `lungs` | 1 | 1 | `services/site_pages.py` |
| 👥 | `users` | 1 | 1 | `services/site_pages.py` |
| 🧊 | `snowflake` | 1 | 1 | `services/weather_service.py` |
| 💻 | `device-laptop` | 1 | 1 | `templates/_print_dialog.html` |
| 🟢 | `=status` | 1 | 1 | `templates/_print_dialog.html` |
| 🔴 | `=status` | 1 | 1 | `templates/_print_dialog.html` |
| 📢 | `speakerphone` | 1 | 1 | `templates/incident/lagefuehrung_druck.html` |
| ▣ | `=fw` | 1 | 1 | `templates/incident_major/karte.html` |
| ▭ | `square` | 1 | 1 | `templates/incident_major/karte.html` |
| 🛡 | `shield` | 1 | 1 | `templates/incident_major/public_report.html` |
| ✈ | `plane` | 1 | 1 | `templates/incident_major/public_report.html` |
| 📑 | `files` | 1 | 1 | `templates/katplan/_anhaenge_anzeige.html` |
| 🏛 | `building-bank` | 1 | 1 | `templates/katplan/_links_anzeige.html` |
| 🖼 | `photo` | 1 | 1 | `templates/media/gallery.html` |
| 📚 | `books` | 1 | 1 | `templates/nachschlagewerke/start.html` |
| 🙋 | `hand-stop` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| 🏠 | `home` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| 🌤 | `sun-low` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| ⛅ | `cloud` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| ☁ | `cloud` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| 📺 | `device-tv` | 1 | 1 | `templates/objekt/infoscreen_verwaltung.html` |
| ▬ | `=fw` | 1 | 1 | `templates/pdf/foerderstrecke.html` |
| ↳ | `=text` | 1 | 1 | `templates/pdf/incident_report.html` |
| ☐ | `square` | 1 | 1 | `templates/pdf/katplan.html` |
| 📜 | `history` | 1 | 1 | `templates/pdf/lagebericht.html` |
| 🚰 | `fire-hydrant` | 1 | 1 | `templates/public/alarm_summary.html` |
| 👁 | `eye` | 1 | 1 | `templates/public/page.html` |
| ⬇ | `download` | 1 | 1 | `templates/stats/fahrtenbuch.html` |
| ⌄ | `chevron-down` | 1 | 1 | `templates/verleih/_ausleihe_form.html` |
| 💨 | `wind` | 1 | 1 | `templates/weather/_data_section.html` |
| 🌬 | `wind` | 1 | 1 | `templates/weather/index.html` |
| ▸ | `chevron-right` | 1 | 1 | `templates/weather/index.html` |

\* ohne Kommentare und externe Kanäle.

## Dateien mit den meisten migrierbaren Treffern

| Datei | Treffer |
|---|---:|
| `app/templates/objekt/infoscreen_alarm.html` | 52 |
| `app/services/objekt_service.py` | 18 |
| `app/templates/weather/_data_section.html` | 11 |
| `app/models/major_incident.py` | 11 |
| `app/services/weather_service.py` | 11 |
| `app/templates/pdf/objektblatt.html` | 10 |
| `app/templates/incident_major/public_report.html` | 9 |
| `app/services/katplan_qs_service.py` | 9 |
| `app/models/objekt.py` | 8 |
| `app/services/katplan_typen.py` | 8 |
| `app/templates/weather/index.html` | 8 |
| `app/templates/pdf/incident_report.html` | 7 |
| `app/templates/incident_major/karte.html` | 7 |
| `app/templates/incident/lagefuehrung_druck.html` | 6 |
| `app/templates/pdf/lagebericht.html` | 6 |
| `app/services/site_pages.py` | 6 |
| `app/templates/pdf/_emoji_font_face.html` | 6 |
| `app/templates/incident_major/public_verify.html` | 5 |
| `app/templates/katplan/_links_anzeige.html` | 5 |
| `app/templates/pdf/foerderstrecke.html` | 5 |
| `app/templates/incident_major/druck_bericht.html` | 4 |
| `app/templates/katplan/_baum.html` | 4 |
| `app/templates/verleih/_ausleihe_detail.html` | 4 |
| `app/static/js/einsatz_info.js` | 4 |
| `app/templates/incident_major/_stellen_multi_druck.html` | 4 |
| `app/templates/_print_dialog.html` | 4 |
| `app/templates/incident_major/druck.html` | 4 |
| `app/templates/media/gallery.html` | 4 |
| `app/routers/ui_termin.py` | 3 |
| `app/templates/incident_major/_journal_entry_druck.html` | 3 |
| `app/templates/nachschlagewerke/start.html` | 3 |
| `app/routers/ui_incident.py` | 3 |
| `app/templates/incident_major/public_sms_error.html` | 3 |
| `app/templates/objekt/_gefahren.html` | 3 |
| `app/templates/stats/fahrtenbuch.html` | 3 |
| `app/services/incident_service.py` | 3 |
| `app/templates/katplan/_anhaenge_anzeige.html` | 3 |
| `app/templates/nachschlagewerke/_rettungskarten_result.html` | 3 |
| `app/templates/nachschlagewerke/rettungskarten.html` | 3 |
| `app/templates/nachschlagewerke/gefahrgut_detail.html` | 2 |
| `app/routers/ui_lagefuehrung.py` | 2 |
| `app/templates/incident_major/_cross_marker_druck.html` | 2 |
| `app/templates/incident_major/_site_druck.html` | 2 |
| `app/templates/incident_major/karte_druck.html` | 2 |
| `app/templates/objekt/_einsatz_inhalt.html` | 2 |
| `app/templates/pdf/troop_protocol.html` | 2 |
| `app/templates/public/alarm_summary.html` | 2 |
| `app/templates/incident_major/_lagedokument_druck.html` | 2 |
| `app/templates/objekt/infoscreen_verwaltung.html` | 2 |
| `app/templates/verleih/_ausleihe_form.html` | 2 |
| `app/seed_data.py` | 1 |
| `app/templates/fahrtenbuch/admin/fahrzeuge.html` | 1 |
| `app/templates/pdf/_troop_block.html` | 1 |
| `app/middleware/csrf.py` | 1 |
| `app/templates/nachschlagewerke/gefahrgut.html` | 1 |
| `app/templates/_macros/address_picker.html` | 1 |
| `app/templates/verleih/_ausleihe_card.html` | 1 |
| `app/routers/ui_infoscreen_alarm.py` | 1 |
| `app/static/js/lagefuehrung.js` | 1 |
| `app/routers/api_v1.py` | 1 |
