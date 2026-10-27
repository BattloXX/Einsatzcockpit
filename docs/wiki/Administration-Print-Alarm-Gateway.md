# Print & Alarm Gateway (Administration)

Einrichtung und Betrieb des ECPG-Moduls im Web-UI: Aktivierung, Gateways koppeln,
Drucker verwalten, Druckregeln, manueller Druck. Die Installation des lokalen
Containers ist unter [Print & Alarm Gateway einrichten](Installation-Print-Alarm-Gateway)
beschrieben.

Das **Gateway** ist ein kleiner Docker-Container im Feuerwehrhaus (eigenes Repo
[`einsatzcockpit-gateway`](https://github.com/BattloXX/Einsatzcockpit-gateway)), der
**ausgehend** (WSS) mit der Cloud verbunden ist und zwei Aufgaben übernimmt:

- **Serieller Alarm:** liest den Leitstellen-Alarmdruck über einen W&T Com-Server
  (TCP) und legt automatisch einen Einsatz an (mit Dedup gegen LIS/API).
- **Netzwerkdruck:** druckt Einsatzinfo, Objektunterlagen, GSL-Lageblatt und
  Alarm-Rohtext auf lokale Netzwerkdrucker (CUPS/IPP-Everywhere) — automatisch per
  Druckregel oder manuell per Klick.

## Modul aktivieren (zweistufig, wie UAS/Objekt)

Beide Schalter müssen an sein:

1. **Systemweit** (nur `system_admin`): `/admin/settings` → Abschnitt **„Systemweite
   Module"** → **🖨️ Print & Alarm Gateway** → „Systemweit aktivieren". Setzt den
   SystemSettings-Key `gateway_module_enabled`.
2. **Je Organisation** (Org-Admin): `/admin/settings` → Org-Abschnitt → **🖨️ Print &
   Alarm Gateway** → „für diese Organisation aktivieren". Solange das System-Flag aus
   ist, ist die Org-Checkbox ausgegraut.

Ausschalten **versteckt nur die Ansichten** (`/gateway`-Routen liefern 404, der
Navigationseintrag verschwindet) — es werden keine Daten gelöscht. Jeder Toggle wird
im Audit-Log protokolliert (`gateway.system_toggle` / `gateway.org_toggle`).

## Rollen

| Rolle | Rechte |
|-------|--------|
| `org_admin` | Gateways, Drucker und Druckregeln verwalten; koppeln; Token rotieren/widerrufen |
| Alle angemeldeten Org-Benutzer (`recorder`+) | Manueller Druck aus Einsatz/GSL/Objekt |

Der Menüpunkt **„Gateway / Druck"** erscheint nur für Org-/System-Admins bei aktivem
Modul.

## Gateway anlegen und koppeln

Unter **Gateway / Druck** (`/gateway`):

1. **+ Gateway anlegen** — Name (z. B. `Gerätehaus`) und optional Standort.
2. Im Gateway-Detail **Pairing-Code erzeugen** — ein 8-stelliger Einmal-Code (10 min
   gültig) wird **einmalig** angezeigt.
3. Den Code am Container hinterlegen (`ECPG_PAIRING_CODE` im `docker-compose.yml`
   beim Erststart) **oder** auf der lokalen Statusseite des Gateways eingeben
   (`http://<gateway-ip>:8631/`). Details:
   [Installation](Installation-Print-Alarm-Gateway).
4. Sobald der Container gekoppelt ist, wechselt der Status auf **Online** und der
   Container zieht seine Konfiguration (Drucker, W&T, Parser) automatisch.

**Token verwalten:** im Gateway-Detail lässt sich das Device-Token **rotieren**
(neues Token, altes ungültig) oder **widerrufen** (Gateway muss neu gekoppelt
werden). Es wird nur der Hash gespeichert; der Klartext erscheint nur einmal.

## Alarmleitung (W&T Com-Server)

Im Gateway-Detail unter **Alarmleitung**:

| Feld | Bedeutung |
|------|-----------|
| Host / IP, Port | Adresse des W&T Com-Servers im LAN (Standard-Datenport 8000) |
| Zeichensatz | CP850 / Latin-1 / UTF-8 (Leitstellen-Drucker meist CP850) |
| Datagramm-Ende | Idle-Timeout (Default) oder Form Feed `0x0C` |
| Idle-Timeout (ms) | Wie lange ohne Daten, bis ein Alarm als vollständig gilt |
| Notfalldrucker | Ziel für den Offline-Notdruck, falls die Cloud nicht erreichbar ist |

> Zeichensatz und Datagramm-Strategie sollten mit einem echten Alarm-Mitschnitt
> verifiziert werden. Der Container legt jedes empfangene Datagramm roh in einen
> Ringpuffer (letzte 500), sichtbar auf der Statusseite — ideal zur Parser-Feinjustage.

## Drucker

Im Abschnitt **Drucker**:

- **Netzwerk durchsuchen** — stößt eine mDNS/SNMP-Discovery am Gateway an; gefundene
  Drucker erscheinen als **Vorschläge** (inaktiv). Mit **Übernehmen** aktivieren.
- **Per IP hinzufügen** — Name + IP-Adresse; das Gateway richtet die CUPS-Queue als
  `ipp://<ip>/ipp/print` (driverless, IPP Everywhere) ein.
- **Testseite** druckt eine kurze Prüfseite.
- **Löschen** / **Deaktivieren** entfernt die Queue am Gateway.

Kein Drucker wird ungefragt eingerichtet — die Aktivierung erfolgt immer hier im
Web-UI.

## Druckregeln (Automatikdruck)

Druckregeln werden im eigenen Tab unter `/gateway/<id>/druckregeln` verwaltet. Pro Regel ist wählbar:

| Feld | Beschreibung |
|------|--------------|
| Auslöser | `einsatz_created`, `einsatz_updated`, `gsl_created`, `gsl_lage_updated`, `alarm_serial_received`, `verleih_created` |
| Filter | z. B. Mindest-Alarmstufe, Stichwort, Einsatzart (Echt/Übung), Zeitfenster, nur BMA |
| Dokumente | Einsatzinfo, GSL-Lageblatt, GSL-Gesamtbericht, Objektblatt, Alarm-Rohtext, Verleihschein |
| Objekt-Elemente | Feuerwehrplan, BMA-Laufkarten, Hydrantenplan … (wenn dem Einsatz ein Objekt zugeordnet ist) |
| Ziel | ein oder mehrere Drucker (der Fallback-Drucker ist derzeit nur speicherbar und wird beim Druck noch nicht ausgewertet) |
| Optionen | Kopien, Duplex, Farbe |

Beispiel: „Einsatzinfo bei Alarm" → Auslöser `einsatz_created`, Dokument
`Einsatzinfo`, Ziel `Florianstation`. Bei jeder Einsatzanlage (API, LIS, seriell,
manuell) wird die Einsatzinfo automatisch gedruckt.

Verleihscheine werden automatisch gedruckt, indem eine Regel mit Auslöser
**Verleihschein angelegt** und Dokument **Verleihschein** eingerichtet wird. Bestehende
Einstellungen des früheren Verleih-Autodruck-Schalters werden bei der Aktualisierung in
eine solche Regel migriert.

**Doppeldruck ausgeschlossen:** pro (Einsatz, Regel, Dokument, Drucker) wird maximal
einmal automatisch gedruckt — auch wenn ein Alarm parallel über LIS **und** die
serielle Leitung eintrifft.

## Manueller Druck

Auf dem Einsatz-Board öffnet **⋯ → 🖨️ Auf Drucker drucken** einen Dialog: Drucker
wählen (zuletzt verwendeter vorbelegt), Kopien/Duplex, **Drucken**. Rückmeldung als
Toast („Gesendet an Florianstation"). Ist gerade kein Gateway online, wird der Job
gespoolt und nachgeholt.

## Status & Ausfallsicherheit

- **Online/Offline** je Gateway und **Alarmleitung verbunden/getrennt** sind live im
  Web-UI sichtbar (WebSocket).
- Der Container spoolt Druckjobs lokal (SQLite) und überlebt Neustarts; offline
  Drucker werden mit Backoff erneut versucht, danach greift der Fallback-Drucker.
- Ist die Cloud nicht erreichbar und ein Alarm trifft ein, druckt das Gateway den
  **Rohtext lokal als Notdruck** und meldet den Alarm bei Reconnect nach.

---

**Verwandt:** [Print & Alarm Gateway einrichten (Installation)](Installation-Print-Alarm-Gateway) ·
[LIS/IPR-Anbindung](Administration-LIS-Anbindung) ·
[Objektverwaltung](Administration-Objektverwaltung)


## Alarmempfang und Diagnose

Der Gateway-Tab **Alarmempfang** ist für `org_admin` und `admin` sichtbar. Er zeigt
Gateway- und Leitungsverbindung, den letzten gespeicherten Empfang und die heutigen
Zähler in der Zeitzone der Organisation. Status, Zeitraum (heute, 7 oder 30 Tage) und
Teile der Leitstellennummer filtern die letzten maximal 200 Einträge. Die Ansicht
aktualisiert sich alle 15 Sekunden und nach Aktionen ohne Neuladen.

Ein Empfang öffnet die Diagnose mit Originaltext, Hex-Auszug (maximal 4 KiB),
Parserfeldern, Hinweisen, Resttext, Qualität, Parserversion, Gateway-Parservergleich,
Versionen derselben Nummer, Einsatz und Fehlertext. Versuchsanzahl und Zeitpunkt des
letzten Versuchs sind verfügbar; eine vollständige Historie einzelner Versuche wird
im aktuellen Datenmodell nicht gespeichert. Rohbytes werden 12 Monate aufbewahrt;
danach bleibt der Text erhalten.

**Neu verarbeiten** verwendet den aktuell aktiven Cloud-Regelsatz. Ohne Einsatz läuft
die normale, über die Leitstellennummer idempotente Einsatzzuordnung. Bei einem
verknüpften Einsatz werden ausschließlich Felder mit Herkunft `alarm` aktualisiert;
manuelle Korrekturen bleiben geschützt. Die vorhandene Version bleibt erhalten,
es gibt keine Nachalarmierung und keinen Nachlauf-Druck. Die Aktion wird auditiert
und im Einsatzjournal vermerkt.

**Als geprüft markieren** ist nur bei `needs_review` oder `error` möglich. Der
Fehlertext wird geleert. Nur mit verknüpftem Einsatz wird der Status `applied`
gesetzt; ohne Einsatz bleibt der Status erhalten und die Prüfung steht im Audit.
Es wird kein zusätzlicher Status `reviewed` eingeführt.

Bei mindestens fünf fehlerhaften oder zu prüfenden Empfängen innerhalb von 24 Stunden
zeigt die Dienstüberwachung einen Hinweis an. Dieser Hinweis versendet keine Alarme.

Das Gateway kann abgeschnittene Übertragungen als `incomplete` melden. Die Cloud
speichert derzeit keinen eigenen `incomplete`-Status; für die Diagnose Originaltext,
Parserhinweise und die lokale Gateway-Statusseite prüfen. Der Instanz-Lock im
Gateway verhindert, dass mehrere Container dieselbe W&T-Verbindung gleichzeitig
lesen. Ein Lock-Konflikt muss am Gateway behoben werden; Cloud-Wiederverarbeitung
ersetzt keine fehlenden Bytes.

## Parser-Regeln pflegen

Im Tab **Alarmempfang** befindet sich der Unterbereich **Parser** fuer Org-Admins
und Admins. Die Versionen gelten fuer die gesamte eigene Organisation. Version 0
ist der eingebaute Standard; Versionen zeigen Quelle (UI/MCP), Ersteller, Notiz und
Zeitpunkte. Ein neuer Entwurf kopiert den aktiven Satz oder den Standard.

Den Entwurf oeffnen, Regeln als JSON und eine Notiz bearbeiten und speichern.
Schema und Regex-Muster werden dabei validiert; Fehlermeldungen erscheinen direkt
im Unterbereich. Der strukturierte Diff zeigt hinzugefuegte, geaenderte und
entfernte Felder gegenueber dem aktiven Satz. Aktive und archivierte Versionen
sind unveraenderlich. **Entwurf verwerfen** archiviert den Entwurf.

Testfaelle koennen manuell oder aus einer Alarmempfang-ID des angezeigten Gateways
angelegt werden. Unter **Erwartete Felder** ein JSON-Objekt mit den zu pruefenden
Feldern des Parserergebnisses angeben, beispielsweise `{"stichwort": "f2"}`.
Nur diese Felder werden verglichen. Testfaelle lassen sich deaktivieren oder
loeschen; ihre Quelle bleibt sichtbar. Beim Speichern ersetzt die Anonymisierung
Telefonnummern, E-Mail-Adressen, IBAN und erkannte Namen nach Notrufer/Melder/Anrufer
durch Platzhalter. Auch Titel und erwartete Textwerte werden maskiert. Weitere
Personendaten in Freitext bitte vor dem Speichern selbst entfernen.

**Probelauf** prueft die gespeicherte Version gegen die letzten maximal 200
Rohdatensaetze der Organisation sowie alle aktiven Testfaelle, jeweils auch mit
dem aktiven Satz. Er schreibt keine Einsaetze, Ingests, Alarm- oder Druckauftraege.
Das Zeitlimit betraegt 10 Sekunden fuer die isolierte Parser-Auswertung. Die Tabelle
zeigt Feld-Diffs und Erwartungsfehler, Regressionen zuerst. Ein bestandener
Testfall, der jetzt fehlschlaegt, oder ein schlechterer Status/Qualitaetswert gilt
als Regression. Neu bestandene Testfaelle oder bessere Status-/Qualitaetswerte
gelten als neu korrekt, sofern keine Regression vorliegt.

**Aktivieren** wiederholt diese Pruefungen. Alle aktiven Testfaelle muessen bestehen
und es darf keine Regression geben. Ein bewusster Override erfordert die Checkbox
und eine Begruendung; beides wird zusammen mit der Zusammenfassung auditiert.
Bei Zeitlimit oder ungueltigen Regeln ist keine Freigabe moeglich. Zum
**Zuruecksetzen** die gewuenschte archivierte Vorversion oeffnen und **Auf diese
Vorversion zuruecksetzen** waehlen; es gelten dieselben Pruefungen und Audit-Regeln.
Die Aktivierung aendert nur kuenftige Verarbeitung. Bestehende Einsaetze werden
nur mit **Neu verarbeiten** in der Alarmempfang-Diagnose erneut ausgewertet.
