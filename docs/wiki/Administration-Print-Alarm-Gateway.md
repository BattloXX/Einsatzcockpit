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

Im Abschnitt **Druckregeln**: pro Regel wählbar

| Feld | Beschreibung |
|------|--------------|
| Auslöser | `einsatz_created`, `einsatz_updated`, `gsl_created`, `gsl_lage_updated`, `alarm_serial_received` |
| Filter | z. B. Mindest-Alarmstufe, Stichwort |
| Dokumente | Einsatzinfo, GSL-Lageblatt, Alarm-Rohtext |
| Objekt-Elemente | Feuerwehrplan, BMA-Laufkarten, Hydrantenplan … (wenn dem Einsatz ein Objekt zugeordnet ist) |
| Ziel | ein oder mehrere Drucker + optionaler Fallback-Drucker |
| Optionen | Kopien, Duplex, Farbe |

Beispiel: „Einsatzinfo bei Alarm" → Auslöser `einsatz_created`, Dokument
`Einsatzinfo`, Ziel `Florianstation`. Bei jeder Einsatzanlage (API, LIS, seriell,
manuell) wird die Einsatzinfo automatisch gedruckt.

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
