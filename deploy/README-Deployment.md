# Deployment-Anleitung (Kurzfassung)

Vollständige Dokumentation: **[GitHub Wiki](../../wiki)**

## Voraussetzungen

- Debian 12 Bookworm
- CloudPanel installiert und konfiguriert
- MariaDB 10.11+ (via CloudPanel)
- Python 3.12

## Systemabhängigkeiten (als root)

```bash
apt install -y python3.12 python3.12-venv python3.12-dev \
    libmariadb-dev build-essential \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libffi-dev libcairo2 libcairo2-dev
```

## Schritt-für-Schritt

```bash
# 1. CloudPanel: Site anlegen (Type: Generic), User: clp-einsatz
#    Domain: einsatzcockpit.com

# 2. Als clp-einsatz User
su - clp-einsatz
cd /home/clp-einsatz/htdocs/
git clone https://github.com/BattloXX/Einsatzcockpit.git einsatzleiter
cd einsatzleiter

# 3. Python-Umgebung
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .

# 4. Konfiguration
cp .env.example .env
nano .env   # DATABASE_URL, SECRET_KEY, VAPID-Keys befüllen

# 5. Datenbank (in CloudPanel angelegt: DB einsatzleiter, User einsatzleiter)
alembic upgrade head
python -m app.seed_data

# 6. Ersten Admin anlegen
python -m app.cli create-admin --username admin --password SICHERES_PASSWORT

# 7. API-Key für Alarmierungssystem
python -m app.cli create-api-key --label "Alarmierungssystem"
# → Key notieren und im Alarmierungssystem eintragen

# 8. Systemd Service (als root)
cp deploy/einsatzleiter.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now einsatzleiter
systemctl status einsatzleiter

# 9. NGINX in CloudPanel konfigurieren
#    → "Vhosts" → Site → "Nginx Config" → Inhalt von deploy/nginx-snippet.conf einfügen

# 10. TLS-Zertifikat
#    → CloudPanel → SSL/TLS → Let's Encrypt

# 11. Logs prüfen
journalctl -u einsatzleiter -f
```

## Reverse-Proxy und Client-IPs

Die App akzeptiert `X-Forwarded-For`/`-Proto` nur von den in `TRUSTED_PROXY_IPS`
gelisteten Adressen (Default `127.0.0.1,::1` — passt zum nginx-Snippet, das auf
demselben Host auf `127.0.0.1:8092` proxied). Läuft nginx/Traefik auf einem
**anderen** Host, dessen IP in `TRUSTED_PROXY_IPS` eintragen. Läuft die App
ganz ohne Reverse-Proxy, `TRUST_PROXY_HEADERS=false` setzen — sonst könnten
Clients ihre IP für Rate-Limits und Audit-Logs fälschen.

## Updates

```bash
su - clp-einsatz
cd /home/clp-einsatz/htdocs/einsatzleiter
git pull
source .venv/bin/activate
pip install -e .
alembic upgrade head
sudo systemctl restart einsatzleiter
```

## Backup & Disaster-Recovery

Automatisierte DB-+Medien-Dumps mit getesteter Restore-Probe (Details:
[Wiki-Runbook](../../wiki/Betrieb-Backup-und-Disaster-Recovery)):

```bash
# Als root: Backup- und Restore-Test-Timer installieren
cp deploy/backup/ec-backup.service deploy/backup/ec-backup.timer /etc/systemd/system/
cp deploy/backup/ec-restore-test.service deploy/backup/ec-restore-test.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ec-backup.timer ec-restore-test.timer
systemctl list-timers 'ec-*'

# Manuell
su - clp-einsatz && cd /home/clp-einsatz/htdocs/einsatzleiter && source .venv/bin/activate
python -m app.cli backup          # beide DBs + Medien nach BACKUP_DIR
python -m app.cli restore-test    # neuesten Dump testweise einspielen + verifizieren
```

Die Restore-Probe braucht `CREATE`/`DROP` auf die Wegwerf-DB
(`einsatzleiter_restore_test`) — einmalig als DB-root gewähren (siehe Runbook).
Die `.env` (v. a. `FERNET_KEY`) **getrennt** sichern — ohne sie sind verschlüsselte
Secrets nach einem Restore unlesbar.

## VAPID-Keys generieren (für Web Push)

```bash
source .venv/bin/activate
python - <<'EOF'
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
print("VAPID_PRIVATE_KEY=" + v.private_key.private_bytes(
    encoding=__import__('cryptography').hazmat.primitives.serialization.Encoding.PEM,
    format=__import__('cryptography').hazmat.primitives.serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=__import__('cryptography').hazmat.primitives.serialization.NoEncryption()
).decode().strip())
print("VAPID_PUBLIC_KEY=" + v.public_key)
EOF
```
