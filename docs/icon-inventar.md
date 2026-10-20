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
| markup | 1460 |
| python | 287 |
| kommentar | 209 |
| js | 77 |
| extern | 60 |
| option | 31 |
| css | 18 |

## Zuordnung

| Zeichen | Ziel | Treffer | migrieren* | Dateien (Auszug) |
|---|---|---:|---:|---|
| → | `=text` | 489 | 287 | `cli.py`, `config.py`, `core/crypto.py`, `core/dependencies.py` … (+170) |
| ⚠ | `alert-triangle` | 131 | 123 | `main.py`, `models/objekt.py`, `routers/ui_incident.py`, `routers/ui_infoscreen_alarm.py` … (+63) |
| ✓ | `check` | 103 | 93 | `cli.py`, `models/sms.py`, `routers/ui_archive.py`, `routers/ui_termin.py` … (+53) |
| 📍 | `map-pin` | 56 | 56 | `models/major_incident.py`, `services/katplan_qs_service.py`, `services/katplan_typen.py`, `static/js/lagefuehrung.js` … (+29) |
| ← | `=text` | 54 | 52 | `static/css/tailwind.input.css`, `static/js/katplan_editor.js`, `templates/admin/device_token_detail.html`, `templates/admin/foerder_pumpe_form.html` … (+47) |
| 🖨 | `printer` | 53 | 53 | `routers/ui_druck.py`, `static/js/media-viewer.js`, `templates/_print_dialog.html`, `templates/admin/_layout.html` … (+39) |
| 📄 | `file-text` | 41 | 41 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `services/site_pages.py`, `static/js/rettungskarten_katalog.js` … (+26) |
| ✕ | `x` | 39 | 39 | `static/js/annotate.js`, `static/js/app.js`, `static/js/lagefuehrung.js`, `templates/admin/device_tokens.html` … (+27) |
| 🚒 | `firetruck` | 38 | 34 | `routers/ui_teams_bot.py`, `services/alarm_outbox.py`, `services/gefahrgut_service.py`, `services/objekt_service.py` … (+25) |
| 📋 | `clipboard-list` | 33 | 33 | `services/incident_service.py`, `services/site_pages.py`, `static/js/incident-board.js`, `templates/admin/_layout.html` … (+18) |
| ✨ | `sparkles` | 28 | 28 | `routers/ui_incident.py`, `static/js/incident-board.js`, `templates/admin/_layout.html`, `templates/admin/ai_prompts.html` … (+14) |
| ✗ | `x` | 27 | 15 | `cli.py`, `services/sms_inbox_service.py`, `static/js/address-picker.js`, `static/js/probe-appell.js` … (+6) |
| 🗺 | `map` | 22 | 21 | `services/teams_card.py`, `templates/admin/gsl_einstellungen.html`, `templates/admin/index.html`, `templates/admin/system_settings.html` … (+16) |
| ✅ | `circle-check` | 21 | 19 | `services/katplan_typen.py`, `services/road_closure_notify_service.py`, `static/js/incident-board.js`, `templates/admin/wasserstellen.html` … (+13) |
| 👥 | `users` | 20 | 20 | `services/site_pages.py`, `templates/admin/_layout.html`, `templates/admin/gsl_einstellungen.html`, `templates/admin/index.html` … (+10) |
| ↔ | `=text` | 19 | 17 | `models/major_incident.py`, `models/master.py`, `models/objekt.py`, `routers/ui_major_incident.py` … (+13) |
| 🚨 | `urgent` | 19 | 18 | `routers/api_v1.py`, `services/teams_card.py`, `static/js/einsatz_info.js`, `templates/admin/_layout.html` … (+10) |
| ↗ | `external-link` | 19 | 19 | `templates/admin/dibos_einsaetze.html`, `templates/admin/index.html`, `templates/admin/page_edit.html`, `templates/admin/pages_list.html` … (+8) |
| ● | `=status` | 19 | 19 | `templates/admin/settings.html`, `templates/einheit/einheit.html`, `templates/incident/board.html`, `templates/incident_major/_sektoren_grid.html` … (+3) |
| 🔗 | `link` | 18 | 18 | `services/katplan_qs_service.py`, `static/js/nachschlagewerke.js`, `templates/admin/_layout.html`, `templates/admin/vehicles.html` … (+14) |
| ☰ | `menu-2` | 18 | 18 | `templates/admin/_layout.html`, `templates/admin/default_messages.html`, `templates/admin/dispatch_order.html`, `templates/admin/lage_hints.html` … (+3) |
| 🔴 | `=status` | 17 | 17 | `core/templating.py`, `templates/_print_dialog.html`, `templates/admin/index.html`, `templates/incident/_dashboard_body.html` … (+9) |
| 🔥 | `flame` | 17 | 16 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `static/js/lagefuehrung.js` … (+9) |
| 🌧 | `cloud-rain` | 15 | 14 | `static/js/weather_layer.js`, `templates/incident/_dashboard_body.html`, `templates/incident_major/karte.html`, `templates/objekt/infoscreen_alarm.html` … (+2) |
| 📥 | `inbox` | 15 | 15 | `templates/admin/_layout.html`, `templates/admin/backup.html`, `templates/admin/einsatz_import.html`, `templates/admin/gsl_einstellungen.html` … (+6) |
| 🔍 | `search` | 15 | 15 | `templates/base.html`, `templates/einheit/einheit.html`, `templates/incident/_dashboard_body.html`, `templates/incident/_uebernahme_dialog.html` … (+3) |
| 📞 | `phone` | 15 | 14 | `templates/incident/_alarm_edit_modal.html`, `templates/incident/_kontakte.html`, `templates/incident/info.html`, `templates/incident_major/public_report.html` … (+8) |
| ↓ | `=text` | 14 | 14 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/admin/device_tokens.html`, `templates/admin/lage_hints.html` … (+9) |
| 👤 | `user` | 14 | 14 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `templates/_profil_aktionen.html`, `templates/base.html` … (+6) |
| 📦 | `package` | 14 | 14 | `services/katplan_typen.py`, `templates/admin/_layout.html`, `templates/admin/backup.html`, `templates/admin/gsl_einstellungen.html` … (+6) |
| 🏢 | `building` | 14 | 14 | `services/katplan_typen.py`, `static/js/lagefuehrung.js`, `templates/base.html`, `templates/gateway/druckregeln.html` … (+8) |
| ✏ | `pencil` | 14 | 13 | `services/road_closure_notify_service.py`, `static/js/media-viewer.js`, `templates/annotate/editor.html`, `templates/incident/_message_modal.html` … (+9) |
| 📷 | `camera` | 14 | 14 | `templates/incident/_message_card.html`, `templates/incident/_message_modal.html`, `templates/incident/_person_card.html`, `templates/incident/_person_modal.html` … (+10) |
| 💧 | `droplet` | 13 | 12 | `models/major_incident.py`, `services/objekt_service.py`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html` … (+3) |
| 📻 | `radio` | 13 | 13 | `services/objekt_service.py`, `templates/admin/_layout.html`, `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html` … (+6) |
| 📢 | `speakerphone` | 13 | 13 | `static/js/lagefuehrung.js`, `templates/admin/index.html`, `templates/breathing/board.html`, `templates/incident/_dashboard_body.html` … (+5) |
| 🌦 | `cloud-rain` | 13 | 13 | `templates/admin/_layout.html`, `templates/base.html`, `templates/incident/board.html`, `templates/incident_major/board.html` … (+5) |
| 📱 | `device-mobile` | 13 | 13 | `templates/admin/device_tokens.html`, `templates/incident/_dashboard_body.html`, `templates/incident_major/_kraefteuebersicht.html`, `templates/incident_major/_site_detail.html` … (+3) |
| ✎ | `pencil` | 13 | 13 | `templates/admin/medienverwaltung.html`, `templates/gateway/detail.html`, `templates/incident/_lane_header.html`, `templates/incident_major/_funkjournal_rows.html` … (+6) |
| ⚙ | `settings` | 12 | 12 | `templates/_profil_aktionen.html`, `templates/admin/_layout.html`, `templates/base.html`, `templates/gateway/_tabs.html` … (+5) |
| 🚰 | `fire-hydrant` | 12 | 12 | `templates/admin/_layout.html`, `templates/admin/foerder_pumpen.html`, `templates/base.html`, `templates/foerderstrecke/maschinist.html` … (+5) |
| 🖼 | `photo` | 12 | 12 | `templates/admin/_layout.html`, `templates/admin/medienverwaltung.html`, `templates/admin/page_edit.html`, `templates/incident/_message_modal.html` … (+7) |
| 📊 | `chart-bar` | 12 | 12 | `templates/admin/_layout.html`, `templates/archive/detail.html`, `templates/base.html`, `templates/gateway/druckregeln.html` … (+5) |
| 🎯 | `target` | 12 | 11 | `templates/admin/foerder_kalibrierung.html`, `templates/admin/index.html`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html` … (+3) |
| 🚁 | `helicopter` | 12 | 12 | `templates/admin/index.html`, `templates/base.html`, `templates/incident/_dashboard_body.html`, `templates/incident/_subnav.html` … (+3) |
| ⇒ | `=text` | 11 | 4 | `config.py`, `models/master.py`, `routers/api_weather.py`, `routers/ui_settings.py` … (+4) |
| ⚡ | `bolt` | 11 | 11 | `models/major_incident.py`, `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py` … (+5) |
| ↑ | `=text` | 11 | 11 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/admin/lage_hints.html`, `templates/incident/funkjournal.html` … (+6) |
| ▶ | `player-play` | 11 | 11 | `static/js/lagefuehrung.js`, `templates/breathing/board.html`, `templates/einheit/einheit.html`, `templates/foerderstrecke/maschinist.html` … (+6) |
| ✉ | `mail` | 11 | 11 | `static/js/probe-appell.js`, `templates/admin/_layout.html`, `templates/gateway/druckregeln.html`, `templates/incident/_ei_objekt_section.html` … (+3) |
| 🎬 | `movie` | 10 | 10 | `templates/admin/medienverwaltung.html`, `templates/incident/_message_card.html`, `templates/incident/_message_modal.html`, `templates/incident/_person_card.html` … (+5) |
| 🔑 | `key` | 9 | 9 | `services/objekt_service.py`, `templates/admin/_layout.html`, `templates/admin/index.html`, `templates/gateway/agent_detail.html` … (+4) |
| 🔎 | `zoom-in` | 9 | 9 | `templates/admin/members.html`, `templates/admin/wasserstellen.html`, `templates/gateway/detail.html`, `templates/nachschlagewerke/rettungskarten.html` … (+4) |
| 🟢 | `=status` | 8 | 8 | `core/templating.py`, `templates/_print_dialog.html`, `templates/incident/_person_card.html`, `templates/incident/_task_card.html` … (+3) |
| 🚧 | `barrier-block` | 8 | 5 | `models/major_incident.py`, `services/road_closure_notify_service.py`, `templates/base.html`, `templates/incident_major/public_report.html` … (+1) |
| 🫁 | `lungs` | 8 | 8 | `services/site_pages.py`, `templates/admin/settings.html`, `templates/archive/detail.html`, `templates/base.html` … (+2) |
| ↻ | `refresh` | 8 | 8 | `static/js/einheit_modus.js`, `static/js/lagefuehrung.js`, `templates/admin/server_log.html`, `templates/archive/detail.html` … (+4) |
| ▼ | `chevron-down` | 8 | 8 | `templates/admin/device_tokens.html`, `templates/admin/medienverwaltung.html`, `templates/admin/members.html`, `templates/admin/wasserstellen.html` … (+3) |
| ❄ | `snowflake` | 7 | 7 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| ⏰ | `alarm` | 7 | 7 | `services/katplan_qs_service.py`, `static/js/app.js`, `templates/incident/_dashboard_body.html`, `templates/incident/_message_card.html` … (+2) |
| 🚪 | `door` | 7 | 7 | `templates/_profil_aktionen.html`, `templates/base.html`, `templates/objekt/_zusatzadressen.html`, `templates/pdf/_emoji_font_face.html` … (+1) |
| 📚 | `books` | 7 | 7 | `templates/admin/_layout.html`, `templates/base.html`, `templates/index.html`, `templates/nachschlagewerke/start.html` |
| ▲ | `chevron-up` | 7 | 7 | `templates/admin/device_tokens.html`, `templates/admin/medienverwaltung.html`, `templates/admin/members.html`, `templates/admin/wasserstellen.html` … (+2) |
| 🗂 | `folders` | 7 | 7 | `templates/admin/gsl_einstellungen.html`, `templates/admin/index.html`, `templates/admin/system_settings.html`, `templates/incident_major/_lage_layout.html` … (+1) |
| 🕐 | `clock` | 7 | 7 | `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html`, `templates/archive/detail.html`, `templates/incident_major/_lage_layout.html` |
| 🔒 | `lock` | 7 | 7 | `templates/admin/medienverwaltung.html`, `templates/incident/_alarm_edit_modal.html`, `templates/incident/board.html`, `templates/objekt/kataloge.html` … (+2) |
| 💡 | `bulb` | 7 | 7 | `templates/admin/wasserstellen.html`, `templates/incident/_dashboard_body.html`, `templates/incident/_message_card.html`, `templates/incident/_message_modal.html` … (+2) |
| ▾ | `chevron-down` | 7 | 7 | `templates/base.html`, `templates/gateway/druckregeln.html`, `templates/incident/board.html`, `templates/incident_major/_board_sector_filter.html` |
| 🟡 | `=status` | 7 | 7 | `templates/incident/_dashboard_body.html`, `templates/incident/_task_card.html`, `templates/incident/_task_modal.html`, `templates/incident/_task_status_pills.html` … (+2) |
| ⚪ | `=status` | 7 | 7 | `templates/incident/_dashboard_body.html`, `templates/incident/_message_card.html`, `templates/incident/_message_modal.html`, `templates/incident/_task_card.html` … (+1) |
| 🏷 | `tag` | 7 | 6 | `templates/incident/lagefuehrung.html`, `templates/objekt/_merkmale.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/lagebericht.html` … (+1) |
| ⏳ | `hourglass` | 6 | 6 | `main.py`, `static/js/einheit_modus.js`, `templates/incident_major/dashboard.html`, `templates/objekt/_dokumente.html` |
| ☀ | `sun` | 6 | 6 | `models/objekt.py`, `services/objekt_service.py`, `templates/incident/_dashboard_body.html`, `templates/objekt/infoscreen_alarm.html` … (+1) |
| ➜ | `=fw` | 6 | 6 | `services/objekt_symbol_service.py`, `static/js/lagefuehrung.js`, `static/js/objekt_karte.js` |
| 📡 | `antenna` | 6 | 6 | `templates/admin/_layout.html`, `templates/admin/device_tokens.html`, `templates/incident/_dashboard_body.html` |
| 📎 | `paperclip` | 6 | 5 | `templates/admin/_mail_eingang_content.html`, `templates/incident/_message_card.html`, `templates/incident/_message_modal.html`, `templates/katplan/_baum.html` … (+2) |
| 🗑 | `trash` | 6 | 6 | `templates/annotate/editor.html`, `templates/archive/detail.html`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html` … (+2) |
| 📅 | `calendar` | 6 | 6 | `templates/base.html`, `templates/index.html`, `templates/probenplanung/_dashboard_kachel.html`, `templates/termin/liste.html` |
| ▸ | `chevron-right` | 6 | 6 | `templates/foerderstrecke/wizard.html`, `templates/gateway/druckregeln.html`, `templates/incident/lagefuehrung.html`, `templates/incident_major/board.html` … (+1) |
| 📝 | `note` | 6 | 6 | `templates/incident/lagefuehrung.html`, `templates/incident_major/_lage_layout.html`, `templates/incident_major/dashboard.html`, `templates/incident_major/lagedokument.html` … (+1) |
| ☣ | `biohazard` | 5 | 5 | `models/major_incident.py`, `templates/incident/lagefuehrung.html`, `templates/nachschlagewerke/gefahrgut.html`, `templates/nachschlagewerke/gefahrgut_detail.html` … (+1) |
| 🖥 | `device-desktop` | 5 | 3 | `services/teams_card.py`, `templates/admin/_layout.html`, `templates/archive/detail.html`, `templates/incident/info.html` |
| ⛈ | `cloud-storm` | 5 | 5 | `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| ↺ | `rotate` | 5 | 5 | `static/js/address-autocomplete.js`, `static/js/lagefuehrung.js`, `templates/foerderstrecke/wizard.html`, `templates/objekt/_dokumente.html` … (+1) |
| ⬇ | `download` | 5 | 5 | `static/js/media-viewer.js`, `templates/admin/server_log.html`, `templates/incident_major/zeitreise.html`, `templates/stats/fahrtenbuch.html` |
| 📜 | `history` | 5 | 5 | `templates/admin/_layout.html`, `templates/objekt/_protokoll.html`, `templates/objekt/changelog.html`, `templates/objekt/liste.html` … (+1) |
| 📧 | `mail` | 5 | 5 | `templates/admin/_layout.html`, `templates/admin/users.html`, `templates/base.html`, `templates/index.html` |
| 🚑 | `ambulance` | 5 | 5 | `templates/incident/_dashboard_body.html`, `templates/incident/board.html`, `templates/nachschlagewerke/rettungskarten.html`, `templates/nachschlagewerke/start.html` … (+1) |
| 📨 | `mail-forward` | 4 | 3 | `services/site_pages.py`, `templates/incident/_vehicle_card.html`, `templates/incident_major/dashboard.html`, `templates/mailing/lists_index.html` |
| 🏠 | `home` | 4 | 4 | `templates/admin/_layout.html`, `templates/base.html`, `templates/objekt/infoscreen_alarm.html` |
| 🧵 | `messages` | 4 | 4 | `templates/admin/_layout.html`, `templates/admin/foerder_schlaeuche.html`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html` |
| 🌐 | `world` | 4 | 4 | `templates/admin/_layout.html`, `templates/probenplanung/verwaltung_oeffentlich.html` |
| 💬 | `message` | 4 | 4 | `templates/admin/_layout.html`, `templates/admin/index.html`, `templates/incident/_dashboard_body.html` |
| 💾 | `device-floppy` | 4 | 4 | `templates/admin/_layout.html`, `templates/admin/medienverwaltung.html`, `templates/admin/org_backup.html`, `templates/admin/sysadmin_quotas.html` |
| ⏱ | `stopwatch` | 4 | 4 | `templates/admin/org_backup.html`, `templates/incident/lagefuehrung.html`, `templates/incident_major/_site_card.html` |
| 📲 | `device-mobile-message` | 4 | 4 | `templates/base.html` |
| 🧭 | `compass` | 4 | 4 | `templates/incident/_subnav.html`, `templates/incident/lagefuehrung.html`, `templates/incident_major/dashboard.html` |
| ❌ | `circle-x` | 4 | 4 | `templates/incident/board.html`, `templates/incident/info.html`, `templates/objekt/infoscreen_alarm.html` |
| 🎤 | `microphone` | 4 | 4 | `templates/incident/board.html` |
| 🟠 | `=status` | 3 | 3 | `core/templating.py`, `templates/incident/_person_card.html`, `templates/incident_major/_kraefteuebersicht.html` |
| ⛔ | `ban` | 3 | 3 | `main.py`, `middleware/csrf.py`, `templates/gateway/_druckregel_simulation.html` |
| 🌊 | `ripple` | 3 | 3 | `models/major_incident.py`, `services/weather_service.py` |
| 💥 | `bomb` | 3 | 3 | `models/objekt.py`, `services/objekt_service.py`, `templates/objekt/kataloge.html` |
| 🧪 | `flask` | 3 | 3 | `models/objekt.py`, `services/objekt_service.py`, `templates/gateway/druckregeln.html` |
| 🔀 | `arrows-shuffle` | 3 | 3 | `services/katplan_typen.py`, `templates/gateway/detail.html` |
| ↩ | `arrow-back-up` | 3 | 2 | `services/road_closure_notify_service.py`, `templates/incident/funkjournal.html`, `templates/incident_major/_funkjournal_rows.html` |
| ⏸ | `player-pause` | 3 | 3 | `static/js/lagefuehrung.js`, `templates/weather/index.html` |
| ➕ | `plus` | 3 | 3 | `templates/_profil_aktionen.html`, `templates/base.html`, `templates/incident_major/ressourcen.html` |
| 🗓 | `calendar-event` | 3 | 3 | `templates/admin/_layout.html`, `templates/admin/settings.html`, `templates/termin/detail.html` |
| 🗄 | `archive` | 3 | 3 | `templates/admin/_layout.html`, `templates/admin/backup.html`, `templates/index.html` |
| ✖ | `x` | 3 | 3 | `templates/annotate/editor.html`, `templates/gateway/_druckhistorie.html` |
| 🧾 | `receipt` | 3 | 3 | `templates/archive/detail.html`, `templates/gateway/_druckhistorie.html`, `templates/objekt/_dokumente.html` |
| 🎲 | `dice-5` | 3 | 3 | `templates/base.html`, `templates/index.html` |
| 📘 | `book-2` | 3 | 3 | `templates/base.html`, `templates/index.html` |
| 🕓 | `clock` | 3 | 3 | `templates/gateway/agent_detail.html`, `templates/gateway/detail.html` |
| 🌡 | `temperature` | 3 | 3 | `templates/incident/_dashboard_body.html`, `templates/objekt/infoscreen_alarm.html` |
| ⭐ | `star` | 3 | 3 | `templates/incident/funkjournal.html` |
| 🔄 | `refresh` | 3 | 3 | `templates/incident_major/meldungen.html`, `templates/objekt/_datenpflege.html`, `templates/objekt/detail.html` |
| 🏘 | `buildings` | 3 | 3 | `templates/objekt/_wohnanlage.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| 🌨 | `cloud-snow` | 3 | 3 | `templates/objekt/infoscreen_alarm.html` |
| ⚫ | `=status` | 2 | 2 | `core/templating.py`, `templates/incident/_person_card.html` |
| ⌛ | `hourglass` | 2 | 2 | `main.py`, `services/katplan_qs_service.py` |
| 🏚 | `home-x` | 2 | 2 | `models/major_incident.py`, `templates/admin/index.html` |
| 🅿 | `parking` | 2 | 2 | `services/objekt_service.py`, `templates/pdf/_emoji_font_face.html` |
| 🔁 | `repeat` | 2 | 1 | `services/road_closure_notify_service.py`, `templates/gateway/agent_detail.html` |
| 🧰 | `box` | 2 | 2 | `static/js/lagefuehrung.js`, `templates/incident/lagefuehrung.html` |
| ○ | `=status` | 2 | 2 | `static/js/probe-appell.js`, `templates/probenplanung/teilnehmer_appell.html` |
| 💻 | `device-laptop` | 2 | 2 | `templates/_print_dialog.html`, `templates/gateway/agent_detail.html` |
| 📖 | `book` | 2 | 2 | `templates/admin/_layout.html`, `templates/admin/index.html` |
| 🎖 | `award` | 2 | 2 | `templates/admin/_layout.html` |
| 📑 | `files` | 2 | 2 | `templates/admin/_layout.html`, `templates/katplan/_anhaenge_anzeige.html` |
| 📺 | `device-tv` | 2 | 2 | `templates/admin/_layout.html`, `templates/objekt/infoscreen_verwaltung.html` |
| 🏛 | `building-bank` | 2 | 2 | `templates/admin/_layout.html`, `templates/katplan/_links_anzeige.html` |
| ⬆ | `upload` | 2 | 2 | `templates/admin/_layout.html`, `templates/base.html` |
| ⛅ | `cloud` | 2 | 2 | `templates/admin/index.html`, `templates/objekt/infoscreen_alarm.html` |
| 🔌 | `plug` | 2 | 2 | `templates/admin/org_backup.html`, `templates/gateway/detail.html` |
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
| 🧱 | `=fw` | 1 | 1 | `models/major_incident.py` |
| 🌉 | `=fw` | 1 | 1 | `models/major_incident.py` |
| 📈 | `chart-line` | 1 | 1 | `models/major_incident.py` |
| 🪵 | `=fw` | 1 | 1 | `models/major_incident.py` |
| ⇄ | `=text` | 1 | 0 | `models/objekt.py` |
| 📩 | `mail-opened` | 1 | 1 | `services/incident_service.py` |
| ☎ | `phone` | 1 | 1 | `services/katplan_qs_service.py` |
| ❔ | `help-circle` | 1 | 1 | `services/katplan_qs_service.py` |
| 🤖 | `robot` | 1 | 1 | `services/katplan_qs_service.py` |
| 📕 | `book` | 1 | 1 | `services/objekt_service.py` |
| 🛗 | `elevator` | 1 | 1 | `services/objekt_service.py` |
| 🚻 | `users-group` | 1 | 1 | `services/objekt_service.py` |
| 🌀 | `=fw` | 1 | 1 | `services/objekt_service.py` |
| ⏩ | `player-track-next` | 1 | 0 | `services/road_closure_notify_service.py` |
| 🧊 | `snowflake` | 1 | 1 | `services/weather_service.py` |
| 🫂 | `users-group` | 1 | 1 | `templates/admin/_layout.html` |
| 🔐 | `lock-password` | 1 | 1 | `templates/admin/_layout.html` |
| 📣 | `speakerphone` | 1 | 1 | `templates/admin/_layout.html` |
| 🚦 | `traffic-lights` | 1 | 1 | `templates/admin/_layout.html` |
| 🎭 | `masks-theater` | 1 | 1 | `templates/admin/_layout.html` |
| 🌍 | `world` | 1 | 1 | `templates/admin/_layout.html` |
| 📵 | `device-mobile-off` | 1 | 1 | `templates/admin/device_token_detail.html` |
| 🔋 | `battery` | 1 | 1 | `templates/admin/device_tokens.html` |
| 🛠 | `tools` | 1 | 1 | `templates/admin/index.html` |
| ♻ | `recycle` | 1 | 1 | `templates/admin/org_backup.html` |
| ⭳ | `download` | 1 | 1 | `templates/admin/wasserstellen.html` |
| ✥ | `pointer` | 1 | 1 | `templates/annotate/editor.html` |
| ◯ | `circle` | 1 | 1 | `templates/annotate/editor.html` |
| ⌫ | `eraser` | 1 | 1 | `templates/annotate/editor.html` |
| ↶ | `arrow-back-up` | 1 | 1 | `templates/annotate/editor.html` |
| ↷ | `arrow-forward-up` | 1 | 1 | `templates/annotate/editor.html` |
| 🧍 | `user-check` | 1 | 1 | `templates/archive/detail.html` |
| 🎛 | `adjustments` | 1 | 1 | `templates/base.html` |
| 🛟 | `lifebuoy` | 1 | 1 | `templates/breathing/board.html` |
| ⬅ | `arrow-left` | 1 | 1 | `templates/breathing/board.html` |
| ⏹ | `player-stop` | 1 | 1 | `templates/foerderstrecke/wizard.html` |
| ✔ | `check` | 1 | 1 | `templates/gateway/_druckhistorie.html` |
| 🧩 | `puzzle` | 1 | 1 | `templates/gateway/detail.html` |
| 🩸 | `droplet-half-2` | 1 | 1 | `templates/gateway/detail.html` |
| 🔧 | `tool` | 1 | 1 | `templates/gateway/druckregeln.html` |
| 🏥 | `building-hospital` | 1 | 1 | `templates/incident/_dashboard_body.html` |
| 📏 | `ruler` | 1 | 1 | `templates/incident/lagefuehrung.html` |
| ⭕ | `circle-dashed` | 1 | 1 | `templates/incident/lagefuehrung.html` |
| 📸 | `camera` | 1 | 1 | `templates/incident/lagefuehrung.html` |
| ↵ | `corner-down-left` | 1 | 1 | `templates/incident_major/_cross_marker_panel.html` |
| ▣ | `=fw` | 1 | 1 | `templates/incident_major/karte.html` |
| 🛡 | `shield` | 1 | 1 | `templates/incident_major/public_report.html` |
| ✈ | `plane` | 1 | 1 | `templates/incident_major/public_report.html` |
| ❓ | `help-circle` | 1 | 1 | `templates/index.html` |
| 🔔 | `bell` | 1 | 1 | `templates/objekt/_benachrichtigung.html` |
| 🙋 | `hand-stop` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| 🌤 | `sun-low` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| ☁ | `cloud` | 1 | 1 | `templates/objekt/infoscreen_alarm.html` |
| ⓘ | `info-circle` | 1 | 1 | `templates/objekt/pflege_review.html` |
| ▬ | `=fw` | 1 | 1 | `templates/pdf/foerderstrecke.html` |
| ↳ | `=text` | 1 | 1 | `templates/pdf/incident_report.html` |
| 👁 | `eye` | 1 | 1 | `templates/public/page.html` |
| ⌄ | `chevron-down` | 1 | 1 | `templates/verleih/_ausleihe_form.html` |

