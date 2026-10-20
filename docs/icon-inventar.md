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
| markup | 1299 |
| python | 280 |
| kommentar | 209 |
| js | 77 |
| extern | 60 |
| option | 31 |
| css | 17 |

## Zuordnung

| Zeichen | Ziel | Treffer | migrieren* | Dateien (Auszug) |
|---|---|---:|---:|---|
| → | `=text` | 489 | 287 | `cli.py`, `config.py`, `core/crypto.py`, `core/dependencies.py` … (+170) |
| ⚠ | `alert-triangle` | 128 | 120 | `models/objekt.py`, `routers/ui_incident.py`, `routers/ui_infoscreen_alarm.py`, `routers/ui_lagefuehrung.py` … (+62) |
| ✓ | `check` | 99 | 89 | `cli.py`, `models/sms.py`, `routers/ui_archive.py`, `routers/ui_termin.py` … (+51) |
| 📍 | `map-pin` | 56 | 56 | `models/major_incident.py`, `services/katplan_qs_service.py`, `services/katplan_typen.py`, `static/js/lagefuehrung.js` … (+29) |
| ← | `=text` | 54 | 52 | `static/css/tailwind.input.css`, `static/js/katplan_editor.js`, `templates/admin/device_token_detail.html`, `templates/admin/foerder_pumpe_form.html` … (+47) |
| 🖨 | `printer` | 52 | 52 | `routers/ui_druck.py`, `static/js/media-viewer.js`, `templates/_print_dialog.html`, `templates/archive/detail.html` … (+38) |
| 📄 | `file-text` | 41 | 41 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `services/site_pages.py`, `static/js/rettungskarten_katalog.js` … (+26) |
| ✕ | `x` | 39 | 39 | `static/js/annotate.js`, `static/js/app.js`, `static/js/lagefuehrung.js`, `templates/admin/device_tokens.html` … (+27) |
| 🚒 | `firetruck` | 34 | 30 | `routers/ui_teams_bot.py`, `services/alarm_outbox.py`, `services/gefahrgut_service.py`, `services/objekt_service.py` … (+22) |
| ✗ | `x` | 27 | 15 | `cli.py`, `services/sms_inbox_service.py`, `static/js/address-picker.js`, `static/js/probe-appell.js` … (+6) |
| ✨ | `sparkles` | 27 | 27 | `routers/ui_incident.py`, `static/js/incident-board.js`, `templates/admin/ai_prompts.html`, `templates/admin/sysadmin_quotas.html` … (+13) |
| 📋 | `clipboard-list` | 27 | 27 | `services/incident_service.py`, `services/site_pages.py`, `static/js/incident-board.js`, `templates/admin/device_tokens.html` … (+15) |
| 🗺 | `map` | 21 | 20 | `services/teams_card.py`, `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html`, `templates/einheit/einheit.html` … (+15) |
| ✅ | `circle-check` | 20 | 18 | `services/katplan_typen.py`, `services/road_closure_notify_service.py`, `static/js/incident-board.js`, `templates/admin/wasserstellen.html` … (+12) |
| ↔ | `=text` | 19 | 17 | `models/major_incident.py`, `models/master.py`, `models/objekt.py`, `routers/ui_major_incident.py` … (+13) |
| ● | `=status` | 19 | 19 | `templates/admin/settings.html`, `templates/einheit/einheit.html`, `templates/incident/board.html`, `templates/incident_major/_sektoren_grid.html` … (+3) |
| 🔥 | `flame` | 17 | 16 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `static/js/lagefuehrung.js` … (+9) |
| 🔗 | `link` | 17 | 17 | `services/katplan_qs_service.py`, `static/js/nachschlagewerke.js`, `templates/admin/vehicles.html`, `templates/foerderstrecke/wizard.html` … (+13) |
| ☰ | `menu-2` | 17 | 17 | `templates/admin/default_messages.html`, `templates/admin/dispatch_order.html`, `templates/admin/lage_hints.html`, `templates/admin/message_suggestions.html` … (+2) |
| ↗ | `external-link` | 17 | 17 | `templates/admin/dibos_einsaetze.html`, `templates/admin/page_edit.html`, `templates/admin/pages_list.html`, `templates/annotate/editor.html` … (+7) |
| 🔴 | `=status` | 16 | 16 | `core/templating.py`, `templates/_print_dialog.html`, `templates/incident/_dashboard_body.html`, `templates/incident/_message_card.html` … (+8) |
| 🌧 | `cloud-rain` | 15 | 14 | `static/js/weather_layer.js`, `templates/incident/_dashboard_body.html`, `templates/incident_major/karte.html`, `templates/objekt/infoscreen_alarm.html` … (+2) |
| 📞 | `phone` | 15 | 14 | `templates/incident/_alarm_edit_modal.html`, `templates/incident/_kontakte.html`, `templates/incident/info.html`, `templates/incident_major/public_report.html` … (+8) |
| ↓ | `=text` | 14 | 14 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/admin/device_tokens.html`, `templates/admin/lage_hints.html` … (+9) |
| ✏ | `pencil` | 14 | 13 | `services/road_closure_notify_service.py`, `static/js/media-viewer.js`, `templates/annotate/editor.html`, `templates/incident/_message_modal.html` … (+9) |
| 👥 | `users` | 14 | 14 | `services/site_pages.py`, `templates/admin/gsl_einstellungen.html`, `templates/admin/settings.html`, `templates/admin/system_settings.html` … (+6) |
| 📷 | `camera` | 14 | 14 | `templates/incident/_message_card.html`, `templates/incident/_message_modal.html`, `templates/incident/_person_card.html`, `templates/incident/_person_modal.html` … (+10) |
| 💧 | `droplet` | 13 | 12 | `models/major_incident.py`, `services/objekt_service.py`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html` … (+3) |
| 📥 | `inbox` | 13 | 13 | `templates/admin/backup.html`, `templates/admin/einsatz_import.html`, `templates/admin/gsl_einstellungen.html`, `templates/admin/konfig.html` … (+5) |
| 📱 | `device-mobile` | 13 | 13 | `templates/admin/device_tokens.html`, `templates/incident/_dashboard_body.html`, `templates/incident_major/_kraefteuebersicht.html`, `templates/incident_major/_site_detail.html` … (+3) |
| ✎ | `pencil` | 13 | 13 | `templates/admin/medienverwaltung.html`, `templates/gateway/detail.html`, `templates/incident/_lane_header.html`, `templates/incident_major/_funkjournal_rows.html` … (+6) |
| 🔍 | `search` | 13 | 13 | `templates/einheit/einheit.html`, `templates/incident/_dashboard_body.html`, `templates/incident/_uebernahme_dialog.html`, `templates/incident_major/_site_card.html` … (+2) |
| 📻 | `radio` | 12 | 12 | `services/objekt_service.py`, `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html`, `templates/incident/_subnav.html` … (+5) |
| 📢 | `speakerphone` | 12 | 12 | `static/js/lagefuehrung.js`, `templates/breathing/board.html`, `templates/incident/_dashboard_body.html`, `templates/incident/board.html` … (+4) |
| ⇒ | `=text` | 11 | 4 | `config.py`, `models/master.py`, `routers/api_weather.py`, `routers/ui_settings.py` … (+4) |
| ⚡ | `bolt` | 11 | 11 | `models/major_incident.py`, `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py` … (+5) |
| 🚨 | `urgent` | 11 | 10 | `routers/api_v1.py`, `services/teams_card.py`, `static/js/einsatz_info.js`, `templates/incident/info.html` … (+6) |
| ↑ | `=text` | 11 | 11 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/admin/lage_hints.html`, `templates/incident/funkjournal.html` … (+6) |
| 🏢 | `building` | 11 | 11 | `services/katplan_typen.py`, `static/js/lagefuehrung.js`, `templates/gateway/druckregeln.html`, `templates/incident/_ei_objekt_section.html` … (+6) |
| ▶ | `player-play` | 11 | 11 | `static/js/lagefuehrung.js`, `templates/breathing/board.html`, `templates/einheit/einheit.html`, `templates/foerderstrecke/maschinist.html` … (+6) |
| 🎯 | `target` | 11 | 10 | `templates/admin/foerder_kalibrierung.html`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html`, `templates/gateway/agent_detail.html` … (+2) |
| 👤 | `user` | 10 | 10 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `templates/incident/_alarm_edit_modal.html`, `templates/incident/_lane_header.html` … (+4) |
| 🖼 | `photo` | 10 | 10 | `templates/admin/medienverwaltung.html`, `templates/admin/page_edit.html`, `templates/incident/_message_modal.html`, `templates/incident/_person_modal.html` … (+6) |
| 🎬 | `movie` | 10 | 10 | `templates/admin/medienverwaltung.html`, `templates/incident/_message_card.html`, `templates/incident/_message_modal.html`, `templates/incident/_person_card.html` … (+5) |
| 📦 | `package` | 9 | 9 | `services/katplan_typen.py`, `templates/admin/backup.html`, `templates/admin/gsl_einstellungen.html`, `templates/admin/konfig.html` … (+3) |
| 🔎 | `zoom-in` | 9 | 9 | `templates/admin/members.html`, `templates/admin/wasserstellen.html`, `templates/gateway/detail.html`, `templates/nachschlagewerke/rettungskarten.html` … (+4) |
| 🌦 | `cloud-rain` | 9 | 9 | `templates/incident/board.html`, `templates/incident_major/board.html`, `templates/objekt/infoscreen_alarm.html`, `templates/objekt/infoscreen_verwaltung.html` … (+2) |
| 🟢 | `=status` | 8 | 8 | `core/templating.py`, `templates/_print_dialog.html`, `templates/incident/_person_card.html`, `templates/incident/_task_card.html` … (+3) |
| ↻ | `refresh` | 8 | 8 | `static/js/einheit_modus.js`, `static/js/lagefuehrung.js`, `templates/admin/server_log.html`, `templates/archive/detail.html` … (+4) |
| ✉ | `mail` | 8 | 8 | `static/js/probe-appell.js`, `templates/gateway/druckregeln.html`, `templates/incident/_ei_objekt_section.html`, `templates/incident/_objekt_panel.html` … (+2) |
| ▼ | `chevron-down` | 8 | 8 | `templates/admin/device_tokens.html`, `templates/admin/medienverwaltung.html`, `templates/admin/members.html`, `templates/admin/wasserstellen.html` … (+3) |
| 🚰 | `fire-hydrant` | 8 | 8 | `templates/admin/foerder_pumpen.html`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html`, `templates/incident/info.html` … (+2) |
| 📊 | `chart-bar` | 8 | 8 | `templates/archive/detail.html`, `templates/gateway/druckregeln.html`, `templates/incident/board.html`, `templates/incident_major/_lage_layout.html` … (+2) |
| 🚁 | `helicopter` | 8 | 8 | `templates/incident/_dashboard_body.html`, `templates/incident/_subnav.html`, `templates/incident/board.html`, `templates/incident_major/_lage_layout.html` |
| ❄ | `snowflake` | 7 | 7 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| ⏰ | `alarm` | 7 | 7 | `services/katplan_qs_service.py`, `static/js/app.js`, `templates/incident/_dashboard_body.html`, `templates/incident/_message_card.html` … (+2) |
| ▲ | `chevron-up` | 7 | 7 | `templates/admin/device_tokens.html`, `templates/admin/medienverwaltung.html`, `templates/admin/members.html`, `templates/admin/wasserstellen.html` … (+2) |
| 🕐 | `clock` | 7 | 7 | `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html`, `templates/archive/detail.html`, `templates/incident_major/_lage_layout.html` |
| 🔒 | `lock` | 7 | 7 | `templates/admin/medienverwaltung.html`, `templates/incident/_alarm_edit_modal.html`, `templates/incident/board.html`, `templates/objekt/kataloge.html` … (+2) |
| 💡 | `bulb` | 7 | 7 | `templates/admin/wasserstellen.html`, `templates/incident/_dashboard_body.html`, `templates/incident/_message_card.html`, `templates/incident/_message_modal.html` … (+2) |
| 🟡 | `=status` | 7 | 7 | `templates/incident/_dashboard_body.html`, `templates/incident/_task_card.html`, `templates/incident/_task_modal.html`, `templates/incident/_task_status_pills.html` … (+2) |
| ⚪ | `=status` | 7 | 7 | `templates/incident/_dashboard_body.html`, `templates/incident/_message_card.html`, `templates/incident/_message_modal.html`, `templates/incident/_task_card.html` … (+1) |
| 🏷 | `tag` | 7 | 6 | `templates/incident/lagefuehrung.html`, `templates/objekt/_merkmale.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/lagebericht.html` … (+1) |
| ☀ | `sun` | 6 | 6 | `models/objekt.py`, `services/objekt_service.py`, `templates/incident/_dashboard_body.html`, `templates/objekt/infoscreen_alarm.html` … (+1) |
| 🔑 | `key` | 6 | 6 | `services/objekt_service.py`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html`, `templates/objekt/infoscreen_alarm.html` … (+2) |
| ➜ | `=fw` | 6 | 6 | `services/objekt_symbol_service.py`, `static/js/lagefuehrung.js`, `static/js/objekt_karte.js` |
| 📎 | `paperclip` | 6 | 5 | `templates/admin/_mail_eingang_content.html`, `templates/incident/_message_card.html`, `templates/incident/_message_modal.html`, `templates/katplan/_baum.html` … (+2) |
| 🗂 | `folders` | 6 | 6 | `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html`, `templates/incident_major/_lage_layout.html`, `templates/objekt/_einsatz_inhalt.html` |
| 🗑 | `trash` | 6 | 6 | `templates/annotate/editor.html`, `templates/archive/detail.html`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html` … (+2) |
| ▸ | `chevron-right` | 6 | 6 | `templates/foerderstrecke/wizard.html`, `templates/gateway/druckregeln.html`, `templates/incident/lagefuehrung.html`, `templates/incident_major/board.html` … (+1) |
| ⚙ | `settings` | 6 | 6 | `templates/gateway/_tabs.html`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html`, `templates/gateway/druckregeln.html` … (+1) |
| 📝 | `note` | 6 | 6 | `templates/incident/lagefuehrung.html`, `templates/incident_major/_lage_layout.html`, `templates/incident_major/dashboard.html`, `templates/incident_major/lagedokument.html` … (+1) |
| 🚧 | `barrier-block` | 5 | 2 | `models/major_incident.py`, `services/road_closure_notify_service.py`, `templates/incident_major/public_report.html` |
| ☣ | `biohazard` | 5 | 5 | `models/major_incident.py`, `templates/incident/lagefuehrung.html`, `templates/nachschlagewerke/gefahrgut.html`, `templates/nachschlagewerke/gefahrgut_detail.html` … (+1) |
| 🫁 | `lungs` | 5 | 5 | `services/site_pages.py`, `templates/admin/settings.html`, `templates/archive/detail.html`, `templates/incident/_subnav.html` |
| ⛈ | `cloud-storm` | 5 | 5 | `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| ↺ | `rotate` | 5 | 5 | `static/js/address-autocomplete.js`, `static/js/lagefuehrung.js`, `templates/foerderstrecke/wizard.html`, `templates/objekt/_dokumente.html` … (+1) |
| ⏳ | `hourglass` | 5 | 5 | `static/js/einheit_modus.js`, `templates/incident_major/dashboard.html`, `templates/objekt/_dokumente.html` |
| ⬇ | `download` | 5 | 5 | `static/js/media-viewer.js`, `templates/admin/server_log.html`, `templates/incident_major/zeitreise.html`, `templates/stats/fahrtenbuch.html` |
| 📡 | `antenna` | 5 | 5 | `templates/admin/device_tokens.html`, `templates/incident/_dashboard_body.html` |
| 🚑 | `ambulance` | 5 | 5 | `templates/incident/_dashboard_body.html`, `templates/incident/board.html`, `templates/nachschlagewerke/rettungskarten.html`, `templates/nachschlagewerke/start.html` … (+1) |
| 📨 | `mail-forward` | 4 | 3 | `services/site_pages.py`, `templates/incident/_vehicle_card.html`, `templates/incident_major/dashboard.html`, `templates/mailing/lists_index.html` |
| 🖥 | `device-desktop` | 4 | 2 | `services/teams_card.py`, `templates/archive/detail.html`, `templates/incident/info.html` |
| ⏱ | `stopwatch` | 4 | 4 | `templates/admin/org_backup.html`, `templates/incident/lagefuehrung.html`, `templates/incident_major/_site_card.html` |
| ▾ | `chevron-down` | 4 | 4 | `templates/gateway/druckregeln.html`, `templates/incident/board.html`, `templates/incident_major/_board_sector_filter.html` |
| 🧭 | `compass` | 4 | 4 | `templates/incident/_subnav.html`, `templates/incident/lagefuehrung.html`, `templates/incident_major/dashboard.html` |
| ❌ | `circle-x` | 4 | 4 | `templates/incident/board.html`, `templates/incident/info.html`, `templates/objekt/infoscreen_alarm.html` |
| 🎤 | `microphone` | 4 | 4 | `templates/incident/board.html` |
| 📜 | `history` | 4 | 4 | `templates/objekt/_protokoll.html`, `templates/objekt/changelog.html`, `templates/objekt/liste.html`, `templates/pdf/lagebericht.html` |
| 🟠 | `=status` | 3 | 3 | `core/templating.py`, `templates/incident/_person_card.html`, `templates/incident_major/_kraefteuebersicht.html` |
| 🌊 | `ripple` | 3 | 3 | `models/major_incident.py`, `services/weather_service.py` |
| 💥 | `bomb` | 3 | 3 | `models/objekt.py`, `services/objekt_service.py`, `templates/objekt/kataloge.html` |
| 🧪 | `flask` | 3 | 3 | `models/objekt.py`, `services/objekt_service.py`, `templates/gateway/druckregeln.html` |
| 🔀 | `arrows-shuffle` | 3 | 3 | `services/katplan_typen.py`, `templates/gateway/detail.html` |
| ↩ | `arrow-back-up` | 3 | 2 | `services/road_closure_notify_service.py`, `templates/incident/funkjournal.html`, `templates/incident_major/_funkjournal_rows.html` |
| ⏸ | `player-pause` | 3 | 3 | `static/js/lagefuehrung.js`, `templates/weather/index.html` |
| 🧵 | `messages` | 3 | 3 | `templates/admin/foerder_schlaeuche.html`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html` |
| 💾 | `device-floppy` | 3 | 3 | `templates/admin/medienverwaltung.html`, `templates/admin/org_backup.html`, `templates/admin/sysadmin_quotas.html` |
| ✖ | `x` | 3 | 3 | `templates/annotate/editor.html`, `templates/gateway/_druckhistorie.html` |
| 🧾 | `receipt` | 3 | 3 | `templates/archive/detail.html`, `templates/gateway/_druckhistorie.html`, `templates/objekt/_dokumente.html` |
| 🕓 | `clock` | 3 | 3 | `templates/gateway/agent_detail.html`, `templates/gateway/detail.html` |
| 🌡 | `temperature` | 3 | 3 | `templates/incident/_dashboard_body.html`, `templates/objekt/infoscreen_alarm.html` |
| ⭐ | `star` | 3 | 3 | `templates/incident/funkjournal.html` |
| 🔄 | `refresh` | 3 | 3 | `templates/incident_major/meldungen.html`, `templates/objekt/_datenpflege.html`, `templates/objekt/detail.html` |
| 🏘 | `buildings` | 3 | 3 | `templates/objekt/_wohnanlage.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| 🚪 | `door` | 3 | 3 | `templates/objekt/_zusatzadressen.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| 🌨 | `cloud-snow` | 3 | 3 | `templates/objekt/infoscreen_alarm.html` |
| 📅 | `calendar` | 3 | 3 | `templates/probenplanung/_dashboard_kachel.html`, `templates/termin/liste.html` |
| ⚫ | `=status` | 2 | 2 | `core/templating.py`, `templates/incident/_person_card.html` |
| 🅿 | `parking` | 2 | 2 | `services/objekt_service.py`, `templates/pdf/_emoji_font_face.html` |
| 🔁 | `repeat` | 2 | 1 | `services/road_closure_notify_service.py`, `templates/gateway/agent_detail.html` |
| 🧰 | `box` | 2 | 2 | `static/js/lagefuehrung.js`, `templates/incident/lagefuehrung.html` |
| ○ | `=status` | 2 | 2 | `static/js/probe-appell.js`, `templates/probenplanung/teilnehmer_appell.html` |
| 💻 | `device-laptop` | 2 | 2 | `templates/_print_dialog.html`, `templates/gateway/agent_detail.html` |
| 🔌 | `plug` | 2 | 2 | `templates/admin/org_backup.html`, `templates/gateway/detail.html` |
| 🗓 | `calendar-event` | 2 | 2 | `templates/admin/settings.html`, `templates/termin/detail.html` |
| ▭ | `square` | 2 | 2 | `templates/annotate/editor.html`, `templates/incident_major/karte.html` |
| ◀ | `chevron-left` | 2 | 2 | `templates/foerderstrecke/maschinist.html`, `templates/incident/board.html` |
| ② | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| ① | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🛣 | `route` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🚩 | `flag` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| ⏺ | `player-record` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🪟 | `brand-windows` | 2 | 2 | `templates/gateway/agent_detail.html`, `templates/gateway/liste.html` |
| 🆔 | `id` | 2 | 2 | `templates/gateway/agent_detail.html` |
| 📟 | `device-mobile-message` | 2 | 2 | `templates/gateway/detail.html` |
| 💨 | `wind` | 2 | 2 | `templates/incident/_dashboard_body.html`, `templates/weather/_data_section.html` |
| 📂 | `folder-open` | 2 | 2 | `templates/incident/_uebernahme_dialog.html`, `templates/objekt/_einsatz_inhalt.html` |
| 🌬 | `wind` | 2 | 2 | `templates/incident/lagefuehrung.html`, `templates/weather/index.html` |
| ↕ | `arrows-sort` | 2 | 2 | `templates/incident_major/_stab_tafel.html`, `templates/incident_major/board.html` |
| ☑ | `square-check` | 2 | 2 | `templates/incident_major/board.html` |
| ☐ | `square` | 2 | 2 | `templates/incident_major/board.html`, `templates/pdf/katplan.html` |
| △ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| ◇ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| 🌫 | `mist` | 2 | 2 | `templates/objekt/infoscreen_alarm.html` |
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
| ⌛ | `hourglass` | 1 | 1 | `services/katplan_qs_service.py` |
| 🤖 | `robot` | 1 | 1 | `services/katplan_qs_service.py` |
| 📕 | `book` | 1 | 1 | `services/objekt_service.py` |
| 🛗 | `elevator` | 1 | 1 | `services/objekt_service.py` |
| 🚻 | `users-group` | 1 | 1 | `services/objekt_service.py` |
| 🌀 | `=fw` | 1 | 1 | `services/objekt_service.py` |
| ⏩ | `player-track-next` | 1 | 0 | `services/road_closure_notify_service.py` |
| 🧊 | `snowflake` | 1 | 1 | `services/weather_service.py` |
| 🗄 | `archive` | 1 | 1 | `templates/admin/backup.html` |
| 📵 | `device-mobile-off` | 1 | 1 | `templates/admin/device_token_detail.html` |
| 🔋 | `battery` | 1 | 1 | `templates/admin/device_tokens.html` |
| ♻ | `recycle` | 1 | 1 | `templates/admin/org_backup.html` |
| 📧 | `mail` | 1 | 1 | `templates/admin/users.html` |
| ⭳ | `download` | 1 | 1 | `templates/admin/wasserstellen.html` |
| ✥ | `pointer` | 1 | 1 | `templates/annotate/editor.html` |
| ◯ | `circle` | 1 | 1 | `templates/annotate/editor.html` |
| ⌫ | `eraser` | 1 | 1 | `templates/annotate/editor.html` |
| ↶ | `arrow-back-up` | 1 | 1 | `templates/annotate/editor.html` |
| ↷ | `arrow-forward-up` | 1 | 1 | `templates/annotate/editor.html` |
| 🧍 | `user-check` | 1 | 1 | `templates/archive/detail.html` |
| 🛟 | `lifebuoy` | 1 | 1 | `templates/breathing/board.html` |
| ⬅ | `arrow-left` | 1 | 1 | `templates/breathing/board.html` |
| ⏹ | `player-stop` | 1 | 1 | `templates/foerderstrecke/wizard.html` |
| ✔ | `check` | 1 | 1 | `templates/gateway/_druckhistorie.html` |
| ⛔ | `ban` | 1 | 1 | `templates/gateway/_druckregel_simulation.html` |
| 🧩 | `puzzle` | 1 | 1 | `templates/gateway/detail.html` |
| 🩸 | `droplet-half-2` | 1 | 1 | `templates/gateway/detail.html` |
| 🔧 | `tool` | 1 | 1 | `templates/gateway/druckregeln.html` |
| 💬 | `message` | 1 | 1 | `templates/incident/_dashboard_body.html` |
| 🏥 | `building-hospital` | 1 | 1 | `templates/incident/_dashboard_body.html` |
| 📏 | `ruler` | 1 | 1 | `templates/incident/lagefuehrung.html` |
| ⭕ | `circle-dashed` | 1 | 1 | `templates/incident/lagefuehrung.html` |
| 📸 | `camera` | 1 | 1 | `templates/incident/lagefuehrung.html` |
| ↵ | `corner-down-left` | 1 | 1 | `templates/incident_major/_cross_marker_panel.html` |
| ▣ | `=fw` | 1 | 1 | `templates/incident_major/karte.html` |
| 🛡 | `shield` | 1 | 1 | `templates/incident_major/public_report.html` |
| ✈ | `plane` | 1 | 1 | `templates/incident_major/public_report.html` |
| ➕ | `plus` | 1 | 1 | `templates/incident_major/ressourcen.html` |
| 📑 | `files` | 1 | 1 | `templates/katplan/_anhaenge_anzeige.html` |
| 🏛 | `building-bank` | 1 | 1 | `templates/katplan/_links_anzeige.html` |
| 📚 | `books` | 1 | 1 | `templates/nachschlagewerke/start.html` |
| 🔔 | `bell` | 1 | 1 | `templates/objekt/_benachrichtigung.html` |
| 🙋 | `hand-stop` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| 🏠 | `home` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| 🌤 | `sun-low` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| ⛅ | `cloud` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| ☁ | `cloud` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| 📺 | `device-tv` | 1 | 1 | `templates/objekt/infoscreen_verwaltung.html` |
| ⓘ | `info-circle` | 1 | 1 | `templates/objekt/pflege_review.html` |
| ▬ | `=fw` | 1 | 1 | `templates/pdf/foerderstrecke.html` |
| ↳ | `=text` | 1 | 1 | `templates/pdf/incident_report.html` |
| 🌐 | `world` | 1 | 1 | `templates/probenplanung/verwaltung_oeffentlich.html` |
| 👁 | `eye` | 1 | 1 | `templates/public/page.html` |
| ⌄ | `chevron-down` | 1 | 1 | `templates/verleih/_ausleihe_form.html` |

