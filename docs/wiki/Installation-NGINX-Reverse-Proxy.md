# NGINX Reverse-Proxy (CloudPanel)

← [Zurück zur Startseite](Home.md)

Diese Seite beschreibt den bestehenden CloudPanel-Weg. Einen vollständigen,
CloudPanel-freien Vhost samt Certbot-Aufruf zeigt
[Debian/Ubuntu manuell](Installation-Debian-Manuell.md). Für Docker bleibt die Proxy-
und WebSocket-Konfiguration gleich; der Upstream ist ebenfalls `127.0.0.1:8092`.

## CloudPanel-Site anlegen

1. **CloudPanel** → **Sites** → **Add Site**
2. Typ: **Reverse Proxy**
3. Domain: `einsatzleiter.feuerwehr-wolfurt.at` (deine Domain)
4. Upstream: `http://127.0.0.1:8092`
5. **Add Site** klicken

## NGINX-Konfiguration anpassen

Öffne die generierte NGINX-Konfiguration der Site in CloudPanel unter  
**Sites** → **[deine Domain]** → **Vhost** und füge das Snippet aus `deploy/nginx-snippet.conf` ein.

Oder bearbeite direkt:

```bash
sudo nano /etc/nginx/sites-enabled/einsatzleiter.feuerwehr-wolfurt.at.conf
```

Minimale Konfiguration mit WebSocket-Support:

```nginx
server {
    listen 80;
    server_name einsatzleiter.feuerwehr-wolfurt.at;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name einsatzleiter.feuerwehr-wolfurt.at;

    ssl_certificate     /etc/nginx/ssl-certificates/einsatzleiter.crt;
    ssl_certificate_key /etc/nginx/ssl-certificates/einsatzleiter.key;

    # Statische Dateien direkt ausliefern (schneller als Proxy):
    location /static/ {
        alias /home/clp-einsatz/htdocs/einsatzleiter/app/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # WebSocket-Verbindungen (benötigen Upgrade-Header):
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

    # Alle anderen Anfragen:
    location / {
        proxy_pass http://127.0.0.1:8092;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

## TLS/SSL via Let's Encrypt

In CloudPanel: **Sites** → **[deine Domain]** → **SSL/TLS** → **Actions** → **New Let's Encrypt Certificate**

Das Zertifikat wird automatisch erneuert.

## Konfiguration testen und neu laden

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Verbindung testen

```bash
curl -I https://einsatzleiter.feuerwehr-wolfurt.at/
# Erwartete Antwort: HTTP/2 200 oder 302 zum Login
```

WebSocket-Test (benötigt `websocat`):
```bash
websocat wss://einsatzleiter.feuerwehr-wolfurt.at/ws/incident/1
```

---

**Nächster Schritt:** [Erst-Setup](Installation-Erst-Setup.md)
