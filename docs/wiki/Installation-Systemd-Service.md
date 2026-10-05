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
    -w 1 \
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

Empfehlung: **`-w 1`**. SMS-Gateway-Sockets gehören jeweils genau einem Gunicorn-Worker;
der SMS-Versand sieht auch mit Redis nur die Gateways seines eigenen Workers. Alarm-SMS
funktionieren deshalb zuverlässig nur mit einem Worker.

Hintergrund-Loops wie DIBOS, LIS, Alarm-Outbox und SMS-Nachversand werden über den Leader-Lock
`LEADER_LOCK_PATH` (Standard `app_storage/background-leader.lock`) nur in einem Worker gestartet.

---

**Nächster Schritt:** [NGINX Reverse-Proxy konfigurieren](Installation-NGINX-Reverse-Proxy)