\* ohne Kommentare und externe Kanäle.

## Dateien mit den meisten migrierbaren Treffern

| Datei | Treffer |
|---|---:|
| `app/templates/objekt/infoscreen_alarm.html` | 52 |
| `app/templates/incident/board.html` | 36 |
| `app/templates/incident_major/_lage_layout.html` | 36 |
| `app/templates/incident/_dashboard_body.html` | 34 |
| `app/templates/foerderstrecke/wizard.html` | 30 |
| `app/templates/incident_major/karte.html` | 28 |
| `app/templates/breathing/board.html` | 26 |
| `app/templates/incident/lagefuehrung.html` | 26 |
| `app/templates/admin/device_tokens.html` | 20 |
| `app/templates/archive/detail.html` | 20 |
| `app/templates/gateway/detail.html` | 20 |
| `app/services/objekt_service.py` | 18 |
| `app/static/js/lagefuehrung.js` | 18 |
| `app/templates/gateway/druckregeln.html` | 18 |
| `app/templates/einheit/einheit.html` | 17 |
| `app/templates/incident_major/_site_detail.html` | 17 |
| `app/templates/incident/_message_card.html` | 17 |
| `app/templates/incident_major/dashboard.html` | 17 |
| `app/templates/incident/_task_card.html` | 16 |
| `app/templates/objekt/_dokumente.html` | 16 |
| `app/templates/gateway/agent_detail.html` | 15 |
| `app/templates/incident/_message_modal.html` | 15 |
| `app/templates/incident_major/_cross_marker_panel.html` | 15 |
| `app/templates/objekt/detail.html` | 13 |
| `app/templates/incident/info.html` | 13 |
| `app/templates/annotate/editor.html` | 11 |
| `app/templates/objekt/liste.html` | 11 |
| `app/templates/weather/_data_section.html` | 11 |
| `app/models/major_incident.py` | 11 |
| `app/services/weather_service.py` | 11 |
| `app/templates/incident_major/board.html` | 11 |
| `app/templates/objekt/_einsatz_inhalt.html` | 11 |
| `app/templates/probenplanung/teilnehmer_appell.html` | 10 |
| `app/templates/pdf/objektblatt.html` | 10 |
| `app/templates/incident_major/_site_card.html` | 10 |
| `app/templates/incident_major/lagedokument.html` | 9 |
| `app/templates/incident_major/public_report.html` | 9 |
| `app/templates/incident_major/stab.html` | 9 |
| `app/templates/incident/_person_card.html` | 9 |
| `app/templates/incident_major/_kraefteuebersicht.html` | 9 |
| `app/services/katplan_qs_service.py` | 9 |
| `app/templates/admin/wasserstellen.html` | 9 |
| `app/templates/termin/liste.html` | 9 |
| `app/static/js/einsatz_info.js` | 9 |
| `app/templates/admin/gsl_einstellungen.html` | 9 |
| `app/templates/admin/system_settings.html` | 9 |
| `app/templates/admin/settings.html` | 9 |
| `app/static/js/probe-appell.js` | 8 |
| `app/templates/admin/push_notifications.html` | 8 |
| `app/templates/foerderstrecke/maschinist.html` | 8 |
| `app/models/objekt.py` | 8 |
| `app/services/katplan_typen.py` | 8 |
| `app/templates/objekt/_gefahren.html` | 8 |
| `app/templates/admin/medienverwaltung.html` | 8 |
| `app/templates/weather/index.html` | 8 |
| `app/templates/incident/_address_modal.html` | 7 |
| `app/templates/incident/_ei_objekt_section.html` | 7 |
| `app/templates/incident/_objekt_panel.html` | 7 |
| `app/templates/objekt/pflege_review.html` | 7 |
| `app/templates/incident/_task_media.html` | 7 |
