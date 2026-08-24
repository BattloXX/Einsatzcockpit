# Erst-Setup nach der Installation

← [Zurück zur Startseite](Home)

## Admin-User verifizieren

Beim ersten App-Start wird automatisch ein Admin-User aus `.env` angelegt  
(`BOOTSTRAP_ADMIN_USER` / `BOOTSTRAP_ADMIN_PASSWORD`).

Prüfen:
```bash
python -m app.cli list-users
```

Manuell einen Admin erstellen (falls nötig):
```bash
python -m app.cli create-admin --username admin --password sicheres-passwort
```

## API-Key für das Alarmierungssystem erzeugen

Das Alarmierungssystem sendet Einsätze per REST-API. Dazu braucht es einen API-Key:

```bash
python -m app.cli create-api-key --label "Alarmierungssystem FWWO"
```

Ausgabe:
```
API-Key: fwwo_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Key-ID: 1
Label: Alarmierungssystem FWWO
```

> **Den Key sofort kopieren!** Er wird nur einmal im Klartext angezeigt.  
> Dieser Key kommt in die Konfiguration des Alarmierungssystems als `X-API-Key`-Header.

## Stammdaten prüfen

```bash
python -m app.cli check-seed
```

Erwartet: Wolfurt-Fahrzeuge, 5 Nachbarwehren, Alarmstichwörter (T1–T7, F1–F4), Qualifikationen.

Falls die Seed-Daten fehlen:
```bash
python -m app.seed_data
```

## Ersten Login testen

1. Browser öffnen: `https://einsatzleiter.feuerwehr-wolfurt.at/login`
2. Benutzername: `admin`, Passwort: aus `.env`
3. Nach Login: Startseite mit leerer Einsatzliste

## Heimwehr-Org prüfen

Beim App-Start wird automatisch eine **Heimwehr** angelegt (`is_home_org = true`). Sie bildet die Basis-Organisation für alle Stammdaten.

Prüfen:
```bash
python -m app.cli list-orgs
```

## Weitere Benutzer anlegen

In der Webapp: **Admin** → **Benutzer** → **Neuer Benutzer**

Oder per CLI:
```bash
python -m app.cli create-user --username stefan --password passwort --role incident_leader
```

Verfügbare Rollen: `system_admin`, `org_admin`, `admin`, `incident_leader`, `breathing_supervisor`, `recorder`, `readonly`

Für Multi-Org-Betrieb: den künftigen Org-Admin über einen Einladungslink einrichten (→ [Organisationen verwalten](Administration-Organisations-verwalten)).

## Passwort ändern

In der Webapp: Oben rechts → Benutzername → **Passwort ändern**

---

**Nächster Schritt:** [Backups einrichten](Installation-Backups)
