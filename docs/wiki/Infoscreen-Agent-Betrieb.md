# Infoscreen-Agent betreiben

## Repo-Modus (Standard seit S9)

Der Agent bezieht Updates im Standard direkt von GitHub und fragt davor `POST /api/v1/infoscreen-agent/update/check` mit Device-Bearer. Neue Versionen starten mit 0 %; Freigabe durch den Sysadmin. Der Geraete-Tab zeigt Quelle und letzten Check-Grund. Ablehnungen priorisieren blocked, locked, pinned, incident, rollout, window; gleiche/aeltere oder unbekannte/unzulaessige Versionen ergeben unknown. Incident bedeutet nach 300 Sekunden erneut pruefen, window nennt die Dauer bis zum naechsten Wartungsfenster in Org-Zeit. Critical umgeht mit Org-Freigabe nur das Fenster. Kanal/Pin/Sperre und **Jetzt pruefen** bleiben erhalten. Der erste Check bindet die bislang nicht gespeicherte Architektur. Automatisch maximal drei neue Versionen pro Geraet/UTC-Tag und 1000 bekannte Versionen insgesamt; groessere Versionsspruenge manuell anlegen. Die folgenden Artefaktablaeufe betreffen `managed`.

## Bisheriger Ablauf und weitere Details

[Überblick](Infoscreen-Agent) · [Formularbedienung Geräte & Pläne](Administration-Infoscreen-Agent)

## Status lesen

Org-Admins finden die Geräte unter **Infoscreens > Geräte & Pläne**.
Online bedeutet: aktives Gerät mit gültiger WebSocket-Presence oder Heartbeat
innerhalb der letzten drei Minuten. Offline kann auch Netzwerkverlust bedeuten.
Ein gesperrtes Gerät gilt als offline. Agent-Version, OS, letzter Heartbeat,
Update-Status und fester Fehlercode helfen, die Ursache einzugrenzen.

Die Resolver-Vorschau zeigt den aktuellen **Sollzustand**. Der letzte Heartbeat
zeigt den gemeldeten Monitorstrom (`on/off/unknown`), Seitenstatus
(`ready/loading/failed/offline/blank/unknown`), Backend und `verified`.
`verified` bestätigt den Rückkanal der Stromsteuerung, keine korrekte Einsatzseite.
Eine alte Rückmeldung ist kein Beweis für den aktuellen Bildschirmzustand.

## Zeitpläne und Übersteuerung

Wochen-Slots gelten in der Plan-Zeitzone. Ausnahmetage ersetzen den Tag vollständig,
auch Lücken bleiben aus. Höhere Priorität gewinnt; bei Gleichstand der spätere
Beginn, zuletzt die höhere Slot-ID. Über-Mitternacht-Slots und Sommerzeit werden
serverseitig auf UTC aufgelöst. Ohne passenden Slot gilt Monitor an mit der
bestehenden Token-Rotation. Der Agent erhält zusätzlich eine 72-Stunden-Plan-Timeline;
offline sind keine neuen Live-Einsatzinformationen verfügbar.

Die erste passende Bedingung gewinnt:

| Rang | Wirkung |
| --- | --- |
| 1 | Gesperrtes Gerät, inaktives Display oder fehlender Alarm-Token: Sperrseite, Strom an |
| 2 | Aktive GSL mit erlaubtem, gültigem GSL-Seitenoverride: Übersteuerung |
| 3 | Aktive GSL |
| 4 | Aktiver regulärer Einsatz |
| 5 | Nachlauf nach dem letzten Einsatz-/GSL-Ende |
| 6 | Gültige manuelle Übersteuerung |
| 7 | Zeitplan-Slot oder Ausnahmetag |
| 8 | Standardanzeige, Strom an |

`org_admin` kann Overrides der eigenen Organisation setzen und aufheben;
Start, Pflicht-Ende (maximal acht Stunden) und Begründung gehören dazu.
GSL-Seiten brauchen aktive GSL und die Erlaubnis am Display; deren Ende verhindert
ein späteres Wiederaufleben desselben Overrides. Manuelle Overrides warten hinter
GSL, Einsatz und Nachlauf. Wetter, aktivierte Statistik und freigegebene HTTPS-URLs
stehen zur Verfügung. Eigenständige Lagekarte-/Einsatzstellen-Seiten werden derzeit
nicht angeboten. Genaue Eingaben und Schnellaktionen stehen in der
[Verwaltungsanleitung](Administration-Infoscreen-Agent).

