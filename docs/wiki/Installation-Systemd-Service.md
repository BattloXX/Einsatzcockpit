# Systemd-Service

← [Zurück zur Startseite](Home)

Die folgenden Pfade entsprechen dem bestehenden CloudPanel-Setup. Für einen Server
ohne Hosting-Panel stehen die angepassten Benutzer und Pfade unter
[Debian/Ubuntu manuell](Installation-Debian-Manuell).

## Unit-Datei kopieren

```bash
sudo cp /home/clp-einsatz/htdocs/einsatzleiter/deploy/einsatzleiter.service \
        /etc/systemd/system/einsatzleiter.service
```

## Unit-Datei anpassen

```bash
sudo nano /etc/systemd/system/einsatzleiter.service
```

Relevante Zeilen:

```ini
[Service]
User=clp-einsatz
WorkingDirectory=/home/clp-einsatz/htdocs/einsatzleiter
EnvironmentFile=/home/clp-einsatz/htdocs/einsatzleiter/.env
ExecStart=/home/clp-einsatz/htdocs/einsatzleiter/.venv/bin/gunicorn \
    -k uvicorn.workers.UvicornWorker \
    -w 2 \
    --bind 127.0.0.1:8092 \
    app.main:app
```

> Passe `User`, `Group`, `WorkingDirectory`, `EnvironmentFile` und `ExecStart` an den
> tatsächlichen Installationsbenutzer und App-Pfad an.

## Dienst aktivieren und starten

```bash
sudo systemctl daemon-reload
sudo systemctl enable einsatzleiter
sudo systemctl start einsatzleiter
sudo systemctl status einsatzleiter
```

Erwartete Ausgabe: `Active: active (running)`

## Logs anzeigen

```bash
# Aktuelle Logs:
journalctl -u einsatzleiter -f

# Letzte 100 Zeilen:
journalctl -u einsatzleiter -n 100

# Seit gestern:
journalctl -u einsatzleiter --since yesterday
```

## Dienst neu starten (z.B. nach Update)

```bash
sudo systemctl restart einsatzleiter
```

## Dienst stoppen

```bash
sudo systemctl stop einsatzleiter
```

## Anzahl Worker anpassen

Empfehlung: **`-w 2`** (Standard-Template) auf einem Einzelserver mit NGINX `ip_hash`.  
Formel allgemein: `2 × CPU-Kerne`. Wert in `/etc/systemd/system/einsatzleiter.service` anpassen → `daemon-reload` → `restart`.

> **Hinweis WebSockets:** Mit mehreren Workern müssen WebSocket-Verbindungen per NGINX `ip_hash`
> (Sticky Sessions) immer zum selben Worker geleitet werden – sonst kommen Live-Updates (Board,
> Lagekarte) nicht bei allen Clients an. Wenn NGINX nicht auf `ip_hash` gestellt werden kann:
> `-w 1` verwenden. Die Render-Performance bleibt trotzdem gut, da GET-Endpoints den
> anyio-Threadpool (bis zu 40 parallele Threads) nutzen.

---

**Nächster Schritt:** [NGINX Reverse-Proxy konfigurieren](Installation-NGINX-Reverse-Proxy)
