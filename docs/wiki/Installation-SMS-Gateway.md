# SMS-Gateway einrichten

← [Zurück zur Startseite](Home)

Das **Einsatzleiter SMS-Gateway** ist ein separater Docker-Container, der im lokalen Netzwerk des CoNiuGo-Modems läuft und sich **ausgehend** per WebSocket mit der Haupt-App verbindet. Eingehende Ports am Modem-Standort sind nicht erforderlich.

> **Alternative: native Android-App.** Statt eines Docker-Containers mit CoNiuGo-Modem kann auch die native Android-App aus dem Repo `Einsatzcockpit-Android` als Gateway verwendet werden — sie verbindet sich über denselben Token-authentifizierten WebSocket und versendet/empfängt SMS über die SIM-Karte eines Android-Geräts (Foreground-Service, läuft dauerhaft im Hintergrund). Diese Anleitung beschreibt den Docker-Weg; Setup der Android-App ist im dortigen Repo (`SETUP.md`) dokumentiert.

**Zweck:** SMS-Versand/-Empfang für die Telefonnummern-Verifizierung im Bürgerportal, [Einsatzinfo-SMS bei Alarm](Administration-SMS-Einsatzinfo), manuellen Versand an Gruppen/Mitglieder und die Weiterleitung eingehender SMS (Teams-Webhook, Gruppen, Mitglieder, Ad-hoc-Nummern).

---

## Voraussetzungen

| Anforderung | Details |
|---|---|
| CoNiuGo SMS Gateway LTE | Im lokalen Netz des Containers erreichbar (HTTP-Schnittstelle) |
| Docker + Docker Compose | Auf dem Gateway-Host installiert |
| Zugang zur Haupt-App | Container muss `wss://deine-domain.at` via HTTPS/WSS erreichen können |
| Alembic-Migration 0040 | Muss auf dem Haupt-App-Server eingespielt sein (`alembic upgrade head`) |

---

## Schritt 1 — Migration einspielen

Auf dem Server der Haupt-App:

```bash
cd /home/clp-einsatz/htdocs/einsatzleiter
source .venv/bin/activate
alembic upgrade head
```

> Legt die Tabelle `sms_gateway_token` an.

---

## Schritt 2 — Connection-Token erzeugen

```bash
python -m app.cli create-sms-gateway-token --label "Modem Wolfurt" --org-id 1
```

Ausgabe (Beispiel):
```
✓ SMS-Gateway-Token angelegt: smsgw_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   → Diesen Token sicher speichern, er wird nicht erneut angezeigt!
   → Als GATEWAY_TOKEN in der .env des SMS-Gateway-Containers eintragen.
```

> `--org-id` entspricht der `id` in der Tabelle `fire_dept`. Standard für die erste Organisation ist `1`.  
> Der Token wird **nur einmal** angezeigt. Sofort notieren oder direkt in die Container-`.env` kopieren.

---

## Schritt 3 — Container-Repo klonen und konfigurieren

Auf dem **Gateway-Host** (Gerät im Modem-Netz, z. B. Raspberry Pi oder Server am Feuerwehrstandort):

```bash
git clone https://github.com/BattloXX/Einsatzleiter-SMS-Gateway.git
cd Einsatzleiter-SMS-Gateway
cp .env.example .env
nano .env
```

Mindest-Konfiguration in `.env`:

```env
# Domain der Haupt-App (ohne Protokoll)
GATEWAY_DOMAIN=einsatz.example.com

# Token aus Schritt 2
GATEWAY_TOKEN=smsgw_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# TLS verwenden (Produktion: true)
GATEWAY_USE_TLS=true

# IP-Adresse des CoNiuGo-Modems im lokalen Netz
MODEM_URL=http://192.168.1.50/cgi-bin/sendsms

# HTTP-Methode (meist GET beim CoNiuGo)
MODEM_METHOD=GET

# Query-Parameter-Template ({to} = Nummer, {text} = Nachricht, URL-encodiert)
MODEM_QUERY=nr={to}&text={text}

# Kein TLS-Check für lokales HTTP-Modem
MODEM_VERIFY_TLS=false
```

Alle verfügbaren Einstellungen sind in `.env.example` dokumentiert.

---

## Schritt 4 — Container starten

```bash
docker compose up -d
docker compose logs -f
```

Erwartete Ausgabe bei erfolgreicher Verbindung:

```
sms-gateway  | 2026-06-07T12:00:00 [INFO] sms-gateway: Einsatzleiter SMS-Gateway gestartet
sms-gateway  | 2026-06-07T12:00:00 [INFO] sms-gateway.client: Verbinde mit wss://einsatz.example.com/ws/sms-gateway
sms-gateway  | 2026-06-07T12:00:01 [INFO] sms-gateway.client: Verbunden — warte auf SMS-Jobs
```

---

## Modem-Konfiguration

Der Container unterstützt jede CoNiuGo-HTTP-Variante über Platzhalter-Templates:

| Variable | Beschreibung | Standard |
|---|---|---|
| `MODEM_URL` | Vollständige URL des Modem-Endpoints | `http://192.168.1.1/cgi-bin/sendsms` |
| `MODEM_METHOD` | HTTP-Methode: `GET` oder `POST` | `GET` |
| `MODEM_QUERY` | Query-String-Template für GET | `nr={to}&text={text}` |
| `MODEM_BODY` | Body-Template für POST (leer = Form-Data) | *(leer)* |
| `MODEM_BASIC_AUTH` | HTTP Basic-Auth `benutzer:passwort` (optional) | *(leer)* |
| `MODEM_TIMEOUT` | Timeout in Sekunden pro SMS-Request | `10.0` |
| `MODEM_VERIFY_TLS` | TLS-Zertifikat des Modems prüfen | `false` |

> **Hinweis:** Pro SMS öffnet der Container eine eigene HTTP-Verbindung zum Modem und schließt sie sofort danach. Das Modem wird dadurch nie dauerhaft blockiert.

---

## Reconnect-Verhalten

Der Container verbindet sich bei Netzunterbrechung automatisch wieder:

- Erster Reconnect nach 1 Sekunde
- Jeder weitere Versuch verdoppelt die Wartezeit (max. 30 Sekunden)
- Heartbeat-Ping alle 20 Sekunden hält die Verbindung aktiv

---

## Token widerrufen

Wenn ein Token kompromittiert ist oder der Container ausgetauscht wird:

```sql
-- Token in der DB widerrufen (direkter SQL-Zugang)
UPDATE sms_gateway_token SET revoked_at = NOW() WHERE label = 'Modem Wolfurt';
```

Danach neuen Token anlegen (Schritt 2) und Container mit aktualisierter `.env` neu starten.

---

## Protokoll-Referenz

Das vollständige WebSocket-Protokoll zwischen Container und Haupt-App ist im Container-Repo dokumentiert:  
[`PROTOCOL.md` im Einsatzleiter-SMS-Gateway-Repo](https://github.com/BattloXX/Einsatzleiter-SMS-Gateway/blob/main/PROTOCOL.md)

---

**Verwandt:** [SMS-Einsatzinfo, manueller Versand & SMS-Empfang](Administration-SMS-Einsatzinfo) — Einrichtung der eigentlichen SMS-Funktionen, sobald das Gateway verbunden ist.

**Nächster Schritt:** [Erst-Setup](Installation-Erst-Setup)
