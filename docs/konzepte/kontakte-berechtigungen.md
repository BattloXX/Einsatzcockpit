# Kontaktberechtigungen

Das zentrale Verzeichnis ist fuer Einsatzleiter, Bearbeiter (`recorder`), Kontaktverwalter,
Objektverwalter und Administratoren der Organisation lesbar. Die bisherigen Rechte zur
Stammdatenpflege bleiben bei Kontakt-/Objektverwaltern und Administratoren. Die Rolle
Einsatzleiter allein erlaubt keine Aenderung zentraler Stammdaten.

Leser und reine Einheitengeraete erhalten keinen Zugriff auf zentrale Kontaktlisten,
Suche, Details, Profilbilder, Exporte und den vollstaendigen Synchronisierungsfeed.
Weitere ausdruecklich vergebene Fachrollen eroeffnen den entsprechenden Zugriff.
Geraete- und QR-Sessions bleiben auf ihren Einsatzkontext beschraenkt.

In Board und Einsatzinformation stehen zusaetzlich direkt zugeordnete Einsatzkontakte.
Einsatzleiter, Bearbeiter, Kontaktverwalter und Administratoren koennen Kontakte suchen,
zuordnen, entfernen und einen fuer den Einsatz sichtbaren Hinweis pflegen. Zuordnungen
und Entfernungen werden protokolliert.

Objektkontakte werden Lesern nur mit `incident_id` und einer bestaetigten
Objekt-Einsatz-Verknuepfung angezeigt. Pro Objektzuordnung kann die Anzeige mit
„Im zugeordneten Einsatz anzeigen“ entzogen werden. Bestehende Zuordnungen bleiben
bei der Migration freigegeben. Entwuerfe und fremde Organisationen bleiben abgeschirmt.
Bei Geraeten muss das im Session-Token gebundene Fahrzeug aktiv dem Einsatz zugeordnet
sein; fehlende oder entfernte Fahrzeugzuordnungen erlauben keinen Kontaktzugriff.

Die eingeschraenkte Anzeige enthaelt Name, Funktion, Organisation und aktive dienstliche
Telefonnummern sowie einsatzbezogene Hinweise. Interne Notizen, private Telefonnummern,
Adressen, Quellen und weitere Objektzuordnungen werden nicht ausgegeben. Ausserhalb
des Einsatzkontexts enthalten Objektansichten und Objektblaetter fuer Leser keine
Kontaktstammdaten. Pflege- und Aenderungsprotokolle mit Kontaktinformationen sind den
Fachrollen vorbehalten.

## Inbetriebnahme

- Migration `0292` mit `alembic upgrade head` anwenden.
- API-Keys fuer das gesamte Verzeichnis benoetigen ausdruecklich `kontakte:read`.
  Bestehende Keys erhalten diesen Scope nicht automatisch; ein Organisationsadministrator
  kann einen entsprechend freigegebenen Key erstellen.
- Der Service Worker verwirft alte Board- und Objektcaches. Kontowechsel und geaenderte
  Kontaktrechte bereinigen personenbezogene Caches beim naechsten Seitenaufruf.
  Abgewiesene Onlineabrufe entfernen den zuvor gecachten Stand des jeweiligen Pfads.
  Ohne Serververbindung bleibt der zuletzt synchronisierte Berechtigungsstand wirksam.
