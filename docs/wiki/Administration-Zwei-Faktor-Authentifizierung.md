# Zwei-Faktor-Authentifizierung (2FA)

2FA gibt es fuer **Systemadministratoren** (globaler Schalter, nur Authenticator-App)
und fuer **Benutzer der Organisationen** (Richtlinie je Organisation, Authenticator-App
oder SMS). Beides ist nach dem Update **standardmaessig AUS**. Geraete-, QR- und
Alarmlink-Zugaenge sind nie betroffen. Abgefragt wird nur bei der Anmeldung, nie
mitten in einer laufenden Sitzung.

## Organisationen: Richtlinie

Org-Admins stellen unter **Verwaltung > Benutzer & Rechte > Anmeldung & 2FA**
(`/admin/sicherheit`) ein:

| Richtlinie | Wirkung |
|---|---|
| Aus | Keine 2FA-Abfrage (Standard). |
| Optional | Benutzer koennen 2FA im Profil einrichten; wer sie eingerichtet hat, wird bei der Anmeldung gefragt, wenn eine Pflicht gilt. |
| Pflicht fuer Admin-Rollen | Org-Admins muessen 2FA verwenden. |
| Pflicht fuer alle | Alle persoenlichen Benutzer muessen 2FA verwenden. |

- **SSO-Benutzer ausgenommen** (Standard an): Microsoft-SSO-Konten bringen eigene MFA mit.
- **SMS als zweiter Faktor erlaubt** (Standard an): schaltet SMS fuer die Organisation ab.
- Die Seite zeigt, wie viele aktive Benutzer nach der gewaehlten Richtlinie noch keinen
  Faktor haben. Diese richten ihn beim naechsten Seitenaufruf ein.
- Vor dem Einschalten einer Pflicht die Anmeldung in der Android-App und am Tablet testen.

Wer unter die Pflicht faellt, wird auf `/mfa/verify` geleitet; ohne Faktor auf die
Einrichtung. Auch der Org-MCP-Login verlangt dann einen Code.

## Benutzer: Faktoren im Profil

Im **Profil > Sicherheit** stehen Status, Einrichtung, neue Recovery-Codes (mit
gueltigem App-Code), SMS-Nummer aendern und 2FA entfernen (nur ohne Pflicht).

- **Authenticator-App (empfohlen):** QR-Code mit Microsoft Authenticator, Google
  Authenticator oder 2FAS scannen, Code bestaetigen.
- **SMS:** Code an eine eigene 2FA-Nummer. Diese Nummer ist unabhaengig von der
  Telefonnummer im Profil; Profil-, Admin- und MCP-Aenderungen der Telefonnummer
  aendern sie nicht. Eine neue Nummer wird per Code an genau diese Nummer bestaetigt,
  ein Wechsel verlangt eine hoechstens fuenf Minuten alte 2FA-Bestaetigung und
  loest eine Mail aus.
- **Recovery-Codes:** zehn Einmalcodes, nur einmal angezeigt, getrennt aufbewahren.

### Regeln fuer SMS

- SMS zaehlt **nur nach Anmeldung mit Passwort oder SSO**. Nach einer Anmeldung per
  SMS-PIN oder nach einem Kontowechsel ist SMS als zweiter Faktor gesperrt, sonst waere
  es zweimal derselbe Faktor. Dann App-Code oder Recovery-Code verwenden.
- Codes: 6 Stellen, 5 Minuten gueltig, einmal verwendbar. Nach 5 Fehlversuchen
  5 Minuten Sperre.
- Hoechstens 3 SMS in 10 Minuten und 10 SMS pro Tag je Benutzer.
- Fuer Systemadministratoren nicht verfuegbar.
- SMS-Codes sind schwaecher als die App (SIM-Tausch, Mitlesen auf dem Handy). Wo
  moeglich die Authenticator-App empfehlen.

## 2FA zuruecksetzen durch Admins

