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
| markup | 554 |
| python | 276 |
| kommentar | 208 |
| extern | 60 |
| js | 28 |
| option | 6 |
| css | 4 |

## Zuordnung

| Zeichen | Ziel | Treffer | migrieren* | Dateien (Auszug) |
|---|---|---:|---:|---|
| → | `=text` | 484 | 282 | `cli.py`, `config.py`, `core/crypto.py`, `core/dependencies.py` … (+167) |
| ⚠ | `alert-triangle` | 68 | 60 | `models/objekt.py`, `routers/ui_incident.py`, `routers/ui_infoscreen_alarm.py`, `routers/ui_lagefuehrung.py` … (+31) |
| ← | `=text` | 38 | 36 | `static/js/katplan_editor.js`, `templates/archive/detail.html`, `templates/auth/forgot.html`, `templates/auth/pin_login.html` … (+31) |
| ✓ | `check` | 35 | 25 | `cli.py`, `models/sms.py`, `routers/ui_archive.py`, `routers/ui_termin.py` … (+16) |
| 🖨 | `printer` | 28 | 28 | `routers/ui_druck.py`, `templates/_print_dialog.html`, `templates/archive/detail.html`, `templates/gateway/_druckhistorie.html` … (+17) |
| 📍 | `map-pin` | 20 | 20 | `models/major_incident.py`, `services/katplan_qs_service.py`, `services/katplan_typen.py`, `templates/_macros/address_picker.html` … (+10) |
| 📄 | `file-text` | 17 | 17 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `services/site_pages.py`, `static/js/rettungskarten_katalog.js` … (+9) |
| ✗ | `x` | 16 | 4 | `cli.py`, `services/sms_inbox_service.py`, `static/js/probe-appell.js`, `templates/probenplanung/teilnehmer_appell.html` |
| ↔ | `=text` | 16 | 14 | `models/major_incident.py`, `models/master.py`, `models/objekt.py`, `routers/ui_major_incident.py` … (+11) |
| 🚒 | `firetruck` | 15 | 11 | `routers/ui_teams_bot.py`, `services/alarm_outbox.py`, `services/gefahrgut_service.py`, `services/objekt_service.py` … (+9) |
| 💧 | `droplet` | 12 | 11 | `models/major_incident.py`, `services/objekt_service.py`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html` … (+2) |
| 🌧 | `cloud-rain` | 12 | 12 | `templates/objekt/infoscreen_alarm.html`, `templates/weather/_data_section.html`, `templates/weather/index.html` |
| ⇒ | `=text` | 11 | 4 | `config.py`, `models/master.py`, `routers/api_weather.py`, `routers/ui_settings.py` … (+4) |
| 🔥 | `flame` | 10 | 9 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `templates/gateway/druckregeln.html` … (+2) |
| ↓ | `=text` | 10 | 10 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/fahrtenbuch/neu.html`, `templates/fahrtenbuch/verwaltung/korrektur.html` … (+6) |
| 🎯 | `target` | 10 | 9 | `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html`, `templates/gateway/agent_detail.html`, `templates/gateway/druckregeln.html` … (+1) |
| ● | `=status` | 10 | 10 | `templates/incident_major/karte.html`, `templates/katplan/_design.html`, `templates/pdf/foerderstrecke.html` |
| ⚡ | `bolt` | 9 | 9 | `models/major_incident.py`, `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py` … (+3) |
| 🔗 | `link` | 9 | 9 | `services/katplan_qs_service.py`, `static/js/nachschlagewerke.js`, `templates/foerderstrecke/wizard.html`, `templates/gateway/agent_detail.html` … (+5) |
| ✅ | `circle-check` | 9 | 7 | `services/katplan_typen.py`, `services/road_closure_notify_service.py`, `templates/foerderstrecke/wizard.html`, `templates/gateway/_druckregel_simulation.html` … (+3) |
| ❄ | `snowflake` | 7 | 7 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| ↑ | `=text` | 7 | 7 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/incident_major/_funkjournal_rows.html`, `templates/incident_major/druck_bericht.html` … (+3) |
| 📋 | `clipboard-list` | 7 | 7 | `services/incident_service.py`, `services/site_pages.py`, `templates/archive/detail.html`, `templates/incident_major/druck.html` … (+2) |
| ➜ | `=fw` | 6 | 6 | `services/objekt_symbol_service.py`, `static/js/lagefuehrung.js`, `static/js/objekt_karte.js` |
| 👥 | `users` | 6 | 6 | `services/site_pages.py`, `templates/archive/detail.html`, `templates/probenplanung/_probeart_formular.html`, `templates/termin/detail.html` … (+1) |
| 🗺 | `map` | 6 | 5 | `services/teams_card.py`, `templates/gateway/druckregeln.html`, `templates/objekt/infoscreen_alarm.html`, `templates/pdf/_emoji_font_face.html` … (+2) |
| ⚙ | `settings` | 6 | 6 | `templates/gateway/_tabs.html`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html`, `templates/gateway/druckregeln.html` … (+1) |
| 📞 | `phone` | 6 | 5 | `templates/incident_major/public_report.html`, `templates/incident_major/public_sms_error.html`, `templates/incident_major/public_verify.html`, `templates/pdf/_emoji_font_face.html` … (+2) |
| 🌦 | `cloud-rain` | 6 | 6 | `templates/objekt/infoscreen_alarm.html`, `templates/objekt/infoscreen_verwaltung.html`, `templates/weather/_data_section.html`, `templates/weather/index.html` |
| 🚧 | `barrier-block` | 5 | 2 | `models/major_incident.py`, `services/road_closure_notify_service.py`, `templates/incident_major/public_report.html` |
| ☀ | `sun` | 5 | 5 | `models/objekt.py`, `services/objekt_service.py`, `templates/objekt/infoscreen_alarm.html`, `templates/weather/_data_section.html` |
| 🏢 | `building` | 5 | 5 | `services/katplan_typen.py`, `templates/gateway/druckregeln.html`, `templates/incident/lagefuehrung_druck.html`, `templates/katplan/_links_anzeige.html` … (+1) |
| ⛈ | `cloud-storm` | 5 | 5 | `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| ✕ | `x` | 5 | 5 | `static/js/annotate.js`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html`, `templates/incident_major/_journal_entry_druck.html` … (+1) |
| ✉ | `mail` | 5 | 5 | `static/js/probe-appell.js`, `templates/gateway/druckregeln.html`, `templates/probenplanung/teilnehmer_appell.html` |
| ☣ | `biohazard` | 4 | 4 | `models/major_incident.py`, `templates/nachschlagewerke/gefahrgut.html`, `templates/nachschlagewerke/gefahrgut_detail.html`, `templates/nachschlagewerke/start.html` |
| 🚨 | `urgent` | 4 | 3 | `routers/api_v1.py`, `services/teams_card.py`, `templates/public/alarm_summary.html`, `templates/termin/liste.html` |
| 🔑 | `key` | 4 | 4 | `services/objekt_service.py`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html`, `templates/objekt/infoscreen_alarm.html` |
| 🗑 | `trash` | 4 | 4 | `templates/annotate/editor.html`, `templates/archive/detail.html`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html` |
| ▶ | `player-play` | 4 | 4 | `templates/foerderstrecke/maschinist.html`, `templates/media/gallery.html`, `templates/weather/index.html` |
| ▸ | `chevron-right` | 4 | 4 | `templates/foerderstrecke/wizard.html`, `templates/gateway/druckregeln.html`, `templates/weather/index.html` |
| 🎬 | `movie` | 4 | 4 | `templates/media/gallery.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/incident_report.html` |
| 🌊 | `ripple` | 3 | 3 | `models/major_incident.py`, `services/weather_service.py` |
| 🧪 | `flask` | 3 | 3 | `models/objekt.py`, `services/objekt_service.py`, `templates/gateway/druckregeln.html` |
| ✨ | `sparkles` | 3 | 3 | `routers/ui_incident.py`, `templates/archive/detail.html` |
| 👤 | `user` | 3 | 3 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `templates/katplan/_links_anzeige.html` |
| 🔀 | `arrows-shuffle` | 3 | 3 | `services/katplan_typen.py`, `templates/gateway/detail.html` |
| 🖥 | `device-desktop` | 3 | 1 | `services/teams_card.py`, `templates/archive/detail.html` |
| ✖ | `x` | 3 | 3 | `templates/annotate/editor.html`, `templates/gateway/_druckhistorie.html` |
| 📊 | `chart-bar` | 3 | 3 | `templates/archive/detail.html`, `templates/gateway/druckregeln.html`, `templates/termin/detail.html` |
| 🚰 | `fire-hydrant` | 3 | 3 | `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html`, `templates/public/alarm_summary.html` |
| 🕓 | `clock` | 3 | 3 | `templates/gateway/agent_detail.html`, `templates/gateway/detail.html` |
| 🔎 | `zoom-in` | 3 | 3 | `templates/gateway/detail.html`, `templates/nachschlagewerke/rettungskarten.html`, `templates/suche/_treffer.html` |
| 📎 | `paperclip` | 3 | 2 | `templates/katplan/_baum.html`, `templates/mailing/campaign_detail.html`, `templates/pdf/incident_report.html` |
| 🚑 | `ambulance` | 3 | 3 | `templates/nachschlagewerke/rettungskarten.html`, `templates/nachschlagewerke/start.html`, `templates/objekt/infoscreen_alarm.html` |
| 🌨 | `cloud-snow` | 3 | 3 | `templates/objekt/infoscreen_alarm.html` |
| 🏷 | `tag` | 3 | 2 | `templates/pdf/_emoji_font_face.html`, `templates/pdf/lagebericht.html`, `templates/pdf/objektblatt.html` |
| 📅 | `calendar` | 3 | 3 | `templates/probenplanung/_dashboard_kachel.html`, `templates/termin/liste.html` |
| 💥 | `bomb` | 2 | 2 | `models/objekt.py`, `services/objekt_service.py` |
| 🅿 | `parking` | 2 | 2 | `services/objekt_service.py`, `templates/pdf/_emoji_font_face.html` |
| ✏ | `pencil` | 2 | 1 | `services/road_closure_notify_service.py`, `templates/annotate/editor.html` |
| 🔁 | `repeat` | 2 | 1 | `services/road_closure_notify_service.py`, `templates/gateway/agent_detail.html` |
| 📨 | `mail-forward` | 2 | 1 | `services/site_pages.py`, `templates/mailing/lists_index.html` |
| 🫁 | `lungs` | 2 | 2 | `services/site_pages.py`, `templates/archive/detail.html` |
| ○ | `=status` | 2 | 2 | `static/js/probe-appell.js`, `templates/probenplanung/teilnehmer_appell.html` |
| 💻 | `device-laptop` | 2 | 2 | `templates/_print_dialog.html`, `templates/gateway/agent_detail.html` |
| ▭ | `square` | 2 | 2 | `templates/annotate/editor.html`, `templates/incident_major/karte.html` |
| 🕐 | `clock` | 2 | 2 | `templates/archive/detail.html` |
| 🧾 | `receipt` | 2 | 2 | `templates/archive/detail.html`, `templates/gateway/_druckhistorie.html` |
| 🧵 | `messages` | 2 | 2 | `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html` |
| ② | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| ① | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🛣 | `route` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🚩 | `flag` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| ⏺ | `player-record` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🪟 | `brand-windows` | 2 | 2 | `templates/gateway/agent_detail.html`, `templates/gateway/liste.html` |
| 🆔 | `id` | 2 | 2 | `templates/gateway/agent_detail.html` |
| 📟 | `device-mobile-message` | 2 | 2 | `templates/gateway/detail.html` |
| ▾ | `chevron-down` | 2 | 2 | `templates/gateway/druckregeln.html` |
| △ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| ◇ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| 📱 | `device-mobile` | 2 | 2 | `templates/incident_major/public_report.html`, `templates/incident_major/public_verify.html` |
| 📷 | `camera` | 2 | 2 | `templates/incident_major/public_report.html`, `templates/verleih/_ausleihe_detail.html` |
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
| 📩 | `mail-opened` | 1 | 1 | `services/incident_service.py` |
| ☎ | `phone` | 1 | 1 | `services/katplan_qs_service.py` |
| ❔ | `help-circle` | 1 | 1 | `services/katplan_qs_service.py` |
| ⏰ | `alarm` | 1 | 1 | `services/katplan_qs_service.py` |
| ⌛ | `hourglass` | 1 | 1 | `services/katplan_qs_service.py` |
| 🤖 | `robot` | 1 | 1 | `services/katplan_qs_service.py` |
| 📦 | `package` | 1 | 1 | `services/katplan_typen.py` |
| 📕 | `book` | 1 | 1 | `services/objekt_service.py` |
| 📻 | `radio` | 1 | 1 | `services/objekt_service.py` |
| 🛗 | `elevator` | 1 | 1 | `services/objekt_service.py` |
| 🚻 | `users-group` | 1 | 1 | `services/objekt_service.py` |
| 🌀 | `=fw` | 1 | 1 | `services/objekt_service.py` |
| ⏩ | `player-track-next` | 1 | 0 | `services/road_closure_notify_service.py` |
| ↩ | `arrow-back-up` | 1 | 0 | `services/road_closure_notify_service.py` |
| 🧊 | `snowflake` | 1 | 1 | `services/weather_service.py` |
| 🟢 | `=status` | 1 | 1 | `templates/_print_dialog.html` |
| 🔴 | `=status` | 1 | 1 | `templates/_print_dialog.html` |
| ✥ | `pointer` | 1 | 1 | `templates/annotate/editor.html` |
| ↗ | `external-link` | 1 | 1 | `templates/annotate/editor.html` |
| ◯ | `circle` | 1 | 1 | `templates/annotate/editor.html` |
| ⌫ | `eraser` | 1 | 1 | `templates/annotate/editor.html` |
| ↶ | `arrow-back-up` | 1 | 1 | `templates/annotate/editor.html` |
| ↷ | `arrow-forward-up` | 1 | 1 | `templates/annotate/editor.html` |
| ↻ | `refresh` | 1 | 1 | `templates/archive/detail.html` |
| 🧍 | `user-check` | 1 | 1 | `templates/archive/detail.html` |
| ◀ | `chevron-left` | 1 | 1 | `templates/foerderstrecke/maschinist.html` |
| ↺ | `rotate` | 1 | 1 | `templates/foerderstrecke/wizard.html` |
| ⏹ | `player-stop` | 1 | 1 | `templates/foerderstrecke/wizard.html` |
| ✔ | `check` | 1 | 1 | `templates/gateway/_druckhistorie.html` |
| ⛔ | `ban` | 1 | 1 | `templates/gateway/_druckregel_simulation.html` |
| 🔌 | `plug` | 1 | 1 | `templates/gateway/detail.html` |
| 🧩 | `puzzle` | 1 | 1 | `templates/gateway/detail.html` |
| ✎ | `pencil` | 1 | 1 | `templates/gateway/detail.html` |
| 🩸 | `droplet-half-2` | 1 | 1 | `templates/gateway/detail.html` |
| 🔧 | `tool` | 1 | 1 | `templates/gateway/druckregeln.html` |
| 📢 | `speakerphone` | 1 | 1 | `templates/incident/lagefuehrung_druck.html` |
| ▣ | `=fw` | 1 | 1 | `templates/incident_major/karte.html` |
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
| 🌐 | `world` | 1 | 1 | `templates/probenplanung/verwaltung_oeffentlich.html` |
| 👁 | `eye` | 1 | 1 | `templates/public/page.html` |
| ⬇ | `download` | 1 | 1 | `templates/stats/fahrtenbuch.html` |
| 🗓 | `calendar-event` | 1 | 1 | `templates/termin/detail.html` |
| ⌄ | `chevron-down` | 1 | 1 | `templates/verleih/_ausleihe_form.html` |
| 💨 | `wind` | 1 | 1 | `templates/weather/_data_section.html` |
| 🌬 | `wind` | 1 | 1 | `templates/weather/index.html` |

