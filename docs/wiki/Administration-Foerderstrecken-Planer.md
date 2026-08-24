# Förderstrecken-Planer – Administration

← [Zurück zur Startseite](Home)

> URL: `/admin/foerderpumpen`, `/admin/foerderschlaeuche`, `/admin/foerderkalibrierung`
> Zugänglich für: `org_admin`, `admin`, `system_admin`

---

## Modul aktivieren (zweistufig)

Wie bei UAS/Objekt: erst systemweit, dann je Organisation.

1. **System-Admin**: *Einstellungen → Systemweite Module → Förderstrecken-Planer* → einschalten.
2. **Org-Admin**: *Einstellungen → Förderstrecken-Planer* → „für diese Organisation aktivieren".

Ohne beide Schalter ist das Modul unsichtbar; Ausschalten löscht **keine** Daten.

---

## Pumpen verwalten (Self-Service)

**Stammdaten → Förderstrecke-Geräte** (`/admin/foerderpumpen`).

- **➕ Eigene Pumpe**: frei anlegen und die **Kennlinie** selbst konfigurieren — je
  Drehzahlstufe eine Punktliste *Fördermenge Q [l/min] → Förderhöhe H [m]* (H ≈ 10·Druck [bar]).
  Punkte werden nach Q sortiert; H sollte mit steigendem Q fallen (wird geprüft).
- **📋 Aus Vorlage**: übernimmt eine mitgelieferte Vorlage als **editierbare Kopie** deiner Org:
  **TS 1600 (FOX 3)**, **TS 1200**, HLP 16.000 (PAS 200HF), HLP 8.000 (PAS 150), Fremdpumpe.
  Die Vorlagen selbst bleiben unverändert.
- Weitere Felder: Druck-/Sauganschluss (Nennweite + max. parallel), max. Ansaughöhe,
  min. Eingangsdruck, max. Ausgangsdruck, Tankinhalt, optional Fahrzeugzuordnung.

> **Hinweis:** Vorlagen-Kennlinien sind digitalisierte Datenblatt-/Normwerte. Vor
> Produktivnutzung feinjustieren bzw. über einen Nassbewerb **kalibrieren** (siehe unten).
> TS 1200 startet bewusst mit Normwerten (PFPN 10-1000).

## Schläuche verwalten

`/admin/foerderschlaeuche` — Kürzel, Durchmesser, **k-Wert** (bar/100 m @ 1000 l/min),
Elementlänge, max. Betriebsdruck, Vorrat. Wasserinhalt je Meter wird aus dem Durchmesser
berechnet. Vorlagen F-150 / A-110 / B-75 stehen bereit (k = 0,049 / 0,23 / 1,56).

---

## Kalibrierung über Übungsmessungen

**Stammdaten → Förderstrecke-Kalibrierung** (`/admin/foerderkalibrierung`).

1. Nach Übung/Nassbewerb je Schlauchtyp **Messungen erfassen**: gemessene Fördermenge,
   Leitungslänge, Parallelzahl, Höhendifferenz, Druck aus (Pumpe) und Druck ein (Folgepumpe).
2. **Kalibrierung berechnen** → ein Least-Squares-Fit schätzt je Schlauchtyp einen
   korrigierten k-Wert und legt ihn als **Vorschlag** in die Review-Queue (ab ≥ 5 % Abweichung).
3. **Übernehmen** setzt den k-Wert des Schlauchtyps; **Verwerfen** lässt ihn unverändert.

> Vorschläge werden **nie automatisch** übernommen. So bildet die Rechnung mit jedem
> Bewerb euer tatsächliches Schlauchmaterial ab statt Literatur-Mittelwerte.

---

## Höhendaten

Geländehöhen kommen online über **Open-Meteo** (Standard) bzw. optional den **Höhenservice
Österreich** (falls `HOEHEN_AT_URL` gesetzt). Ergebnisse werden gecacht (`hoehen_cache`).
Ein Offline-/DGM-Betrieb ist nicht Teil des Moduls.

---

## Integration

- **PDF „Einsatzplan Wasserförderung"** je Strecke; als Dokumentart `foerderstrecke` ablegbar.
- **Maschinisten-Zettel** als login-freier Token-Link (nur SHA-256-Hash gespeichert; scopet
  strikt auf die eigene Org).
- Relais-Standorte lassen sich als **Wasserstelle** Typ `relais` persistieren.

Siehe auch: [Anwendung](Anwender-Foerderstrecken-Planer).
