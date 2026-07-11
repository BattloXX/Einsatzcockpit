# Stammdaten pflegen

← [Zurück zur Startseite](Home)

Stammdaten sind die Grundkonfiguration der App: Fahrzeuge, Wehren, Alarmstichwörter, Auftragsvorschläge und Lage-Hinweise.

Sie werden beim ersten Start automatisch aus `app/seed_data.py` befüllt.

## Fahrzeuge

**Admin** → **Fahrzeuge**

Fahrzeuge sind org-spezifisch: jede Organisation verwaltet ihre eigenen Fahrzeuge.

| Feld | Beschreibung |
|------|-------------|
| Code | Kürzel (z.B. `RLF`, `KDOF`) |
| Name | Vollständiger Name |
| Typ | Kategorisierung |
| Kennzeichen | Amtliches Kennzeichen (optional) — dieselbe Angabe wie im Fahrtenbuch, hier oder dort gepflegt wirkt sich auf beide Ansichten aus |
| Erstausrückung | ✓ = dieses Fahrzeug rückt bei T1/T2/F1/F2 aus |
| Reihenfolge | Sortierung im Board |
| Aktiv | Inaktive Fahrzeuge erscheinen nicht mehr |

### Nachbarwehren (Multi-Org)

In einer Multi-Org-Instanz werden Nachbarwehren als eigene Organisationen angelegt und verwalten ihre Fahrzeuge selbst. Bei einem kollaborativen Einsatz erscheinen die Fahrzeuge der beteiligten Orgs automatisch im Board (mit der Org-Farbe als linkem Streifen).

Für Single-Org-Betrieb mit vordefinierten Nachbar-Fahrzeugen: Fahrzeuge anderer Wehren direkt anlegen und ihrer `dept_id` zuordnen (System-Admin).

## Alarm-Stichwörter

**Admin** → **Stichwörter**

Stichwörter sind org-spezifisch (TenantScoped). Jede Organisation hat ihren eigenen Satz.

Jedes Stichwort hat:
- **Code**: z.B. `T1`, `F3`
- **Kategorie**: `Technik` oder `Feuer`
- **Erstausrückung**: Nur Erstausrückungsfahrzeuge oder alle?
- **Nachbarn**: Werden Nachbarwehren automatisch eingeblendet?

## Auftragsvorschläge

**Admin** → **Auftragsvorschläge**

Beim Einsatzstart werden für das gewählte Stichwort automatisch Auftragsvorschläge in der Sidebar angezeigt. Hier können diese gepflegt werden.

Vorschläge sind sortiert nach Reihenfolge-Zahl und werden nach Stichwort gefiltert.

## Lage-Hinweise

**Admin** → **Lage-Hinweise**

Rotierender Ticker in der Sidebar während eines Einsatzes. Kurze Erinnerungen an wichtige Maßnahmen:
- „Geräteträger auf Fremdgase achten"
- „Einsatzleitung dokumentieren"

## Atemschutz-Parameter

**Admin** → **Wehr-Einstellungen**

Die Rückzugsdruck-Formel ist parametrisierbar:

| Parameter | Standard | Beschreibung |
|-----------|---------|-------------|
| Faktor | 0,5 | Multiplikator für den Anfangsdruck |
| Reserve | 10 bar | Fixer Sicherheitszuschlag |

Formel: `Rückzugsdruck = Anfangsdruck × Faktor + Reserve`

> Standard nach FF Vorarlberg: 300 bar × 0,5 + 10 = **160 bar**

## Seed-Daten zurücksetzen

Falls die Stammdaten versehentlich gelöscht wurden:

```bash
python -m app.seed_data
```

> Achtung: Bestehende Daten werden nicht überschrieben, nur fehlende ergänzt.
