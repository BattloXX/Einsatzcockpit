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
| markup | 1024 |
| python | 276 |
| kommentar | 209 |
| js | 65 |
| extern | 60 |
| css | 12 |
| option | 10 |

## Zuordnung

| Zeichen | Ziel | Treffer | migrieren* | Dateien (Auszug) |
|---|---|---:|---:|---|
| → | `=text` | 488 | 286 | `cli.py`, `config.py`, `core/crypto.py`, `core/dependencies.py` … (+170) |
| ⚠ | `alert-triangle` | 89 | 81 | `models/objekt.py`, `routers/ui_incident.py`, `routers/ui_infoscreen_alarm.py`, `routers/ui_lagefuehrung.py` … (+44) |
| ✓ | `check` | 75 | 65 | `cli.py`, `models/sms.py`, `routers/ui_archive.py`, `routers/ui_termin.py` … (+40) |
| ← | `=text` | 50 | 48 | `static/css/tailwind.input.css`, `static/js/katplan_editor.js`, `templates/admin/device_token_detail.html`, `templates/admin/foerder_pumpe_form.html` … (+43) |
| 📍 | `map-pin` | 49 | 49 | `models/major_incident.py`, `services/katplan_qs_service.py`, `services/katplan_typen.py`, `static/js/lagefuehrung.js` … (+22) |
| 🖨 | `printer` | 45 | 45 | `routers/ui_druck.py`, `templates/_print_dialog.html`, `templates/archive/detail.html`, `templates/gateway/_druckhistorie.html` … (+31) |
| 📄 | `file-text` | 33 | 33 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `services/site_pages.py`, `static/js/rettungskarten_katalog.js` … (+18) |
| ✕ | `x` | 30 | 30 | `static/js/annotate.js`, `static/js/lagefuehrung.js`, `templates/admin/device_tokens.html`, `templates/gateway/agent_detail.html` … (+18) |
| 🚒 | `firetruck` | 27 | 23 | `routers/ui_teams_bot.py`, `services/alarm_outbox.py`, `services/gefahrgut_service.py`, `services/objekt_service.py` … (+17) |
| ✗ | `x` | 25 | 13 | `cli.py`, `services/sms_inbox_service.py`, `static/js/address-picker.js`, `static/js/probe-appell.js` … (+4) |
| 📋 | `clipboard-list` | 19 | 19 | `services/incident_service.py`, `services/site_pages.py`, `templates/admin/device_tokens.html`, `templates/admin/foerder_pumpen.html` … (+10) |
| 🗺 | `map` | 18 | 17 | `services/teams_card.py`, `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html`, `templates/einheit/einheit.html` … (+12) |
| ● | `=status` | 18 | 18 | `templates/admin/settings.html`, `templates/einheit/einheit.html`, `templates/incident_major/_sektoren_grid.html`, `templates/incident_major/karte.html` … (+2) |
| ↔ | `=text` | 17 | 15 | `models/major_incident.py`, `models/master.py`, `models/objekt.py`, `routers/ui_major_incident.py` … (+12) |
| ☰ | `menu-2` | 17 | 17 | `templates/admin/default_messages.html`, `templates/admin/dispatch_order.html`, `templates/admin/lage_hints.html`, `templates/admin/message_suggestions.html` … (+2) |
| ↗ | `external-link` | 17 | 17 | `templates/admin/dibos_einsaetze.html`, `templates/admin/page_edit.html`, `templates/admin/pages_list.html`, `templates/annotate/editor.html` … (+7) |
| ✨ | `sparkles` | 16 | 16 | `routers/ui_incident.py`, `templates/admin/ai_prompts.html`, `templates/admin/sysadmin_quotas.html`, `templates/admin/system_settings.html` … (+4) |
| 🔥 | `flame` | 15 | 14 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `static/js/lagefuehrung.js` … (+7) |
| 🔗 | `link` | 14 | 14 | `services/katplan_qs_service.py`, `static/js/nachschlagewerke.js`, `templates/admin/vehicles.html`, `templates/foerderstrecke/wizard.html` … (+10) |
| 🌧 | `cloud-rain` | 14 | 13 | `static/js/weather_layer.js`, `templates/incident_major/karte.html`, `templates/objekt/infoscreen_alarm.html`, `templates/weather/_data_section.html` … (+1) |
| 👥 | `users` | 13 | 13 | `services/site_pages.py`, `templates/admin/gsl_einstellungen.html`, `templates/admin/settings.html`, `templates/admin/system_settings.html` … (+5) |
| 📥 | `inbox` | 13 | 13 | `templates/admin/backup.html`, `templates/admin/einsatz_import.html`, `templates/admin/gsl_einstellungen.html`, `templates/admin/konfig.html` … (+5) |
| 💧 | `droplet` | 12 | 11 | `models/major_incident.py`, `services/objekt_service.py`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html` … (+2) |
| ↓ | `=text` | 12 | 12 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/admin/device_tokens.html`, `templates/admin/lage_hints.html` … (+8) |
| ✅ | `circle-check` | 12 | 10 | `services/katplan_typen.py`, `services/road_closure_notify_service.py`, `templates/admin/wasserstellen.html`, `templates/foerderstrecke/wizard.html` … (+6) |
| 📱 | `device-mobile` | 12 | 12 | `templates/admin/device_tokens.html`, `templates/incident_major/_kraefteuebersicht.html`, `templates/incident_major/_site_detail.html`, `templates/incident_major/public_report.html` … (+2) |
| ⇒ | `=text` | 11 | 4 | `config.py`, `models/master.py`, `routers/api_weather.py`, `routers/ui_settings.py` … (+4) |
| ⚡ | `bolt` | 11 | 11 | `models/major_incident.py`, `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py` … (+5) |
| 📻 | `radio` | 11 | 11 | `services/objekt_service.py`, `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html`, `templates/incident_major/_kraefteuebersicht.html` … (+4) |
| 🎯 | `target` | 11 | 10 | `templates/admin/foerder_kalibrierung.html`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html`, `templates/gateway/agent_detail.html` … (+2) |
| ✎ | `pencil` | 11 | 11 | `templates/admin/medienverwaltung.html`, `templates/gateway/detail.html`, `templates/incident_major/_funkjournal_rows.html`, `templates/objekt/_bma.html` … (+5) |
| 📞 | `phone` | 11 | 10 | `templates/incident_major/public_report.html`, `templates/incident_major/public_sms_error.html`, `templates/incident_major/public_verify.html`, `templates/objekt/_einsatz_inhalt.html` … (+5) |
| 🚨 | `urgent` | 10 | 9 | `routers/api_v1.py`, `services/teams_card.py`, `static/js/einsatz_info.js`, `templates/incident_major/lage_overview.html` … (+5) |
| 🔍 | `search` | 10 | 10 | `templates/einheit/einheit.html`, `templates/incident_major/_site_card.html`, `templates/incident_major/_site_detail.html`, `templates/objekt/formular.html` |
| ↑ | `=text` | 9 | 9 | `routers/ui_major_incident.py`, `services/funkjournal_service.py`, `templates/admin/lage_hints.html`, `templates/incident_major/_funkjournal_rows.html` … (+5) |
| 📦 | `package` | 9 | 9 | `services/katplan_typen.py`, `templates/admin/backup.html`, `templates/admin/gsl_einstellungen.html`, `templates/admin/konfig.html` … (+3) |
| ✏ | `pencil` | 9 | 8 | `services/road_closure_notify_service.py`, `templates/annotate/editor.html`, `templates/incident_major/_cross_marker_panel.html`, `templates/incident_major/_journal_entry_detail.html` … (+4) |
| 🔎 | `zoom-in` | 9 | 9 | `templates/admin/members.html`, `templates/admin/wasserstellen.html`, `templates/gateway/detail.html`, `templates/nachschlagewerke/rettungskarten.html` … (+4) |
| ❄ | `snowflake` | 7 | 7 | `models/objekt.py`, `services/objekt_service.py`, `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| 🏢 | `building` | 7 | 7 | `services/katplan_typen.py`, `static/js/lagefuehrung.js`, `templates/gateway/druckregeln.html`, `templates/incident/lagefuehrung_druck.html` … (+3) |
| ▲ | `chevron-up` | 7 | 7 | `templates/admin/device_tokens.html`, `templates/admin/medienverwaltung.html`, `templates/admin/members.html`, `templates/admin/wasserstellen.html` … (+2) |
| ▼ | `chevron-down` | 7 | 7 | `templates/admin/device_tokens.html`, `templates/admin/medienverwaltung.html`, `templates/admin/members.html`, `templates/admin/wasserstellen.html` … (+2) |
| 🕐 | `clock` | 7 | 7 | `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html`, `templates/archive/detail.html`, `templates/incident_major/_lage_layout.html` |
| 📊 | `chart-bar` | 7 | 7 | `templates/archive/detail.html`, `templates/gateway/druckregeln.html`, `templates/incident_major/_lage_layout.html`, `templates/incident_major/_stab_journal.html` … (+1) |
| 📷 | `camera` | 7 | 7 | `templates/incident_major/_cross_marker_card.html`, `templates/incident_major/_cross_marker_panel.html`, `templates/incident_major/_site_card.html`, `templates/incident_major/_site_detail.html` … (+3) |
| 🌦 | `cloud-rain` | 7 | 7 | `templates/incident_major/board.html`, `templates/objekt/infoscreen_alarm.html`, `templates/objekt/infoscreen_verwaltung.html`, `templates/weather/_data_section.html` … (+1) |
| 🔑 | `key` | 6 | 6 | `services/objekt_service.py`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html`, `templates/objekt/infoscreen_alarm.html` … (+2) |
| ➜ | `=fw` | 6 | 6 | `services/objekt_symbol_service.py`, `static/js/lagefuehrung.js`, `static/js/objekt_karte.js` |
| ↻ | `refresh` | 6 | 6 | `static/js/einheit_modus.js`, `static/js/lagefuehrung.js`, `templates/admin/server_log.html`, `templates/archive/detail.html` … (+2) |
| 📢 | `speakerphone` | 6 | 6 | `static/js/lagefuehrung.js`, `templates/incident/lagefuehrung_druck.html`, `templates/incident_major/dashboard.html` |
| ▶ | `player-play` | 6 | 6 | `static/js/lagefuehrung.js`, `templates/einheit/einheit.html`, `templates/foerderstrecke/maschinist.html`, `templates/media/gallery.html` … (+1) |
| ✉ | `mail` | 6 | 6 | `static/js/probe-appell.js`, `templates/gateway/druckregeln.html`, `templates/objekt/_kontakte.html`, `templates/probenplanung/teilnehmer_appell.html` |
| 🗂 | `folders` | 6 | 6 | `templates/admin/gsl_einstellungen.html`, `templates/admin/system_settings.html`, `templates/incident_major/_lage_layout.html`, `templates/objekt/_einsatz_inhalt.html` |
| 🖼 | `photo` | 6 | 6 | `templates/admin/medienverwaltung.html`, `templates/admin/page_edit.html`, `templates/incident_major/_cross_marker_panel.html`, `templates/incident_major/_site_detail.html` … (+2) |
| ⚙ | `settings` | 6 | 6 | `templates/gateway/_tabs.html`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html`, `templates/gateway/druckregeln.html` … (+1) |
| 🏷 | `tag` | 6 | 5 | `templates/objekt/_merkmale.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/lagebericht.html`, `templates/pdf/objektblatt.html` |
| 🚧 | `barrier-block` | 5 | 2 | `models/major_incident.py`, `services/road_closure_notify_service.py`, `templates/incident_major/public_report.html` |
| ☀ | `sun` | 5 | 5 | `models/objekt.py`, `services/objekt_service.py`, `templates/objekt/infoscreen_alarm.html`, `templates/weather/_data_section.html` |
| 👤 | `user` | 5 | 5 | `services/katplan_qs_service.py`, `services/katplan_typen.py`, `templates/incident_major/_kraefteuebersicht.html`, `templates/katplan/_links_anzeige.html` |
| ⛈ | `cloud-storm` | 5 | 5 | `services/weather_service.py`, `templates/objekt/infoscreen_alarm.html` |
| ↺ | `rotate` | 5 | 5 | `static/js/address-autocomplete.js`, `static/js/lagefuehrung.js`, `templates/foerderstrecke/wizard.html`, `templates/objekt/_dokumente.html` … (+1) |
| ⏳ | `hourglass` | 5 | 5 | `static/js/einheit_modus.js`, `templates/incident_major/dashboard.html`, `templates/objekt/_dokumente.html` |
| 🎬 | `movie` | 5 | 5 | `templates/admin/medienverwaltung.html`, `templates/media/gallery.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/incident_report.html` |
| 🔒 | `lock` | 5 | 5 | `templates/admin/medienverwaltung.html`, `templates/objekt/kataloge.html`, `templates/verleih/_ausleihe_detail.html`, `templates/verleih/_ausleihe_form.html` |
| 🗑 | `trash` | 5 | 5 | `templates/annotate/editor.html`, `templates/archive/detail.html`, `templates/gateway/agent_detail.html`, `templates/gateway/detail.html` … (+1) |
| ▸ | `chevron-right` | 5 | 5 | `templates/foerderstrecke/wizard.html`, `templates/gateway/druckregeln.html`, `templates/incident_major/board.html`, `templates/weather/index.html` |
| 📝 | `note` | 5 | 5 | `templates/incident_major/_lage_layout.html`, `templates/incident_major/dashboard.html`, `templates/incident_major/lagedokument.html`, `templates/incident_major/stab.html` |
| ☣ | `biohazard` | 4 | 4 | `models/major_incident.py`, `templates/nachschlagewerke/gefahrgut.html`, `templates/nachschlagewerke/gefahrgut_detail.html`, `templates/nachschlagewerke/start.html` |
| 🫁 | `lungs` | 4 | 4 | `services/site_pages.py`, `templates/admin/settings.html`, `templates/archive/detail.html` |
| 📎 | `paperclip` | 4 | 3 | `templates/admin/_mail_eingang_content.html`, `templates/katplan/_baum.html`, `templates/mailing/campaign_detail.html`, `templates/pdf/incident_report.html` |
| 📡 | `antenna` | 4 | 4 | `templates/admin/device_tokens.html` |
| 🚰 | `fire-hydrant` | 4 | 4 | `templates/admin/foerder_pumpen.html`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html`, `templates/public/alarm_summary.html` |
| 📜 | `history` | 4 | 4 | `templates/objekt/_protokoll.html`, `templates/objekt/changelog.html`, `templates/objekt/liste.html`, `templates/pdf/lagebericht.html` |
| 🌊 | `ripple` | 3 | 3 | `models/major_incident.py`, `services/weather_service.py` |
| 💥 | `bomb` | 3 | 3 | `models/objekt.py`, `services/objekt_service.py`, `templates/objekt/kataloge.html` |
| 🧪 | `flask` | 3 | 3 | `models/objekt.py`, `services/objekt_service.py`, `templates/gateway/druckregeln.html` |
| 🔀 | `arrows-shuffle` | 3 | 3 | `services/katplan_typen.py`, `templates/gateway/detail.html` |
| 📨 | `mail-forward` | 3 | 2 | `services/site_pages.py`, `templates/incident_major/dashboard.html`, `templates/mailing/lists_index.html` |
| 🖥 | `device-desktop` | 3 | 1 | `services/teams_card.py`, `templates/archive/detail.html` |
| ⏸ | `player-pause` | 3 | 3 | `static/js/lagefuehrung.js`, `templates/weather/index.html` |
| 🧵 | `messages` | 3 | 3 | `templates/admin/foerder_schlaeuche.html`, `templates/foerderstrecke/maschinist.html`, `templates/foerderstrecke/wizard.html` |
| 💾 | `device-floppy` | 3 | 3 | `templates/admin/medienverwaltung.html`, `templates/admin/org_backup.html`, `templates/admin/sysadmin_quotas.html` |
| ⬇ | `download` | 3 | 3 | `templates/admin/server_log.html`, `templates/incident_major/zeitreise.html`, `templates/stats/fahrtenbuch.html` |
| ✖ | `x` | 3 | 3 | `templates/annotate/editor.html`, `templates/gateway/_druckhistorie.html` |
| 🧾 | `receipt` | 3 | 3 | `templates/archive/detail.html`, `templates/gateway/_druckhistorie.html`, `templates/objekt/_dokumente.html` |
| 🕓 | `clock` | 3 | 3 | `templates/gateway/agent_detail.html`, `templates/gateway/detail.html` |
| ▾ | `chevron-down` | 3 | 3 | `templates/gateway/druckregeln.html`, `templates/incident_major/_board_sector_filter.html` |
| 🚁 | `helicopter` | 3 | 3 | `templates/incident_major/_lage_layout.html` |
| 🔄 | `refresh` | 3 | 3 | `templates/incident_major/meldungen.html`, `templates/objekt/_datenpflege.html`, `templates/objekt/detail.html` |
| 🚑 | `ambulance` | 3 | 3 | `templates/nachschlagewerke/rettungskarten.html`, `templates/nachschlagewerke/start.html`, `templates/objekt/infoscreen_alarm.html` |
| 🏘 | `buildings` | 3 | 3 | `templates/objekt/_wohnanlage.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| 🚪 | `door` | 3 | 3 | `templates/objekt/_zusatzadressen.html`, `templates/pdf/_emoji_font_face.html`, `templates/pdf/objektblatt.html` |
| 🌨 | `cloud-snow` | 3 | 3 | `templates/objekt/infoscreen_alarm.html` |
| 📅 | `calendar` | 3 | 3 | `templates/probenplanung/_dashboard_kachel.html`, `templates/termin/liste.html` |
| 🅿 | `parking` | 2 | 2 | `services/objekt_service.py`, `templates/pdf/_emoji_font_face.html` |
| 🔁 | `repeat` | 2 | 1 | `services/road_closure_notify_service.py`, `templates/gateway/agent_detail.html` |
| ↩ | `arrow-back-up` | 2 | 1 | `services/road_closure_notify_service.py`, `templates/incident_major/_funkjournal_rows.html` |
| ○ | `=status` | 2 | 2 | `static/js/probe-appell.js`, `templates/probenplanung/teilnehmer_appell.html` |
| 💻 | `device-laptop` | 2 | 2 | `templates/_print_dialog.html`, `templates/gateway/agent_detail.html` |
| 🟢 | `=status` | 2 | 2 | `templates/_print_dialog.html`, `templates/incident_major/_einheit_dispatch_options.html` |
| 🔴 | `=status` | 2 | 2 | `templates/_print_dialog.html`, `templates/incident_major/_einheit_dispatch_options.html` |
| 🔌 | `plug` | 2 | 2 | `templates/admin/org_backup.html`, `templates/gateway/detail.html` |
| ⏱ | `stopwatch` | 2 | 2 | `templates/admin/org_backup.html`, `templates/incident_major/_site_card.html` |
| 🗓 | `calendar-event` | 2 | 2 | `templates/admin/settings.html`, `templates/termin/detail.html` |
| ▭ | `square` | 2 | 2 | `templates/annotate/editor.html`, `templates/incident_major/karte.html` |
| ② | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| ① | `=text` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🛣 | `route` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🚩 | `flag` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| ⏺ | `player-record` | 2 | 2 | `templates/foerderstrecke/wizard.html` |
| 🪟 | `brand-windows` | 2 | 2 | `templates/gateway/agent_detail.html`, `templates/gateway/liste.html` |
| 🆔 | `id` | 2 | 2 | `templates/gateway/agent_detail.html` |
| 📟 | `device-mobile-message` | 2 | 2 | `templates/gateway/detail.html` |
| 🟡 | `=status` | 2 | 2 | `templates/incident_major/_einheit_dispatch_options.html`, `templates/incident_major/_kraefteuebersicht.html` |
| ↕ | `arrows-sort` | 2 | 2 | `templates/incident_major/_stab_tafel.html`, `templates/incident_major/board.html` |
| ☑ | `square-check` | 2 | 2 | `templates/incident_major/board.html` |
| ☐ | `square` | 2 | 2 | `templates/incident_major/board.html`, `templates/pdf/katplan.html` |
| 🧭 | `compass` | 2 | 2 | `templates/incident_major/dashboard.html` |
| △ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| ◇ | `=fw` | 2 | 2 | `templates/incident_major/druck_bericht.html`, `templates/incident_major/karte.html` |
| ❌ | `circle-x` | 2 | 2 | `templates/objekt/infoscreen_alarm.html` |
| 🌫 | `mist` | 2 | 2 | `templates/objekt/infoscreen_alarm.html` |
| 🌡 | `temperature` | 2 | 2 | `templates/objekt/infoscreen_alarm.html` |
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
| 📕 | `book` | 1 | 1 | `services/objekt_service.py` |
| 🛗 | `elevator` | 1 | 1 | `services/objekt_service.py` |
| 🚻 | `users-group` | 1 | 1 | `services/objekt_service.py` |
| 🌀 | `=fw` | 1 | 1 | `services/objekt_service.py` |
| ⏩ | `player-track-next` | 1 | 0 | `services/road_closure_notify_service.py` |
| 🧊 | `snowflake` | 1 | 1 | `services/weather_service.py` |
| 🧰 | `box` | 1 | 1 | `static/js/lagefuehrung.js` |
| 🗄 | `archive` | 1 | 1 | `templates/admin/backup.html` |
| 📵 | `device-mobile-off` | 1 | 1 | `templates/admin/device_token_detail.html` |
| 🔋 | `battery` | 1 | 1 | `templates/admin/device_tokens.html` |
| ♻ | `recycle` | 1 | 1 | `templates/admin/org_backup.html` |
| 📧 | `mail` | 1 | 1 | `templates/admin/users.html` |
| ⭳ | `download` | 1 | 1 | `templates/admin/wasserstellen.html` |
| 💡 | `bulb` | 1 | 1 | `templates/admin/wasserstellen.html` |
| ✥ | `pointer` | 1 | 1 | `templates/annotate/editor.html` |
| ◯ | `circle` | 1 | 1 | `templates/annotate/editor.html` |
| ⌫ | `eraser` | 1 | 1 | `templates/annotate/editor.html` |
| ↶ | `arrow-back-up` | 1 | 1 | `templates/annotate/editor.html` |
| ↷ | `arrow-forward-up` | 1 | 1 | `templates/annotate/editor.html` |
| 🧍 | `user-check` | 1 | 1 | `templates/archive/detail.html` |
| ◀ | `chevron-left` | 1 | 1 | `templates/foerderstrecke/maschinist.html` |
| ⏹ | `player-stop` | 1 | 1 | `templates/foerderstrecke/wizard.html` |
| ✔ | `check` | 1 | 1 | `templates/gateway/_druckhistorie.html` |
| ⛔ | `ban` | 1 | 1 | `templates/gateway/_druckregel_simulation.html` |
| 🧩 | `puzzle` | 1 | 1 | `templates/gateway/detail.html` |
| 🩸 | `droplet-half-2` | 1 | 1 | `templates/gateway/detail.html` |
| 🔧 | `tool` | 1 | 1 | `templates/gateway/druckregeln.html` |
| ↵ | `corner-down-left` | 1 | 1 | `templates/incident_major/_cross_marker_panel.html` |
| 🟠 | `=status` | 1 | 1 | `templates/incident_major/_kraefteuebersicht.html` |
| ▣ | `=fw` | 1 | 1 | `templates/incident_major/karte.html` |
| 🛡 | `shield` | 1 | 1 | `templates/incident_major/public_report.html` |
| ✈ | `plane` | 1 | 1 | `templates/incident_major/public_report.html` |
| ➕ | `plus` | 1 | 1 | `templates/incident_major/ressourcen.html` |
| 📑 | `files` | 1 | 1 | `templates/katplan/_anhaenge_anzeige.html` |
| 🏛 | `building-bank` | 1 | 1 | `templates/katplan/_links_anzeige.html` |
| 📚 | `books` | 1 | 1 | `templates/nachschlagewerke/start.html` |
| 🔔 | `bell` | 1 | 1 | `templates/objekt/_benachrichtigung.html` |
| 📂 | `folder-open` | 1 | 1 | `templates/objekt/_einsatz_inhalt.html` |
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
| 💨 | `wind` | 1 | 1 | `templates/weather/_data_section.html` |
| 🌬 | `wind` | 1 | 1 | `templates/weather/index.html` |

