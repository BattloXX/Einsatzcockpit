# Infoscreen-Agent installieren

## Repo-Modus (Standard seit S9)

Im Standardmodus `github` kommt der kopierbare Befehl direkt aus dem Pairing-Dialog: `curl -fsSL https://github.com/BattloXX/Einsatzcockpit-Infoscreen-Agent/releases/latest/download/install.sh | sudo bash -s -- --server https://ecp.example --token CODE`. Repo und HTTPS-Server stammen aus der Serverkonfiguration; Name und optionaler Fingerprint werden sicher ergaenzt. Ohne hinterlegten Schluessel entfaellt der Fingerprint und der bisherige Freischaltungsbanner. Die Variante mit `EC_PAIRING_CODE` bleibt. Ausgehend muessen github.com und *.githubusercontent.com ueber 443 erreichbar sein. Fuer abgeschottete Installationen `INFOSCREEN_AGENT_DISTRIBUTION=managed` verwenden; die folgende ECP-Installation gilt dann weiterhin.

## Bisheriger Ablauf und weitere Details

Zurück zum [Überblick](Infoscreen-Agent). Die Einrichtung im ECP beschreibt
[Geräte & Pläne](Administration-Infoscreen-Agent).

## Voraussetzungen

Ubuntu 24.04 oder 26.04, root/sudo, apt/dpkg, Python ab 3.12, curl,
OpenSSL ab 3 mit Ed25519, CA-Zertifikate, sha256sum, korrekte Uhr/NTP,
HTTPS-Zugang zum ECP und Zugang zu den Ubuntu-Paketquellen. Mindestens 512 MiB
müssen sowohl in `/var` als auch `/tmp` frei sein; Chromium/sway benötigen
zusätzlichen Platz. amd64 ist der aktuelle Release-Build; arm64 setzt ein
passendes freigegebenes Paket voraus.

**Paket passend zum Betriebssystem wählen:** Das Paket enthält eine Umgebung
für eine konkrete Python-Minorversion. Ein für Ubuntu 24.04 gebautes Paket ist
kein universelles Paket für 26.04. Der aktuelle ECP-Download unterscheidet nur
Architektur und Kanal, keine Ubuntu-Version. Der Release-Workflow baut derzeit
auf 24.04; Container-Tests auf 26.04 ersetzen kein passendes Release. Vor einer
26.04-Installation muss der Betreiber die passende Paket-/Kanalzuordnung klären.

