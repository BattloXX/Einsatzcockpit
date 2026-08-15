# Installation mit Docker Compose

← [Zurück zur Startseite](Home.md)

Docker Compose startet die App mit MariaDB 10.11 und Redis 7. Redis ist wegen der
zwei Gunicorn-Worker verpflichtend. Ein Reverse-Proxy mit TLS bleibt erforderlich;
Compose ersetzt NGINX nicht.

## 1. Voraussetzungen

Docker Engine mit Compose-Plugin und Git müssen installiert sein. Repository klonen:

```bash
git clone https://github.com/BattloXX/Einsatzcockpit.git
cd Einsatzcockpit
```

## 2. `.env` anlegen

```bash
cp deploy/docker.env.example .env
nano .env
```

Alle leeren Geheimnisse sicher befüllen. Besonders wichtig sind
`MARIADB_ROOT_PASSWORD`, `MARIADB_PASSWORD`, `SECRET_KEY`, `FERNET_KEY` und
`BOOTSTRAP_ADMIN_PASSWORD`. Das Passwort in `DATABASE_URL` muss mit
`MARIADB_PASSWORD` übereinstimmen; Sonderzeichen müssen URL-kodiert werden.

Im Compose-Netz werden Service-Namen statt `127.0.0.1` verwendet:

```ini
DATABASE_URL=mysql+pymysql://einsatzleiter:PASSWORT@db:3306/einsatzleiter
REDIS_URL=redis://redis:6379/0
```

`MEDIA_STORAGE_DIR`, `OBJEKT_MEDIA_DIR`, `NACHSCHLAGEWERK_DATA_DIR` und
`BACKUP_DIR` liegen standardmäßig unter `app_storage/`. Das benannte Volume
`app_storage` bindet dieses Verzeichnis persistent nach `/app/app_storage` ein.

## 3. Starten

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f app
```

Der Entrypoint führt bei jedem App-Start automatisch `alembic upgrade head` aus.
Seed-Daten bleiben bewusst ein manueller Schritt nach dem ersten Start:

```bash
docker compose exec app python -m app.seed_data
```

Wenn kein Bootstrap-Admin konfiguriert wurde, einen Admin manuell anlegen:

```bash
docker compose exec app python -m app.cli create-admin \
  --username admin --password 'SICHERES-PASSWORT'
```

## 4. Reverse-Proxy und TLS

Der Host-Port ist aus Sicherheitsgründen nur als `127.0.0.1:8092` veröffentlicht.
Port 8092 nicht in der Firewall freigeben. NGINX auf demselben Host kann an
`http://127.0.0.1:8092` weiterleiten; WebSocket-Header und Timeouts stehen unter
[NGINX-Reverse-Proxy](Installation-NGINX-Reverse-Proxy.md).

TLS wird außerhalb von Compose terminiert, etwa mit Certbot auf dem Host oder einem
vorgelagerten Reverse-Proxy-Container. `TRUSTED_PROXY_IPS` muss zur tatsächlichen
Proxy-Adresse passen; niemals beliebige Quellnetze eintragen.

## 5. Updates

```bash
git pull origin main
docker compose up -d --build
docker compose ps
```

Migrationen laufen beim Neustart automatisch. Bei einem fremden, vorgebauten Image
lautet der Ablauf stattdessen `docker compose pull && docker compose up -d`.

## 6. Backups

Vor Updates und regelmäßig im Betrieb Datenbank und Medien getrennt sichern:

```bash
docker compose exec -T db sh -c \
  'mariadb-dump -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE"' \
  > einsatzcockpit.sql
```

Zusätzlich das Volume `app_storage` sichern. Ein bloßes Kopieren des Datenbank-Volumes
im laufenden Betrieb ist kein konsistenter Dump. Aufbewahrung, Off-Site-Kopie und
Restore-Proben beschreibt [Backup & Disaster-Recovery](Betrieb-Backup-und-Disaster-Recovery.md).

---

**Nächster Schritt:** [Erst-Setup](Installation-Erst-Setup.md)