\* ohne Kommentare und externe Kanäle.

## Dateien mit den meisten migrierbaren Treffern

| Datei | Treffer |
|---|---:|
| `app/templates/objekt/infoscreen_alarm.html` | 52 |
| `app/templates/foerderstrecke/wizard.html` | 30 |
| `app/templates/archive/detail.html` | 20 |
| `app/templates/gateway/detail.html` | 20 |
| `app/services/objekt_service.py` | 18 |
| `app/templates/gateway/druckregeln.html` | 18 |
| `app/templates/gateway/agent_detail.html` | 15 |
| `app/templates/annotate/editor.html` | 11 |
| `app/templates/weather/_data_section.html` | 11 |
| `app/models/major_incident.py` | 11 |
| `app/services/weather_service.py` | 11 |
| `app/templates/probenplanung/teilnehmer_appell.html` | 10 |
| `app/templates/pdf/objektblatt.html` | 10 |
| `app/templates/incident_major/public_report.html` | 9 |
| `app/services/katplan_qs_service.py` | 9 |
| `app/templates/termin/liste.html` | 9 |
| `app/static/js/probe-appell.js` | 8 |
| `app/templates/foerderstrecke/maschinist.html` | 8 |
| `app/models/objekt.py` | 8 |
| `app/services/katplan_typen.py` | 8 |
| `app/templates/weather/index.html` | 8 |
| `app/templates/pdf/incident_report.html` | 7 |
| `app/templates/incident_major/karte.html` | 7 |
| `app/templates/incident/lagefuehrung_druck.html` | 6 |
| `app/templates/termin/detail.html` | 6 |
| `app/templates/gateway/_druckhistorie.html` | 6 |
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
| `app/templates/probenplanung/_probeart_formular.html` | 3 |
| `app/templates/incident_major/_journal_entry_druck.html` | 3 |
| `app/templates/nachschlagewerke/start.html` | 3 |
| `app/routers/ui_incident.py` | 3 |
| `app/templates/incident_major/public_sms_error.html` | 3 |
| `app/templates/objekt/_gefahren.html` | 3 |
| `app/templates/gateway/liste.html` | 3 |
| `app/templates/stats/fahrtenbuch.html` | 3 |
| `app/services/incident_service.py` | 3 |
| `app/templates/katplan/_anhaenge_anzeige.html` | 3 |
| `app/templates/nachschlagewerke/_rettungskarten_result.html` | 3 |
| `app/templates/nachschlagewerke/rettungskarten.html` | 3 |
| `app/templates/nachschlagewerke/gefahrgut_detail.html` | 2 |
| `app/routers/ui_lagefuehrung.py` | 2 |
| `app/static/js/annotate.js` | 2 |
| `app/templates/incident_major/_cross_marker_druck.html` | 2 |
| `app/templates/incident_major/_site_druck.html` | 2 |
| `app/templates/incident_major/karte_druck.html` | 2 |
| `app/templates/objekt/_einsatz_inhalt.html` | 2 |