Für den Kiosk braucht es eine eingerichtete grafische Sitzung und einen
freigegebenen nativen Chromium mit CDP-Pipe und Managed Policies. Die Ubuntu-
Snap-/Transition-Variante ist nicht als funktionierender Kiosk bestätigt.
Das Setup aktiviert Autologin nicht automatisch. Die separate Einrichtung
mit greetd/sway beschreibt die
[Kiosk-Anleitung des Agenten](https://github.com/BattloXX/Einsatzcockpit-Infoscreen-Agent/blob/main/docs/kiosk-session.md).

## Installation aus dem ECP-Dialog

1. Als Org-Admin **Infoscreens > Geräte & Pläne > Gerät hinzufügen** öffnen.
2. Namen vergeben, den frisch erzeugten Einzeiler kopieren und Fingerprint mit
   dem vom Betreiber bestätigten Release-Schlüssel vergleichen.
3. Die Zeile am Ziel-PC ausführen. Beispiel mit Platzhaltern:

```sh
curl -fsSL https://<ecp-host>/infoscreen-agent/install.sh | sudo bash -s -- --server https://<ecp-host> --pairing-code ABCD1234 --key-fingerprint sha256:<hex>
```

Der Dialog bietet alternativ `EC_PAIRING_CODE` statt des Code-Arguments an.
Beide kopierten Zeilen können in der Shell-History stehen; Code und Zeile nicht
in Tickets oder Chats kopieren. Der Code gilt einmalig für 60 Minuten. Die
Downloads verbrauchen ihn noch nicht, erst das Pairing. Bei Ablauf im ECP einen
neuen Code erzeugen. Der Installer-Banner verschwindet mit bestaetigtem aktivem
Schluessel, gueltigem Skript und freigegebenem stable-Release.

Der Installer prüft Werkzeuge, Plattform, Platz und Uhr, lädt über TLS Manifest
und Paket vom ECP, prüft Fingerprint, Signatur, Hash und Paketmetadaten und
installiert erst danach mit apt. Das Paket richtet Dienstkonten und Units ein.
Setup koppelt den Agent, erkennt Displays und startet den Agentdienst; danach
läuft `doctor`. Ohne freigegebene Monitor-Probe bleibt `logical_blank`.
Mehrere Displays benötigen eine bewusste EDID-/Connector-Zuordnung. Kiosk-Sitzung
und physischer Monitor-Rückkanal sind separat einzurichten und zu prüfen.

## Kontrolle und Fehlersuche

```sh
sudo ec-infoscreen-agent doctor
sudo ec-infoscreen-agent status
systemctl status ec-infoscreen-agent.service
journalctl -u ec-infoscreen-agent.service
```

| Symptom | Nächster Schritt |
| --- | --- |
| 401 / Code abgelaufen | Neuen Code erzeugen; Sperre und Kopplung im ECP prüfen |
| 404 beim Download | Bestaetigten Schluessel, Skript, freigegebenes Release, Architektur/Kanal prüfen lassen |
| Fingerprint / Signatur falsch | Mit Betreiber den Schlüssel und das Release abgleichen; Prüfung beibehalten |
| DNS / TLS / Uhrzeit | Hostname, CA-Kette, Zertifikatsablauf, Netz und NTP prüfen |
| apt unterbrochen | `sudo dpkg --configure -a`, Ursache beheben und Installation erneut versuchen |
| Schwarzer Bildschirm / kein Standby | Kiosk-Sitzung, Chromium, Display-Zuordnung und Probe im Sitzungskontext prüfen |
| Doctor gelb | Hinweis bearbeiten; Exitcode 0 kann gelbe, ungeprüfte Hardwarezustände enthalten |

Root über SSH hat häufig keine Compositor-Umgebung. Dort gelbe Sitzungsdiagnose
ist keine Messung am Kiosk. Keine Token oder komplette Zustandsdateien zur Diagnose
weitergeben; auch keine Ausführung mit `bash -x`. Weitere feste Exitcodes stehen im
[Installer-Protokoll](https://github.com/BattloXX/Einsatzcockpit-Infoscreen-Agent/blob/main/docs/installer-protocol.md).

## Reparatur und Deinstallation

Ein erneuter Aufruf kann eine bestehende Installation reparieren; bewusstes
Neukoppeln benötigt einen frischen Code und `--force-repair`. Bei apt-Updates
muss das Paket ebenfalls zur Ubuntu-/Python-Version passen.

Ein zuvor vom ECP geladenes, geprüftes Installerskript lokal verwenden:

```sh
sudo ec-infoscreen-agent unpair
sudo bash ./install.sh --uninstall
# Endgültig einschließlich Daten, Token, Profilen und Konten:
sudo bash ./install.sh --purge
```

`--uninstall` entfernt Paket und Dienste, lässt Daten/Konten für Reparatur stehen.
`--purge` entfernt sie zusätzlich. Reines `apt purge` hat diese vollständige
Bereinigung nicht. Unpair vor Paketentfernung ausführen; ohne erreichbaren Server
den Zugang zusätzlich im ECP sperren. Die endgültige Entfernung löscht lokale
Konfiguration und Browserprofile.


## Releases im ECP bereitstellen (Sysadmin)

1. Im Agent-Repository lokal `scripts/init_release_keys.sh` ausfuehren und den Fingerprint aufbewahren.
2. Tag setzen; die CI veroeffentlicht signierte Pakete und `install.sh` im oeffentlichen GitHub-Release.
3. Unter **Updates & Backups > Agent-Releases** den Repository-Schluessel abrufen,
   Fingerprint gegen Schritt 1 exakt bestaetigen, **Releases von GitHub holen** und den Pilot freigeben.

Kein Env-Edit und kein Neustart erforderlich. Der bisherige CI-Upload bleibt optional.
Ein fremder/veraenderter Repo-Key wird nie automatisch vertraut. Beta-Releases sind
GitHub-Prereleases; fuer den Standarddialog braucht es ein freigegebenes stable-Release.
