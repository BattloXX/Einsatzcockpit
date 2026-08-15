# Troubleshooting

← [Zurück zur Startseite](Home)

## App startet nicht

**Symptom:** `systemctl status einsatzleiter` zeigt `failed`

```bash
# Details anzeigen:
journalctl -u einsatzleiter -n 50 --no-pager
```

Häufige Ursachen:

| Fehler | Ursache | Lösung |
|--------|---------|--------|
| `Can't connect to MySQL server` | DB-Verbindung schlägt fehl | DATABASE_URL in `.env` prüfen, MariaDB läuft? |
| `ImportError: No module named 'app'` | venv nicht aktiviert / falscher Pfad | `WorkingDirectory` in service-Datei prüfen |
| `Address already in use` | Port 8092 belegt | `ss -tlnp \| grep 8092` → Prozess beenden |
| `SECRET_KEY not set` | `.env` nicht geladen | `EnvironmentFile` in service-Datei prüfen |

## Docker

Wenn der App-Container nicht startet, zuerst Status und vollständige Logs prüfen:

```bash
docker compose ps
docker compose logs app
docker compose logs db
```

Häufige Ursachen:

- **App beendet sich sofort:** Pflichtwerte in `.env` und `DATABASE_URL` mit Host `db`
  prüfen. Der konkrete Fehler steht in `docker compose logs app`.
- **Keine Schreibrechte im Storage:** Das Volume muss unter `/app/app_storage` gemountet
  und für den Non-Root-Benutzer `einsatzcockpit` schreibbar sein. Bei einem selbst
  eingebundenen Host-Verzeichnis dessen Besitzer/Rechte prüfen.
- **DB bleibt `unhealthy`:** `MARIADB_ROOT_PASSWORD`, `MARIADB_DATABASE`,
  `MARIADB_USER` und `MARIADB_PASSWORD` in `.env` prüfen. Details liefert
  `docker compose logs db`. Die App wartet auf den erfolgreichen Healthcheck der DB.

## Login funktioniert nicht

- Passwort falsch → Reset: `python -m app.cli reset-password --username admin --password neues-pw`
- Session-Cookie blockiert → Browser-Cache leeren, HTTPS prüfen (Cookies werden nur über HTTPS gesetzt)
- `.env` SECRET_KEY geändert → alle Sessions werden ungültig, neu einloggen

## WebSocket-Verbindung bricht ab

```bash
# NGINX-Logs prüfen:
tail -f /var/log/nginx/error.log
```

Häufige Ursachen:
- **Upgrade-Header fehlt:** NGINX-Konfiguration um `proxy_set_header Upgrade $http_upgrade;` ergänzen
- **Timeout:** `proxy_read_timeout 3600s;` setzen
- **Mehrere Worker:** Sticky Sessions via `ip_hash` in NGINX upstream aktivieren

## PDF-Generierung schlägt fehl

```bash
journalctl -u einsatzleiter | grep -i weasyprint
```

Häufige Ursachen:
- Fehlende Systempakete: `sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0`
- Schriftarten fehlen: `sudo apt-get install -y fonts-liberation`

## API-Key wird abgelehnt (401)

1. Key existiert: `python -m app.cli list-api-keys`
2. Key nicht widerrufen: Spalte `revoked_at` ist NULL?
3. Key nicht abgelaufen: Spalte `expires_at` ist NULL oder in der Zukunft?
4. Header korrekt: `X-API-Key: fwwo_xxxx` (nicht `Bearer fwwo_xxxx`)

## Alembic-Migration schlägt fehl

```bash
alembic current
alembic history
```

Falls die Datenbank in einem inkonsistenten Zustand ist:
```bash
# Aktuellen Stand erzwingen (Vorsicht!):
alembic stamp head
```

### errno 150 „Foreign key constraint is incorrectly formed"

Ursache: FK-Spalte hat falschen Typ (häufig `INT` statt `BIGINT`) oder eine andere Tabelle hat noch einen FK auf die zu ändernde Tabelle.

- Alle FK-Spalten auf `fire_dept.id` müssen `BIGINT` sein.
- Vor `ALTER TABLE` auf einer referenzierten Tabelle müssen alle eingehenden FKs per `INFORMATION_SCHEMA` gefunden und entfernt werden.
- Referenz: [MIGRATION_RUNBOOK.md](../MIGRATION_RUNBOOK.md)

### errno 1060 „Duplicate column name"

Ursache: Migration wurde nach einem Teilfehler erneut ausgeführt, `ADD COLUMN` scheitert weil die Spalte schon existiert.

Lösung: Neue Migrationen verwenden `ADD COLUMN IF NOT EXISTS` (MariaDB 10.11+). Bei älteren Migrationen: Spalten-Existenz über `INFORMATION_SCHEMA.COLUMNS` prüfen, dann bedingt anlegen.

## Push-Benachrichtigungen werden nicht zugestellt

1. VAPID-Keys in `.env` korrekt gesetzt?
2. `APP_BASE_URL` korrekt (wird als VAPID-Subject verwendet)?
3. Browser hat Permission erteilt?
4. Logs: `journalctl -u einsatzleiter | grep -i push`

## Hoher Speicherverbrauch

WeasyPrint kann für große PDFs viel RAM brauchen. Falls der Server unter Last gerät:
- Anzahl Gunicorn-Worker reduzieren (`-w 1`)
- PDF-Generierung in einen separaten Queue-Worker auslagern (zukünftiges Feature)

## Port 8092 von außen erreichbar

Firewall-Regel hinzufügen:
```bash
sudo ufw deny 8092/tcp
```

Nur NGINX (80/443) soll von außen erreichbar sein.