\* ohne Kommentare und externe Kanäle.

## Dateien mit den meisten migrierbaren Treffern

| Datei | Treffer |
|---|---:|
| `app/templates/base.html` | 60 |
| `app/templates/admin/_layout.html` | 58 |
| `app/templates/objekt/infoscreen_alarm.html` | 52 |
| `app/templates/incident/board.html` | 36 |
| `app/templates/incident_major/_lage_layout.html` | 36 |
| `app/templates/incident/_dashboard_body.html` | 34 |
| `app/templates/foerderstrecke/wizard.html` | 30 |
| `app/templates/incident_major/karte.html` | 28 |
| `app/templates/breathing/board.html` | 26 |
| `app/templates/incident/lagefuehrung.html` | 26 |
| `app/templates/index.html` | 21 |
| `app/templates/admin/device_tokens.html` | 20 |
| `app/templates/archive/detail.html` | 20 |
| `app/templates/gateway/detail.html` | 20 |
| `app/services/objekt_service.py` | 18 |
| `app/static/js/lagefuehrung.js` | 18 |
| `app/templates/gateway/druckregeln.html` | 18 |
| `app/templates/einheit/einheit.html` | 17 |
| `app/templates/incident_major/_site_detail.html` | 17 |
| `app/templates/admin/index.html` | 17 |
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
| `app/templates/objekt/_einsatz_inhalt.html` | 11 |
| `app/models/major_incident.py` | 11 |
| `app/services/weather_service.py` | 11 |
| `app/templates/incident_major/board.html` | 11 |
| `app/templates/probenplanung/teilnehmer_appell.html` | 10 |
| `app/templates/pdf/objektblatt.html` | 10 |
| `app/templates/incident_major/_site_card.html` | 10 |
| `app/templates/incident_major/lagedokument.html` | 9 |
| `app/templates/incident_major/public_report.html` | 9 |
| `app/templates/incident_major/stab.html` | 9 |
| `app/templates/incident/_person_card.html` | 9 |
| `app/templates/incident_major/_kraefteuebersicht.html` | 9 |
| `app/static/js/einsatz_info.js` | 9 |
| `app/templates/admin/gsl_einstellungen.html` | 9 |
| `app/templates/admin/wasserstellen.html` | 9 |
| `app/services/katplan_qs_service.py` | 9 |
| `app/templates/termin/liste.html` | 9 |
| `app/templates/admin/system_settings.html` | 9 |
| `app/templates/admin/settings.html` | 9 |
| `app/static/js/probe-appell.js` | 8 |
| `app/templates/admin/push_notifications.html` | 8 |
| `app/models/objekt.py` | 8 |
| `app/services/katplan_typen.py` | 8 |
| `app/templates/foerderstrecke/maschinist.html` | 8 |
| `app/templates/objekt/_gefahren.html` | 8 |
| `app/templates/admin/medienverwaltung.html` | 8 |
| `app/templates/weather/index.html` | 8 |
| `app/templates/incident/_address_modal.html` | 7 |
