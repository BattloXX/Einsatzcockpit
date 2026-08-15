# Datenbank-Einrichtung

← [Zurück zur Startseite](Home.md)

## MariaDB über CloudPanel

1. **CloudPanel öffnen** → `https://<server-ip>:8443`
2. Links im Menü: **Databases** → **Add Database**
3. Folgende Werte eintragen:

| Feld | Wert |
|------|------|
| Database Name | `einsatzleiter` |
| Database User Name | `einsatzleiter` |
| Password | *(sicheres Passwort wählen)* |

4. **Add Database** klicken.

## Zeichensatz sicherstellen (UTF8MB4)

CloudPanel setzt standardmäßig utf8mb4. Zur Kontrolle:

```sql
-- Via CloudPanel phpMyAdmin oder SSH:
mysql -u root -p
SHOW CREATE DATABASE einsatzleiter;
-- Erwartete Ausgabe enthält: DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
```

Falls nicht:
```sql
ALTER DATABASE einsatzleiter
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
```

## Verbindungsstring

Den `DATABASE_URL` für die `.env`-Datei zusammensetzen:

```
DATABASE_URL=mysql+pymysql://einsatzleiter:PASSWORT@127.0.0.1:3306/einsatzleiter
```

> Die Verbindung läuft über `127.0.0.1` (lokale Verbindung), nicht über einen externen Host.

## Verbindung testen (optional)

```bash
python3.14 -c "
import pymysql
conn = pymysql.connect(host='127.0.0.1', user='einsatzleiter',
                       password='PASSWORT', db='einsatzleiter')
print('Verbindung OK:', conn.get_server_info())
conn.close()
"
```

---

**Nächster Schritt:** [App installieren](Installation-App-Installation.md)
