# NGINX Reverse-Proxy (CloudPanel)

Bei aktivem MCP-Server braucht `/mcp` deaktiviertes Proxy-Buffering und lange Timeouts; auch OAuth-Discovery und OAuth-Endpunkte müssen durchgereicht werden. Details: [MCP-Server](Administration-MCP-Server).

← [Zurück zur Startseite](Home)

Diese Seite beschreibt den bestehenden CloudPanel-Weg. Einen vollständigen,
CloudPanel-freien Vhost samt Certbot-Aufruf zeigt
[Debian/Ubuntu manuell](Installation-Debian-Manuell). Für Docker bleibt die Proxy-
und WebSocket-Konfiguration gleich; der Upstream ist ebenfalls `127.0.0.1:8092`.

## CloudPanel-Site anlegen

1. **CloudPanel** → **Sites** → **Add Site**
2. Typ: **Reverse Proxy**
3. Domain: `einsatzcockpit.com` (deine Domain)
4. Upstream: `http://127.0.0.1:8092`
5. **Add Site** klicken

## NGINX-Konfiguration anpassen

Öffne die generierte NGINX-Konfiguration der Site in CloudPanel unter  
**Sites** → **[deine Domain]** → **Vhost** und füge das Snippet aus `deploy/nginx-snippet.conf` ein.

Oder bearbeite direkt:

```bash
sudo nano /etc/nginx/sites-enabled/einsatzcockpit.com.conf
```

Minimale Konfiguration mit WebSocket-Support:

```nginx
server {
    listen 80;
    server_name einsatzcockpit.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name einsatzcockpit.com;

    ssl_certificate     /etc/nginx/ssl-certificates/einsatzcockpit.crt;
    ssl_certificate_key /etc/nginx/ssl-certificates/einsatzcockpit.key;

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
curl -I https://einsatzcockpit.com/
# Erwartete Antwort: HTTP/2 200 oder 302 zum Login
```

WebSocket-Test (benötigt `websocat`):
```bash
websocat wss://einsatzcockpit.com/ws/incident/1
```

---

**Nächster Schritt:** [Erst-Setup](Installation-Erst-Setup)


### Medien-Caches und Logos

Geschuetzte `/objekt-medien/seite/{id}/thumb|bild|pdf`, Dokumentoriginale,
Symbolbilder und BSP-Raster/Ausschnitte senden `Cache-Control: private, no-cache`
sowie `ETag` und `Last-Modified`. Der Browser muss jeden Abruf revalidieren;
bei unveraenderter Darstellung antwortet die App mit 304 ohne Dateiinhalte.
Organisations- und Freigabepruefungen erfolgen auch vor einer 304-Antwort.
Drehung, Originalauswahl und Einzeichnungsversion sind Teil des Seiten-ETags.
Ein `If-None-Match` hat Vorrang vor `If-Modified-Since`. Fuer unmittelbare
Erkennung solcher Variantenwechsel sollen Clients den ETag verwenden;
`Last-Modified` beschreibt die Aenderungszeit der ausgelieferten Quelldatei.
Das Sync-Manifest (weiterhin Version 2) ergaenzt pro Seite `version` und
`versioned_urls`; die bisherigen `urls` bleiben queryfrei. Fuer Thumb/Bild/PDF
setzt die App bei `?v=<aktuelle Medienversion>`
`Cache-Control: private, max-age=31536000, immutable`. Bei fehlendem, leerem
oder veraltetem `v` bleibt es bei `private, no-cache`. ETag, Last-Modified,
304 und die vorherige Org-Pruefung bleiben erhalten. Die Medienversion umfasst
Dateigroessen/mtime_ns, Drehung, Einzeichnungsversion und Ausgabeformatversion.

Der Android-Sync speichert Medien unter queryfreien Cache-Storage-Schluesseln
und merkt sich deren Version in localStorage. Fehlt Cache Storage nach dem
Beenden des Hintergrund-WebViews, kann der HTTP-Cache den versionierten Abruf
bedienen. Nur erfolgreiche Cache-Schreibvorgaenge bestaetigen eine Version;
Speicherfehler (z. B. QuotaExceededError) erscheinen in der App-Diagnose.
Parallele Aufrufe teilen einen Lauf. Nach erfolgreichem Sync gilt ein
Mindestabstand von 30 Minuten (auch fuer WorkManager ohne Argumente);
`window.objektOfflineSync({force:true})` erzwingt einen neuen Lauf.
Private Medien duerfen im Reverse-Proxy nicht oeffentlich gecacht werden.

Im produktiven Vhost ist der `/static/`-Block derzeit auskommentiert.
Empfohlen ist die Aktivierung mit `expires 7d; add_header Cache-Control "public";`
und gzip fuer CSS/JavaScript/JSON (`gzip_static on` allein benoetigt vorhandene
`.gz`-Dateien). Die deploy-Vorlagen sind nicht die aktive Produktionskonfiguration.
Falls nginx `/static` nicht selbst ausliefert, setzt der App-Fallback
`Cache-Control: public, max-age=604800`; mit Query-Parameter `v` gilt
`public, max-age=31536000, immutable`. Bei versionierten URLs muss sich `v`
aendern, sobald sich der Inhalt aendert. Bei direkter nginx-Auslieferung gelten
die Header der nginx-Konfiguration.

Neue Organisationslogos werden auf maximal 512 Pixel an der laengsten Seite
verkleinert und von EXIF-Metadaten bereinigt. PNG-Transparenz und Dateiformat
bleiben erhalten, SVG bleibt unveraendert. Bestehende Uploads im App-Verzeichnis
lassen sich einmalig (oder wiederholt) mit der Anwendungsumgebung nachziehen:

```bash
python -m app.cli logos-verkleinern
```

Der Befehl bearbeitet `app/static/img/uploads/logo_org*` direkt und gibt die
Dateigroessen vorher/nachher in Bytes aus. Bereits bereinigte kleine Logos
bleiben unveraendert; wiederholte Laeufe aendern diese Dateien nicht.
