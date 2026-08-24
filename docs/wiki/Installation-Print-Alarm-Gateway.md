# Print & Alarm Gateway einrichten (Docker)

← [Zurück zur Startseite](Home)

Der **ECPG-Gateway** ist ein kleiner Docker-Container im Feuerwehrhaus (eigenes Repo
[`BattloXX/einsatzcockpit-gateway`](https://github.com/BattloXX/Einsatzcockpit-gateway)),
der die lokale Infrastruktur mit der Cloud verbindet:

- **serieller Leitstellen-Alarm** über einen W&T Com-Server (TCP) → automatische
  Einsatzanlage
- **Netzwerkdruck** (CUPS/IPP-Everywhere) für Einsatzinfo, Objektunterlagen,
  GSL-Lageblatt und Alarm-Rohtext

Der Container baut **ausschließlich ausgehende** Verbindungen auf (WSS zur Cloud, TCP
zum W&T, IPP zu Druckern) — **keine Portfreigaben, kein VPN, keine offenen Ports**.

Die Verwaltung (Drucker, Druckregeln, W&T-Konfiguration) erfolgt vollständig im
Web-UI: [Print & Alarm Gateway (Administration)](Administration-Print-Alarm-Gateway).

---

## Voraussetzungen

| Anforderung | Details |
|---|---|
| Docker-Host im Feuerwehrhaus | Beliebiger Linux-Host mit Docker (auch Raspberry Pi — Image ist Multi-Arch amd64/arm64) |
| Netzwerkzugang | Ausgehend zur Cloud (443/WSS), ins LAN zum W&T Com-Server und zu den Druckern |
| W&T Com-Server | Im Modus **TCP-Socket-Server** (serielle Leitstellen-Leitung → LAN), z. B. [W&T Com-Server](https://www.wut.de/) |
| Netzwerkdrucker | A4, IPP/AirPrint (driverless); Altgeräte per `socket://<ip>:9100` möglich |
| Rolle `org_admin` | Zum Anlegen des Gateways und Erzeugen des Pairing-Codes im Web-UI |

---

## Schritt 1 — Modul aktivieren

Einmalig im Web-UI (siehe [Administration](Administration-Print-Alarm-Gateway)):
systemweit (System-Admin) **und** je Organisation (Org-Admin) unter
`/admin/settings`.

## Schritt 2 — Gateway anlegen & Pairing-Code erzeugen

Unter **Gateway / Druck** (`/gateway`):

1. **+ Gateway anlegen** (Name, z. B. `Gerätehaus`).
2. Im Detail **Pairing-Code erzeugen** — 8-stelliger Code, **10 Minuten gültig**,
   wird nur einmal angezeigt.

## Schritt 3 — Container starten

`docker-compose.yml` auf dem Docker-Host:

```yaml
services:
  ecpg-gateway:
    image: ghcr.io/battloxx/einsatzcockpit-gateway:latest
    network_mode: host          # nötig für mDNS/SNMP-Discovery
    restart: unless-stopped
    environment:
      ECPG_CLOUD_URL: https://app.einsatzcockpit.com
      ECPG_PAIRING_CODE: "ABCD2345"   # Code aus Schritt 2 – nur beim Erststart
      TZ: Europe/Vienna
    volumes:
      - ecpg-data:/data         # SQLite, Spool, Device-Token
volumes:
  ecpg-data:
```

```bash
docker compose up -d
```

Nach dem ersten erfolgreichen Pairing den `ECPG_PAIRING_CODE` wieder **entfernen**
(das langlebige Device-Token liegt danach im `ecpg-data`-Volume).

> **`network_mode: host`** ist für die mDNS-/SNMP-Discovery (Multicast/Broadcast)
> nötig. Alternativ ist ein `macvlan`-Netz möglich; dann funktioniert die
> automatische Druckererkennung ggf. eingeschränkt (Drucker lassen sich weiterhin
> „Per IP hinzufügen").

### Pairing alternativ über die Statusseite

Statt der ENV-Variable kann der Code auch auf der lokalen Statusseite eingegeben
werden: `http://<gateway-ip>:8631/` → Feld **Kopplung**.

## Schritt 4 — Konfiguration im Web-UI

Sobald der Container **Online** ist:

1. **Alarmleitung** (W&T): Host/IP, Port (Standard 8000), Zeichensatz, Datagramm-Ende,
   Notfalldrucker.
2. **Drucker**: „Netzwerk durchsuchen" oder „Per IP hinzufügen", dann **Übernehmen**
   und **Testseite**.
3. **Druckregeln**: z. B. „Einsatzinfo bei Alarm".

Details: [Administration](Administration-Print-Alarm-Gateway).

---

## Umgebungsvariablen

| Variable | Default | Bedeutung |
|---|---|---|
| `ECPG_CLOUD_URL` | `http://localhost:8092` | Basis-URL der Cloud (WSS wird abgeleitet) |
| `ECPG_PAIRING_CODE` | – | Einmal-Code fürs erste Pairing (danach entfernen) |
| `ECPG_DATA_DIR` | `/data` | SQLite, Spool, Device-Token (als Volume mounten!) |
| `ECPG_STATUS_PORT` | `8631` | Lokale Statusseite |
| `TZ` | `Europe/Vienna` | Zeitzone |

---

## Statusseite & Healthcheck

`http://<gateway-ip>:8631/` (read-only): Verbindungsstatus Cloud & W&T, letzte Alarme
(Rohtext), Spool-Inhalt, Druckerstatus, Version — plus `/healthz` für Docker.

---

## Betriebshinweise

- **W&T absichern:** Web-Konfiguration des Com-Servers mit Passwort schützen; Zugriff
  auf das Gateway per Firewall/VLAN einschränken.
- **DHCP-Reservierung** für Drucker empfohlen (stabile IP; IP-Wechsel wird zwar
  erkannt, eine feste Adresse ist robuster).
- **Redundanz/Übergang:** Der W&T Com-Server++ kann die serielle Leitung im
  Multipoint-Modus parallel an den bestehenden Alarmdrucker **und** das Gateway
  verteilen.
- **Updates:** Watchtower-kompatibel; zusätzlich meldet das Web-UI verfügbare
  Versionen. Manuell: `docker compose pull && docker compose up -d`.

---

## Troubleshooting

| Symptom | Ursache / Lösung |
|---|---|
| Gateway bleibt „Nicht gekoppelt" | Code abgelaufen (10 min) oder falsch → neuen Pairing-Code erzeugen; `ECPG_CLOUD_URL` prüfen |
| Status „Offline" trotz laufendem Container | Ausgehende WSS-Verbindung blockiert (Firewall/Proxy); Container-Logs `docker logs ecpg-gateway` |
| „Alarmleitung getrennt" | W&T-Host/Port falsch oder Com-Server nicht im TCP-Server-Modus |
| Alarm kommt, aber falsch/kein Text | Zeichensatz/Datagramm-Strategie anpassen; Rohtext im Ringpuffer auf der Statusseite prüfen |
| Druckjob bleibt hängen | Drucker offline → Job wird mit Backoff wiederholt, danach Fallback-Drucker; Druckerstatus im Web-UI |
| Keine Drucker bei „Netzwerk durchsuchen" | `network_mode: host` fehlt, oder Drucker in anderem VLAN → „Per IP hinzufügen" nutzen |

---

**Verwandt:** [Print & Alarm Gateway (Administration)](Administration-Print-Alarm-Gateway) ·
[SMS-Gateway einrichten](Installation-SMS-Gateway)

**Nächster Schritt:** [Erst-Setup](Installation-Erst-Setup)
