# App-Installation

← [Zurück zur Startseite](Home.md)

## 1. Repository klonen

```bash
# Als CloudPanel-Site-User (z.B. clp-einsatz):
cd /home/clp-einsatz/htdocs/
git clone https://github.com/BattloXX/Einsatzcockpit.git einsatzleiter
cd einsatzleiter
```

## 2. Virtuelle Umgebung anlegen

```bash
python3.14 -m venv .venv
source .venv/bin/activate
```

## 3. Abhängigkeiten installieren

```bash
pip install --upgrade pip
pip install -e ".[dev]"
```

> Das installiert FastAPI, SQLAlchemy, WeasyPrint, pywebpush und alle weiteren Pakete aus `pyproject.toml`.

## 4. Konfigurationsdatei anlegen

```bash
cp .env.example .env
nano .env   # oder vim .env
```

**Pflichtfelder:**

```ini
# Datenbankverbindung (aus dem Datenbank-Einrichten-Schritt):
DATABASE_URL=mysql+pymysql://einsatzleiter:PASSWORT@127.0.0.1:3306/einsatzleiter

# Zufälliger Session-Secret (min. 32 Zeichen):
SECRET_KEY=hier-einen-langen-zufaelligen-string-eintragen

# Öffentliche URL der App (für QR-Codes und Web-Push):
APP_BASE_URL=https://einsatzleiter.feuerwehr-wolfurt.at

# VAPID-Keys für Web-Push (Generierung siehe unten):
VAPID_PRIVATE_KEY=
VAPID_PUBLIC_KEY=

# Bootstrap-Admin (wird beim ersten Start automatisch angelegt):
BOOTSTRAP_ADMIN_USER=admin
BOOTSTRAP_ADMIN_PASSWORD=sicheres-admin-passwort
```

### VAPID-Keys generieren

```bash
python3.14 -c "
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
print('Private:', v.private_key.private_bytes(
    __import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding','PrivateFormat','NoEncryption']).Encoding.PEM,
    __import__('cryptography.hazmat.primitives.serialization', fromlist=['PrivateFormat']).PrivateFormat.PKCS8,
    __import__('cryptography.hazmat.primitives.serialization', fromlist=['NoEncryption']).NoEncryption()
).decode())
print('Public:', v.public_key.public_bytes(
    __import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding','PublicFormat']).Encoding.X962,
    __import__('cryptography.hazmat.primitives.serialization', fromlist=['PublicFormat']).PublicFormat.UncompressedPoint
).hex())
"
```

Alternativ: https://vapidkeys.com/ (Browser-Tool, kein Server-Upload)

## 5. Datenbankschema anlegen (Migrationen)

```bash
source .venv/bin/activate
alembic upgrade head
```

> Legt alle Tabellen in der MariaDB an.

## 6. Stammdaten einfüllen (Seed)

```bash
python -m app.seed_data
```

> Füllt Fahrzeugliste Wolfurt, Nachbarwehren, Alarmstichwörter, Auftragsvorschläge, Qualifikationen.

## 7. Erster Test

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8092
```

Im Browser: `http://127.0.0.1:8092/login`
Login mit `admin` / dem Passwort aus `BOOTSTRAP_ADMIN_PASSWORD`.

---

**Nächster Schritt:** [Systemd-Service einrichten](Installation-Systemd-Service.md)