## Sperren und Neukoppeln

Sperren widerruft die Geräte-Zugangsdaten und trennt die Verbindung. Ein offline
getrennter PC kann den Widerruf erst beim nächsten Kontakt erkennen; lokales
Abschalten bleibt bei unmittelbarem Bedarf notwendig. Entsperren/Token-Rotation
erzeugen einen neuen Pairing-Code, das alte Token lebt nicht wieder auf.
Nach Geräteverlust zusätzlich die zugeordneten Infoscreen-Seitentokens prüfen.

## Updates und Releases

Org-Admins wählen pro Gerät `stable` oder `beta`, optional einen Versions-Pin und
die Updatesperre. **Jetzt prüfen** fordert nur eine erneute Prüfung an.
Das Org-Wartungsfenster ist standardmäßig 02:00–04:30 in der Org-Zeitzone und kann
über Mitternacht gehen. **Kritische Updates erlauben** gestattet bei kritischem
Release die Ausnahme vom Fenster. Einsatz/GSL/Nachlauf, Pin, Updatesperre,
Architektur, Mindestversion und Release-Sperre gelten weiterhin.

Sysadmins verwalten plattformweit unter **Updates & Backups > Agent-Releases**
bestaetigte Release-Schluessel, GitHub-Import, optionale CI-Upload-Tokens,
Release-Freigabe und Rueckzug. Neue Importe und Uploads stehen auf 0 %;
10/50/100 % erweitern die deterministische Testgruppe. Dasselbe Gerät bleibt
für dieselbe Release-Version im gleichen Bucket. Zuerst Pilot, dann Quote erweitern.
Die Erstinstallation nutzt ein freigegebenes Release ohne Canary-Auswahl.
**Release sperren** verhindert weitere Downloads, deinstalliert aber keine schon
installierte Version. Ein Pin auf eine ältere Version erzwingt keinen Downgrade.

`installing`, `ok`, `failed`, `rolled_back` und feste Fehlercodes unterscheiden
Installation und Ergebnis. Bei `rolled_back` Fehler beheben und Pilot erneut
prüfen; Rollback ist ein lokaler Agentvorgang. Signatur-/Hashfehler erfordern
Release-/Schlüsselprüfung. `health_failed` betrifft den Gesundheitscheck,
`rollback_failed` erfordert lokale Wartung. Details liefert
[doctor](Infoscreen-Agent-Installation#kontrolle-und-fehlersuche).

## Lesender MCP-Zugriff

Mit aktiviertem MCP-Modul und `org_admin` (einschließlich vorhandener Admin-Aliase)
liefern `infoscreen_geraete_liste` und `infoscreen_display_status(device_id)`
technische Daten ausschließlich der eigenen Organisation. Der Displaystatus
enthält Resolver-Modus, Sollstrom, `valid_until` und letzten gemeldeten Strom,
Seitenstatus, Backend, `verified` sowie wirksamen Override ja/nein.
Tokens, Codes, URLs, Rotation und Einsatzinhalte werden nicht ausgegeben.
Die Abfrage verändert weder Konfiguration noch Revision und ist keine Live-Probe.


### Optionaler GitHub-Abgleich

`INFOSCREEN_AGENT_GITHUB_SYNC_ENABLED=true` aktiviert den Abgleich alle 24 Stunden
unter dem bestehenden Leader-Lock. DB-/HTTP-Arbeit laeuft im Hintergrundthread;
Worker-Diagnose zeigt Iterationsstatus. Standard ist aus. `INFOSCREEN_AGENT_GITHUB_REPO`
ist ein validiertes owner/name (Default BattloXX/Einsatzcockpit-Infoscreen-Agent).
Ein optionales `INFOSCREEN_AGENT_GITHUB_TOKEN` erhoeht das API-Limit; es ist fuer
oeffentliche Releases nicht notwendig. `rate_limited` meldet HTTP 403/429.

Ergebnis und Datum stehen im Agent-Releases-Tab. Neue Releases bleiben bei 0 %;
der Import weckt keine Geraete. Erst die manuelle Freigabe sendet den Update-Hinweis.
Ein 304-Abgleich verwendet den ETag-Cache und versucht fehlgeschlagene Releases erneut.
