# Zwei-Faktor-Authentifizierung fuer Systemadministratoren

Die optionale TOTP-2FA ist nach dem Update **standardmaessig AUS**. Ein fehlender
Systemschluessel `admin_2fa_enabled` oder jeder andere Wert als exakt `true`
bedeutet AUS. Organisationsbenutzer sowie Geraete- und QR-Zugaenge bleiben unberuehrt.

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

Nur mit Zugang zur Server-Shell, im Anwendungsverzeichnis bzw. Container:

```bash
.venv/bin/python -m app.cli mfa-reset <username>
.venv/bin/python -m app.cli mfa-global-aus
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
keinen zweiten Faktor. Normale Organisationsbenutzer bleiben unveraendert.
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
