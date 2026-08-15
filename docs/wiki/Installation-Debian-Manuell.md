# Installation auf Debian/Ubuntu ohne CloudPanel

← [Zurück zur Startseite](Home.md)

Diese Anleitung beschreibt einen klassischen Einzelserver mit Debian 12 oder Ubuntu
22.04+, systemd und NGINX. Hardware, Firewall und Python-/Systempakete stehen unter
[Server-Voraussetzungen](Installation-Server-Voraussetzungen.md).

## 1. Basisdienste installieren

MariaDB, NGINX, Redis und Certbot werden direkt auf dem Host betrieben:

```bash
sudo apt-get update
sudo apt-get install -y mariadb-server nginx redis-server certbot python3-certbot-nginx
sudo systemctl enable --now mariadb nginx redis-server
```

Die Datenbank und den Datenbankbenutzer wie unter
[Datenbank-Einrichtung](Installation-Datenbank-Einrichtung.md) anlegen.

## 2. Systembenutzer und Anwendung anlegen

```bash
sudo useradd --system --create-home --home-dir /opt/einsatzcockpit \
  --shell /usr/sbin/nologin einsatzcockpit
sudo -u einsatzcockpit git clone \
  https://github.com/BattloXX/Einsatzcockpit.git /opt/einsatzcockpit/app
cd /opt/einsatzcockpit/app
sudo -u einsatzcockpit python3.14 -m venv .venv
sudo -u einsatzcockpit .venv/bin/pip install --upgrade pip
sudo -u einsatzcockpit .venv/bin/pip install -e .
```

## 3. Konfiguration, Migrationen und Seed-Daten

```bash
sudo -u einsatzcockpit cp .env.example .env
sudoedit /opt/einsatzcockpit/app/.env
```

Mindestens `DATABASE_URL`, `SECRET_KEY`, `FERNET_KEY`, `APP_BASE_URL`,
`COOKIE_SECURE=true` und ein sicheres `BOOTSTRAP_ADMIN_PASSWORD` setzen. Bei zwei
Gunicorn-Workern muss außerdem `REDIS_URL=redis://127.0.0.1:6379/0` gesetzt sein.

```bash
cd /opt/einsatzcockpit/app
sudo -u einsatzcockpit .venv/bin/alembic upgrade head
sudo -u einsatzcockpit .venv/bin/python -m app.seed_data
```

Der Seed ist wiederholbar. Ein Admin wird beim ersten App-Start aus
`BOOTSTRAP_ADMIN_USER` und `BOOTSTRAP_ADMIN_PASSWORD` angelegt. Alternativ kann er
nach dem ersten Start manuell erzeugt werden:

```bash
sudo -u einsatzcockpit .venv/bin/python -m app.cli create-admin \
  --username admin --password 'SICHERES-PASSWORT'
```

## 4. systemd-Service einrichten

Die produktive Unit unter `deploy/einsatzleiter.service` dient als Vorlage. Kopiere
sie nach `/etc/systemd/system/einsatzleiter.service` und ersetze darin die
CloudPanel-Pfade (`/home/clp-einsatz/htdocs/einsatzleiter`, User/Group
`clp-einsatz`) durch die hier verwendeten:

```bash
sudo cp /opt/einsatzcockpit/app/deploy/einsatzleiter.service \
  /etc/systemd/system/einsatzleiter.service
sudo sed -i \
  -e 's#/home/clp-einsatz/htdocs/einsatzleiter#/opt/einsatzcockpit/app#g' \
  -e 's/User=clp-einsatz/User=einsatzcockpit/' \
  -e 's/Group=clp-einsatz/Group=einsatzcockpit/' \
  /etc/systemd/system/einsatzleiter.service
```

Kontrolle: `User`, `Group`, `WorkingDirectory`, `EnvironmentFile` und alle Pfade in
`ExecStart` müssen danach auf `einsatzcockpit` bzw. `/opt/einsatzcockpit/app`
zeigen — mit `sudoedit /etc/systemd/system/einsatzleiter.service` gegenprüfen.

Danach aktivieren:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now einsatzleiter
sudo systemctl status einsatzleiter
```

Weitere Erklärungen zu Workern und Logs stehen unter
[Systemd-Service](Installation-Systemd-Service.md).

## 5. NGINX-Vhost anlegen

Aus `deploy/nginx-snippet.conf` wird ein eigenständiger Vhost. Domain und statischen
Pfad anpassen:

```nginx
server {
    listen 80;
    server_name einsatzcockpit.example.at;

    location /static/ {
        alias /opt/einsatzcockpit/app/app/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8092;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8092;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        client_max_body_size 100m;
    }
}
```

Die Datei als `/etc/nginx/sites-available/einsatzcockpit.conf` speichern und aktivieren:

```bash
sudo ln -s /etc/nginx/sites-available/einsatzcockpit.conf \
  /etc/nginx/sites-enabled/einsatzcockpit.conf
sudo nginx -t
sudo systemctl reload nginx
```

WebSocket- und Proxy-Details erklärt
[NGINX-Reverse-Proxy](Installation-NGINX-Reverse-Proxy.md).

## 6. TLS-Zertifikat ausstellen

Die Domain muss bereits auf den Server zeigen. Certbot ergänzt den Vhost und richtet
die automatische Erneuerung ein:

```bash
sudo certbot --nginx -d einsatzcockpit.example.at
sudo nginx -t
sudo systemctl reload nginx
```

Danach `https://einsatzcockpit.example.at/` aufrufen und den ersten Login prüfen.

---

**Nächster Schritt:** [Erst-Setup](Installation-Erst-Setup.md)
