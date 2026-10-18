# Benutzer-Mails: Inhalt, Optik und Ablauf

Stand: 2026-10-18

## Bestand

| Mail | Funktion | Auslöser | Probleme |
|---|---|---|---|
| Willkommen (lokal) | `mail_service.send_welcome_mail` | `/admin/benutzer/neu` (Haken), MCP `benutzer_anlegen` | Passwort im Klartext in der Mail; nur „bitte ändern", kein erzwungener Wechsel; kein Org-Branding; kein App-Hinweis; Mailfehler nur im Log |
| Willkommen (Microsoft-SSO) | `send_sso_welcome_mail` | `/admin/benutzer/neu` (Haken) | Umlaute als ue/ss im Text; kein App-Hinweis; kein Branding |
| Passwort zurücksetzen | `send_password_reset` | `/passwort-vergessen`, Admin „Reset-Mail", Org-Admin-Anlage in `ui_settings.py:1750` | Name wird im HTML **nicht escaped**; 30 min TTL auch für die Erst-Einrichtung eines Org-Admins; SSO-Benutzer bekommen einen nutzlosen Reset-Link; keine Bestätigung nach erfolgter Änderung |

Alle Mails sind Inline-f-Strings in `app/services/mail_service.py`, jede mit eigenem Mini-Layout, Absender immer „Einsatzcockpit" statt der Organisation. Eine gute Vorlage für ein mail-client-taugliches Layout mit Org-Logo/-Farbe gibt es bereits: `app/templates/email/einsatzinfo_mail.html` + `einsatzinfo_mail_service.py` (`_absolute_url`, `brand_color`, `sample_mail_view`).

Apps: Android-App (Capacitor, öffentliches Repo `BattloXX/Einsatzcockpit-Android`, Release-Assets `einsatzcockpit-vX.apk` und `einsatzcockpit-autoupdate-vX.apk`, kein Play Store). iPhone nutzt die PWA (Safari → Teilen → „Zum Home-Bildschirm"), Anleitung bisher nur in `hilfe.html`.

## Ziele

1. Ein gemeinsames, gebrandetes Layout (Org-Name, Logo, Farbe, Testsystem-Banner, Fußzeile) für alle Benutzer-Mails, HTML + Text, durchgehend escaped.
2. Willkommens-Mails mit Abschnitt „App installieren": Android-Download-Link, iPhone-Anleitung für die PWA.
3. Kein Klartext-Passwort mehr per Mail: Einladungslink „Passwort festlegen" mit längerer Gültigkeit.
4. Sauberer Reset-Ablauf: SSO-Hinweis statt Reset-Link, Bestätigungs-Mail nach Passwortänderung.
5. Admins sehen eine Vorschau aller Mails und können eine Einladung erneut senden.

## Entscheidungen

- E1 Templates als Jinja-Dateien unter `app/templates/email/benutzer/` (`_layout.html`, je Mail `.html` + `.txt`), gerendert über einen neuen `app/services/benutzer_mail_service.py`. `mail_service.py` behält nur Transport (`deliver`, `_build_message`).
- E2 Absenderanzeige/Gruß: Org-Name (Fallback „Einsatzcockpit"). Betreff mit Org-Name, z. B. „Willkommen bei Einsatzcockpit – FF Wolfurt".
- E3 Android-Link: neue öffentliche Route `/app/android` leitet auf das `einsatzcockpit-autoupdate-v*.apk` (User-Entscheidung 2026-10-18) des neuesten Releases im Android-Repo um (1 h gecacht, Fallback Release-Seite). Konfigurierbar über `ANDROID_APP_REPO` und `ANDROID_APK_PRAEFIX` (Standard `einsatzcockpit-autoupdate-v`; für die Variante ohne Selbst-Update `einsatzcockpit-v`). Neue öffentliche Seite `/app` mit Anleitung Android / iPhone / Desktop; die Mail verlinkt beide.
- E4 Einladung: `PasswordResetToken.zweck` (`reset` | `einladung`), Einladungen gelten `INVITE_TTL_HOURS` (Standard 168 h). Die Willkommens-Mail enthält Benutzername + Button „Passwort festlegen". Ein im Formular manuell vergebenes Passwort wird nie gemailt (bleibt einmalig in der UI sichtbar wie bisher).
- E5 Nach jeder Passwortänderung (Reset, Einladung, Profil) geht eine Bestätigungs-Mail „Dein Passwort wurde geändert" raus (Zeit in Org-Zeitzone, Hinweis „warst du das nicht? → Administrator").
- E6 Vorschlag, nicht beauftragt: alle anderen Sitzungen nach Passwortänderung beenden (Session-Epoche im Cookie). Betrifft die Auth-Middleware, eigener PR nach Freigabe.

## Pakete

| Paket | Inhalt |
|---|---|
| M1 | Layout + `benutzer_mail_service` (Branding, Escaping, Text-Variante), die drei bestehenden Mails darauf umgestellt und textlich überarbeitet (Du-Form, Umlaute, klare Handlungsaufforderung, Fußzeile). Verhalten der Auslöser unverändert. |
| M2 | `/app` (Installationsseite) + `/app/android` (APK-Weiterleitung), Abschnitt „App installieren" in beiden Willkommens-Mails, Link in `hilfe.html`. |
| M3 | Einladung statt Passwort: Migration 0291 `password_reset_token.zweck`, Einladungs-Mail, Reset-Seite mit Einladungs-Wortlaut, Org-Admin-Anlage und MCP `benutzer_anlegen` nutzen die Einladung, Admin-Liste „Einladung offen" + „Einladung erneut senden", Rückmeldung bei Mailfehler. |
| M4 | Reset-Ablauf: SSO-Benutzer bekommen bei „Passwort vergessen" einen Microsoft-Login-Hinweis, Bestätigungs-Mail nach Passwortänderung (Reset, Einladung, Profil), Admin-Reset-Mail mit Hinweis „von deinem Administrator ausgelöst". |
| M5 | Admin-Vorschau `/admin/benutzer/mail-vorschau` (alle Typen mit Beispieldaten der eigenen Org, Testversand an sich selbst), Wiki/CHANGELOG. |

## Umsetzungsstatus

- M1–M5 umgesetzt. Die aktive Mail-Auswahl umfasst Einladung, Microsoft-Willkommen,
  Passwort-Reset, SSO-Hinweis und Passwort-Bestätigung. Der alte lokale
  Klartext-Willkommensversand wurde entfernt.
- Migration 0291 ergänzt den Zweck von Passwort-Tokens für Einladungen.
- `/app/android` liefert die Auto-Update-APK; `ANDROID_APP_REPO`,
  `ANDROID_APK_PRAEFIX` und `INVITE_TTL_HOURS` sind konfigurierbar.

## Offene Tests nach Deploy

- Willkommens-Mail in Outlook, Gmail und iOS Mail ansehen (Logo, Buttons, Dark Mode).
- Android-Link auf einem Android-Gerät öffnen, APK installieren.
- iPhone: Link `/app` in Safari öffnen, PWA nach Anleitung installieren.
- Einladung annehmen, Bestätigungs-Mail prüfen; „Passwort vergessen" mit SSO-Konto.
