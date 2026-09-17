# Externe Objektpflege (Administration)

Einrichtung, Berechtigungen und Sicherheitsrahmen für die externe Datenpflege von
Objekten. Anwender-Doku: [Externe Objektpflege](Anwender-Objektpflege).

## Berechtigungen

Die externe Pflege verwendet das bestehende Berechtigungsmodell der Objektverwaltung;
eine neue Rolle gibt es nicht. Einladungen, Verlängerungen, Widerrufe und die interne
Prüfung sind für `objekt_verwalter` vorgesehen. Administrative Rollen (`admin`,
`org_admin`, `system_admin`) haben die Rechte entsprechend dem bestehenden
Objektverwaltungsmodell ebenfalls. Das Objektmodul muss wie gewohnt systemweit und für die
jeweilige Organisation aktiviert sein.

## Sicherheit der Einladungslinks

Jeder Link enthält einen zufälligen Token aus `secrets.token_urlsafe(32)`. In der
Datenbank wird ausschließlich sein SHA-256-Hash gespeichert; der Klartext ist nur beim
Erzeugen beziehungsweise beim direkten Versand verfügbar. Das Portal verwendet kein
Session-Cookie und prüft Token, Status und Gültigkeitszeit bei jeder Anfrage erneut.
Ein Link ist standardmäßig 60 Tage gültig. Beim erneuten Senden und bei einer angeforderten
Nacharbeit wird der Token rotiert, wodurch der vorherige Link sofort ungültig wird.

## Erinnerungen

Für offene Pflegeaufträge werden Erinnerungen nach 30 Tagen sowie sieben Tage vor Ablauf
vorgesehen. Terminale oder anderweitig geschlossene Aufträge erhalten keine Erinnerungen.
Prüfe bei abgelaufenen Aufträgen, ob ein neuer Auftrag mit aktuellem Umfang erforderlich
ist, statt einen alten Link weiterzugeben.

## Extern sichtbare Daten und Grenzen

Das Portal zeigt nur die im Pflegeauftrag gewählten, explizit freigegebenen Bereiche.
Bearbeitbar sind Stammdaten, Adresse, Zufahrt, zentrale Kontaktdaten und Dokumente.
BMA- und Gefahrenangaben können extern bestätigt, aber nicht verschachtelt bearbeitet
werden. Diese Einschränkung ist eine bewusste Umfangsentscheidung: Die Bearbeitung der
verschachtelten BMA- und Gefahrenstrukturen wird derzeit nicht unterstützt.

Externe Änderungen bleiben bis zur Freigabe als Arbeitsstand beziehungsweise Vorschlag
getrennt. Administratoren sollten daher weder den Einladungslink noch Screenshots aus dem
Portal unnötig weitergeben und die interne Prüfung immer abschließen.

---

**Verwandt:** [Administration: Objektverwaltung](Administration-Objektverwaltung) · [Anwender: Externe Objektpflege](Anwender-Objektpflege) · [Kontaktverwaltung](Administration-Kontaktverwaltung)