In **Verwaltung > Benutzer & Rechte > Benutzer** zeigt die Spalte **2FA** Aus, TOTP,
SMS, TOTP + SMS, "Gesperrt bis" oder "Neueinrichtung offen".

| Wer | Darf zuruecksetzen |
|---|---|
| Org-Admin | Benutzer und andere Org-Admins der eigenen Organisation; nie Systemadmins, nie sich selbst |
| Systemadmin | Alle Benutzer aller Organisationen, auch andere Systemadmins |

- **2FA zuruecksetzen:** Grund angeben (Pflicht). Hat der Admin selbst 2FA, muss seine
  Bestaetigung hoechstens fuenf Minuten alt sein. Alle Faktoren und Recovery-Codes des
  Benutzers werden geloescht, er wird ueberall abgemeldet und bekommt eine Mail
  (und eine Hinweis-SMS an die alte 2FA-Nummer). Audit-Eintrag `admin.mfa.reset`
  mit Grund.
- **24-Stunden-Frist:** Gilt fuer den Benutzer eine Pflicht, muss er innerhalb von
  24 Stunden neu einrichten. Danach ist die Einrichtung gesperrt, bis ein Admin
  erneut zuruecksetzt.
- **Sperre aufheben:** hebt nur die Fehlversuchssperre auf, Faktoren bleiben.
- Vor dem Zuruecksetzen die Identitaet des Benutzers pruefen (persoenlich oder per
  Rueckruf), da danach jeder mit seinem Passwort einen neuen Faktor einrichten kann.
- Per MCP ist der 2FA-Status lesbar (`benutzer_lesen`, `benutzer_suchen`), ein Reset
  ist per MCP bewusst nicht moeglich.

# Systemadministratoren

## Einrichtung

1. Als Systemadministrator unter `/admin/system-einstellungen` den Abschnitt
   "Zwei-Faktor-Authentifizierung" oeffnen und "Eigene 2FA einrichten" waehlen.
2. QR-Code unter `/mfa/einrichten` mit Microsoft Authenticator, Google Authenticator
   oder 2FAS scannen. Alternativ den manuellen Schluessel uebernehmen.
3. Einen aktuellen sechsstelligen Code bestaetigen.
4. Die zehn einmalig angezeigten Recovery-Codes sicher und getrennt vom Authenticator
   aufbewahren. Spaeter koennen diese Codes nicht erneut angezeigt werden.

Die Einrichtung allein aktiviert die globale Pflicht noch nicht. TOTP-Secrets liegen
verschluesselt in der Datenbank; Recovery-Codes ausschliesslich als Hash.
Fuer eine Wiederherstellung werden neben der Datenbank auch der bisherige
`FERNET_KEY` bzw. beim bestehenden Fallback der `SECRET_KEY` benoetigt.
Die Serverzeit muss stimmen (30-Sekunden-Schritte, Toleranz ein Schritt davor/danach).
Ein bereits verbrauchter oder aelterer Schritt wird abgelehnt.

## Aktivierung und Anmeldung

Erst wenn **alle aktiven Systemadministratoren** die Einrichtung bestaetigt haben,
kann der globale Schalter aktiviert werden. Die Liste nennt fehlende Einrichtungen.
Danach verlangen auch vorhandene Sessions beim naechsten geschuetzten Zugriff eine
Pruefung unter `/mfa/verify`. Das gilt fuer Passwort, SSO, SMS-PIN und Kontowechsel.
Nach fuenf falschen Codes ist die Pruefung fuer fuenf Minuten gesperrt. Die Sperre
ist pro Benutzer in der Datenbank gespeichert und gilt auch nach einem Serverneustart.

Normale und gemerkte Sessions behalten die MFA-Bestaetigung beim Aktivitaets-Refresh.
Beim Kontowechsel ist erneut MFA erforderlich. Ein Code kann nicht zweimal verwendet
werden: fuer eine erneute Pruefung den naechsten TOTP-Schritt abwarten oder einen
Recovery-Code verwenden.

