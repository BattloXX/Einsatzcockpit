# Cloudflare Turnstile einrichten

← [Zurück zur Startseite](Home)

Cloudflare Turnstile ist die letzte, optionale Schutzschicht für das öffentliche
Kontaktformular. Es ergänzt Honeypot, URL-Filter im Namensfeld und Rate-Limit;
die technische Reihenfolge und das Server-Verhalten beschreibt
[Öffentliches Kontaktformular](Entwickler-Sicherheit#öffentliches-kontaktformular-post-kontakt).

---

## Voraussetzungen

| Anforderung | Details |
|---|---|
| Cloudflare-Account | Zum Anlegen und Verwalten des Turnstile-Widgets unter `dash.cloudflare.com` |
| Öffentliche Domain der Einsatzcockpit-Instanz | Wird beim Widget als erlaubte Domain eingetragen |
| Zugriff auf die `.env` | Zum Eintragen von Site Key und Secret Key |

---

## Schritt 1 — Turnstile-Widget bei Cloudflare anlegen

1. Bei [dash.cloudflare.com](https://dash.cloudflare.com/) anmelden.
2. **Turnstile** öffnen und ein neues Widget anlegen.
3. Als Widget-Typ **Managed** oder **Invisible** auswählen:
   - **Managed** zeigt dem Besucher bei Bedarf ein sichtbares Turnstile-Widget.
   - **Invisible** läuft für den Besucher im Hintergrund.
4. Die Domain der eigenen Einsatzcockpit-Instanz als Domain des Widgets eintragen.
5. Site Key und Secret Key aus dem Dashboard bereithalten.

Turnstile funktioniert eigenständig. Die Domain muss dafür nicht als DNS-Zone bei
Cloudflare liegen und nicht über den Cloudflare-Proxy (orange Wolke) laufen; ein
DNS-Umzug zu Cloudflare ist für Turnstile nicht erforderlich.

---

## Schritt 2 — Keys in `.env` eintragen

Die `.env` der Einsatzcockpit-Installation öffnen und beide Werte eintragen. Die
vorbereiteten Einträge stehen auch in [`.env.example`](../../.env.example):

```ini
TURNSTILE_SITE_KEY=site-key-aus-cloudflare
TURNSTILE_SECRET_KEY=secret-key-aus-cloudflare
```

Der Site Key wird für das Widget verwendet. Das Kontaktformular prüft das
übermittelte Token mit dem Secret Key serverseitig bei Cloudflare.

---

## Schritt 3 — App neu starten

Einsatzcockpit liest die `.env` beim Start in die globale Konfiguration ein; einen
Reload der geänderten Umgebungsvariablen im laufenden Prozess gibt es nicht.
Starten Sie die App daher nach dem Speichern der `.env` neu:

- Bei einem systemd-Betrieb:

  ```bash
  sudo systemctl restart einsatzleiter
  ```

  Siehe [Systemd-Service](Installation-Systemd-Service).

- Bei Docker Compose:

  ```bash
  docker compose up -d
  ```

  Siehe [Docker Compose](Installation-Docker).

---

## Schritt 4 — Kontaktformular prüfen

Das Kontaktformular unter [`/ueber-das-projekt#kontakt`](/ueber-das-projekt#kontakt)
testweise absenden. Bei **Managed** sollte das Turnstile-Widget erscheinen; bei
**Invisible** läuft die Prüfung im Hintergrund.

Die Prüfung erfolgt serverseitig. Erreicht die App Cloudflare nicht, etwa bei einem
Timeout oder Netzwerkfehler, wird die Formularübermittlung als fehlgeschlagen
behandelt und nicht zugelassen (fail closed).

---

## Turnstile deaktivieren

Beide Variablen leer lassen:

```ini
TURNSTILE_SITE_KEY=
TURNSTILE_SECRET_KEY=
```

Das Kontaktformular läuft dann weiter ohne CAPTCHA. Honeypot, URL-Filter im
Namensfeld und das IP-basierte Rate-Limit bleiben aktiv.

---

**Verwandt:** [Öffentliches Kontaktformular](Entwickler-Sicherheit#öffentliches-kontaktformular-post-kontakt) — technische Beschreibung des Spam-Schutzes; [App-Installation](Installation-App-Installation) — `.env` anlegen und bearbeiten.

**Nächster Schritt:** [Erst-Setup](Installation-Erst-Setup)
