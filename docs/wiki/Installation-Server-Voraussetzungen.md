# Server-Voraussetzungen

← [Zurück zur Startseite](Home)

## Mindest-Hardware

| Komponente | Minimum | Empfohlen |
|------------|---------|-----------|
| CPU | 1 vCore | 2 vCores |
| RAM | 1 GB | 2 GB |
| Disk | 10 GB | 20 GB SSD |
| Netz | 10 Mbit/s | 100 Mbit/s |

Für 10 gleichzeitige Browser-Sessions reicht 1 GB RAM. WeasyPrint für große PDFs kann kurz ~300 MB extra benötigen.

## Ports und Firewall

Der App-Prozess lauscht intern auf Port `8092`. Nach außen ist **nur der Reverse-Proxy**
(Port 80/443) erreichbar. Port 8092 darf nicht direkt aus dem Internet erreichbar sein.

```bash
# Beispiel mit ufw:
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp   # SSH
sudo ufw enable
```

## Welcher Installationsweg?

| Weg | Geeignet für | Anleitung |
|-----|--------------|-----------|
| CloudPanel | Bestehende oder neue CloudPanel-Server mit verwaltetem NGINX, TLS und Datenbank | [App-Installation mit CloudPanel](Installation-App-Installation) |
| Debian/Ubuntu manuell | Klassischer Einzelserver ohne Hosting-Panel; volle Kontrolle über systemd und NGINX | [Debian/Ubuntu manuell](Installation-Debian-Manuell) |
| Docker Compose | Container-basierter Betrieb mit gebündelter MariaDB und Redis | [Docker Compose](Installation-Docker) |

Die folgenden Pakete gelten für die beiden nativen Installationswege. Im Docker-Weg
werden Python und alle Systempakete durch das Image bereitgestellt.

## Betriebssystem

**Debian 12 Bookworm** (empfohlen) oder Ubuntu 22.04 LTS.

Für den CloudPanel-Weg installiert und verwaltet
[CloudPanel](https://www.cloudpanel.io) NGINX, SSL, Datenbanken und Cron-Jobs.
Installation nach der offiziellen Anleitung:

```bash
curl -sS https://installer.cloudpanel.io/ce/v2/install.sh -o install.sh
sudo bash install.sh
```

## Python 3.14

```bash
# Prüfen ob Python 3.14 vorhanden:
python3.14 --version

# Falls nicht (Deadsnakes PPA für Debian/Ubuntu):
sudo apt-get update
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt-get install -y python3.14 python3.14-venv python3.14-dev
```

## Systempakete für WeasyPrint, MariaDB und PDF-Rendering

WeasyPrint (PDF-Generierung) benötigt Pango/Cairo. Der MariaDB-Connector braucht die Dev-Header.
`poppler-utils` rendert die PDF-Seiten der **Objektverwaltung** (pdf2image) — ohne Poppler werden
hochgeladene PDFs zwar zerlegt, aber ohne Vorschaubilder abgelegt.
`tesseract-ocr` (+ Sprachpaket `tesseract-ocr-deu`) liefert die **OCR-Volltextsuche** für gescannte
Objektdokumente. Ohne Tesseract wird nur der eingebettete PDF-Textlayer indexiert — reine Scan-PDFs
sind dann nicht durchsuchbar (die App startet trotzdem).
`ffmpeg` verarbeitet hochgeladene Videos und erzeugt Vorschaubilder.

```bash
sudo apt-get install -y \
    libmariadb-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    build-essential \
    ffmpeg \
    poppler-utils \
    tesseract-ocr tesseract-ocr-deu \
    git
```

---

**Nächster Schritt:** [Datenbank einrichten](Installation-Datenbank-Einrichtung)