Zum Deaktivieren muss die eigene MFA-Pruefung hoechstens fuenf Minuten alt sein.
"2FA frisch bestaetigen" bietet die erneute Pruefung an. Deaktivieren widerruft alle
Admin-Sessions und gespeicherten Admin-Konten im Kontowechsler.

## Recovery und Zuruecksetzen

Auf `/mfa/verify` kann anstelle des TOTP-Codes ein unbenutzter Recovery-Code eingegeben
werden. Jeder Recovery-Code ist nur einmal verwendbar.

Die eigene Einrichtung kann unter `/mfa/einrichten` mit einem gueltigen TOTP- oder
Recovery-Code zurueckgesetzt werden. Ein anderer Systemadministrator kann den Reset
in den System-Einstellungen vornehmen; bei aktiver globaler 2FA braucht er dazu eine
hoechstens fuenf Minuten alte MFA-Pruefung. Der Betroffene wird abgemeldet und muss
sich neu anmelden und 2FA neu einrichten. Ein Reset deaktiviert die globale Pflicht
nicht. Bei konfiguriertem Mailversand und vorhandener Adresse erhaelt der Betroffene
einen Hinweis; Audit-Eintraege entstehen auch ohne Mailkonfiguration.

## Totalverlust: lokale Server-CLI

Ohne verfuegbare Recovery-Codes erfolgt der Notfallreset nur per CLI am Server.
Die regulaere, frisch MFA-bestaetigte Verwaltung durch einen anderen
Systemadministrator bleibt wie oben beschrieben verfuegbar.
Nur mit Zugang zur Server-Shell, im Anwendungsverzeichnis bzw. Container:

```bash
.venv/bin/python -m app.cli mfa-reset <username>
.venv/bin/python -m app.cli mfa-global-aus
```

Mit Docker Compose heisst der Appservice in diesem Repo `app`:

```bash
docker compose exec app python -m app.cli mfa-reset <username>
docker compose exec app python -m app.cli mfa-global-aus
```

`mfa-reset` entfernt Faktoren und Recovery-Codes dieses Systemadministrators und
widerruft dessen Sessions. Nach neuer Anmeldung kann er sich neu einrichten.
`mfa-global-aus` deaktiviert die Pflicht und widerruft alle Admin-Sessions.
Beide Aktionen schreiben Audit-Eintraege mit dem Hinweis auf die lokale Ausfuehrung.
Es gibt dafuer keinen HTTP-Endpunkt und keinen Mail-Bypass.

## MCP und Systemdiagnose

Der separate [System-Admin-MCP](Administration-System-Admin-MCP) besitzt einen
eigenen OAuth-Login mit zweitem Faktor und expliziten Freigaben. Bei aktiver 2FA wird der bestehende
Org-MCP-Login fuer Systemadministratoren verweigert: Dieser OAuth-Flow hat noch
keinen zweiten Faktor. Fuer Organisationsbenutzer mit 2FA-Pflicht verlangt der Org-MCP-Login einen Code (siehe oben).
Bereits ausgestellte Org-MCP-Bearer-Tokens werden durch den Schalter nicht widerrufen;
ihre bestehenden Org-Berechtigungen und Laufzeiten gelten weiter.


Die [Systemdiagnose-Webseite](Administration-Systemdiagnose) verwendet das zentrale
MFA-Gate und erlaubt ausschliesslich persoenliche Systemadministratoren. Fuer
Reparaturen muessen 2FA und Reparaturschalter aktiv sein. Eine kontrollierte
Ausfuehrung erfordert eine hoechstens fuenf Minuten alte MFA; HIGH verlangt
zusaetzlich einen neu geprueften Faktor auf der vorhandenen Freigabeseite. Die
Freigabe ist einmalig, zehn Minuten gueltig und an die konkrete Simulation gebunden.
Ohne aktive 2FA sind Reparaturen auch bei gesetztem Reparaturschalter gesperrt.

Die empfohlene Inbetriebnahme und Notfallverfahren stehen im
[System-Admin-Ueberblick](Administration-System-Admin-Ueberblick).