\* ohne Kommentare und externe Kanäle.

## Dateien mit den meisten migrierbaren Treffern

| Datei | Treffer |
|---|---:|
| `app/templates/objekt/infoscreen_alarm.html` | 52 |
| `app/templates/incident_major/_lage_layout.html` | 36 |
| `app/templates/foerderstrecke/wizard.html` | 30 |
| `app/templates/incident_major/karte.html` | 28 |
| `app/templates/admin/device_tokens.html` | 20 |
| `app/templates/archive/detail.html` | 20 |
| `app/templates/gateway/detail.html` | 20 |
| `app/services/objekt_service.py` | 18 |
| `app/static/js/lagefuehrung.js` | 18 |
| `app/templates/gateway/druckregeln.html` | 18 |
| `app/templates/einheit/einheit.html` | 17 |
| `app/templates/incident_major/_site_detail.html` | 17 |
| `app/templates/incident_major/dashboard.html` | 17 |
| `app/templates/objekt/_dokumente.html` | 16 |
| `app/templates/gateway/agent_detail.html` | 15 |
| `app/templates/incident_major/_cross_marker_panel.html` | 15 |
| `app/templates/objekt/detail.html` | 13 |
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
| `app/services/katplan_qs_service.py` | 9 |
| `app/templates/admin/wasserstellen.html` | 9 |
| `app/templates/termin/liste.html` | 9 |
| `app/static/js/einsatz_info.js` | 9 |
| `app/templates/admin/gsl_einstellungen.html` | 9 |
| `app/templates/incident_major/_kraefteuebersicht.html` | 9 |
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
| `app/templates/objekt/pflege_review.html` | 7 |
| `app/templates/pdf/incident_report.html` | 7 |
| `app/templates/incident/lagefuehrung_druck.html` | 6 |
| `app/templates/termin/detail.html` | 6 |
| `app/templates/gateway/_druckhistorie.html` | 6 |
| `app/templates/pdf/lagebericht.html` | 6 |
| `app/services/site_pages.py` | 6 |
| `app/templates/pdf/_emoji_font_face.html` | 6 |
| `app/static/js/address-picker.js` | 5 |
| `app/templates/admin/server_log.html` | 5 |
| `app/templates/incident_major/_meldung_card.html` | 5 |
| `app/templates/incident_major/public_verify.html` | 5 |
| `app/templates/katplan/_links_anzeige.html` | 5 |
| `app/templates/pdf/foerderstrecke.html` | 5 |
