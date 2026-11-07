# Infoscreen-Agent: Sicherheit und Datenschutz

[Überblick](Infoscreen-Agent) · [Installation](Infoscreen-Agent-Installation) · [Betrieb](Infoscreen-Agent-Betrieb)

## Zugangsdaten

Der Pairing-Code ist einmalig und 15 Minuten gültig. Er autorisiert den
Erstdownload; erst Pairing verbraucht ihn atomar. Ein neuer Code ersetzt den
vorigen. Codes nur am vorgesehenen Gerät eingeben und nicht veröffentlichen.
Die Umgebungsvariante des Einzeilers vermeidet ein Installer-Code-Argument,
aber nicht automatisch die Shell-History.

Nach Pairing authentifiziert sich das Gerät mit einem eigenen Bearer-Token.
ECP speichert dessen Hash, der Agent den Klartext im geschützten lokalen Store
`/var/lib/ec-infoscreen/state.json` (0600). Sperren oder Rotation widerrufen das
Token. Die Browser-Seitentokens sind eigene Zugangsdaten; sie stecken in
Infoscreen-URLs und dürfen ebenfalls nicht in Tickets oder Logs gelangen.
CI-Upload-Tokens sind getrennt: einmal sichtbar, serverseitig nur gehasht,
widerrufbar und ausschließlich für Release-Uploads verwendbar.

## Signaturkette

TLS schützt die Übertragung vom ECP. CI signiert Paketmanifest und unveränderte
Installerbytes mit Ed25519; private Schlüssel bleiben in CI. ECP prüft beim
Upload Signaturen, Paketgröße, SHA256, Paketname, Version und Architektur.
Der Installer vergleicht den übergebenen Fingerprint mit seinem eingebetteten
Public Key und prüft Manifest und Paket vor apt. Fingerprint unabhängig mit dem
Betreiber abgleichen. Ein Fingerprint schützt nicht die Ausführung eines bereits
manipulierten Shellskripts; vertrauenswürdige TLS-/Skriptauslieferung bleibt nötig.

Das kanonische Manifest bindet Paketname, Version, Kanal, Architektur, SHA256,
Größe und Mindestversion. `key_id`, Downloadpfad und `critical` sind im aktuellen
Vertrag nicht signiert; TLS und die serverseitige Freigabe schützen diese Policy.
Bei Schlüsselwechsel müssen bestehende Geräte rechtzeitig den neuen Trust-Key
erhalten. Keine Signatur-/TLS-Prüfung zur Fehlerbehebung abschalten.

## Allowlist und Rechte

Nur Org-Admins verwalten Geräte, Pläne, Overrides und Hosts der eigenen Organisation.
Sysadmins verwalten Releases plattformweit. Custom-Seiten verlangen einen exakt
freigegebenen öffentlichen Domainnamen und HTTPS-Port 443; keine Wildcards,
IP-Adressen, Userinfo oder internen Ziele. DNS wird bei Freigabe und Override-Anlage
geprüft. Der Server ruft diese Seiten nicht ab. Redirects und spätere DNS-Änderungen
muss zusätzlich der Kiosk begrenzen. Eine Hostfreigabe vertraut dem dortigen Inhalt;
externe Seiten können selbst Nutzungsdaten erfassen.

## Was der Heartbeat enthält

| Bereich | Gemeldete Daten |
| --- | --- |
| Gerät | Agent-Version, OS-Version, technischer Hostname |
| Stand | Gemeldete Konfigurationsversion, letzte Revision je Display-Key |
| Display | Display-Key, Stromzustand, verified, Backend, Seitenstatus, feste Fehlercodes |

Keine Einsatzdaten, Seiteninhalte, Screenshots, beliebige Freitexte oder URLs im
Heartbeat. Unbekannte Felder werden abgelehnt. ECP hält außerdem letzten
Kontaktzeitpunkt/IP und technische Verbindungs-Presence vor; Hostname und IP
sind technische, potentiell personenbeziehbare Betriebsdaten. Geräte-/Displaynamen
und Override-Begründungen deshalb ohne Einsatzdetails oder Personendaten wählen.

Technische Diagnoseereignisse werden nach 90 Tagen bereinigt, abgelaufene Overrides
nach 30 Tagen. Die tägliche Bereinigung ersetzt nicht die eigene Backup-Retention.
Sicherungen von DB, Release-Ablage und lokalem Store benötigen Zugriffsschutz.
Lesende MCP-Tools geben nur ausdrücklich ausgewählte Statusfelder aus, keine
IP, Hostnamen, Tokens, Codes oder Seiten-URLs.
