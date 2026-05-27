# Atemschutzüberwachung

← [Zurück zur Startseite](Home)

Entspricht **FwDV 7 „Atemschutz"** und dem **ASÜW-Leitfaden (Falter A6, V0)**.

---

## Ansicht öffnen

Im Einsatz-Board: Button **AS-Überwachung** in der Kopfleiste, oder direkt `/einsatz/{id}/atemschutz`.

Die Atemschutz-Ansicht ist für alle Rollen lesbar. Änderungen dürfen:
- Admin, Einsatzleiter, AS-Überwacher, Bearbeiter

---

## Trupp anlegen

1. **+ Trupp anlegen** klicken.
2. **Trupp-Bezeichnung** eingeben (z.B. „Trupp 1 – RLF").
3. **Auftrag** beschreiben (z.B. „Innenangriff 2. OG").
4. **Standort / Einsatzabschnitt** eingeben (z.B. „Treppenhaus Süd").
5. **Einheit (Funkrufname)** eintragen (z.B. „1. Gruppe").
6. **Flasche / Einsatzzeit** wählen:
   - `1 × 6 L (300 bar)` → **33 min** Grundeinsatzzeit
   - `1 × 6,8 L (300 bar)` → **37 min**
   - `1 × 9 L (300 bar)` → **50 min**
   - `manuell` → eigene Minutenzahl eingeben
7. Optional **Fahrzeug** zuweisen.
8. Mindestens **2 Truppmitglieder** ausfüllen (Truppführer TF + Truppmann TM).
9. Für jedes Mitglied den **Anfangsdruck** im Picker wählen (max. 300 bar).

> **Hinweis:** 200-bar-Flaschen und 2×6,8 L-Geräte werden nicht mehr unterstützt.

---

## Anfangsdrücke

Der Druck-Picker geht von 10 bis 300 bar in 10er-Schritten.  
**Rückzugsdruck wird automatisch berechnet:**

```
Rückzugsdruck = Anfangsdruck × 0,5 + 10 bar (Sicherheitsreserve)
Beispiel: 300 bar × 0,5 + 10 = 160 bar
```

Der niedrigste Rückzugsdruck aller Mitglieder gilt für den gesamten Trupp.

---

## Einsatz starten

**▶ Einsetzen** klicken → `entry_at` wird gesetzt → Stoppuhr und Warnungen laufen.

---

## Timer und Warnungen

| Ereignis | Anzeige | Akustik |
|---|---|---|
| 1/3 der Einsatzzeit verstrichen, keine Lagemeldung | Gelbes Badge „⚠ Lagemeldung fällig (1/3)" | Ton ~10 s (muss mind. 10 s laut hörbar sein, Leitfaden) |
| Rückzugsdruck unterschritten | Rotes Badge „⚠ RÜCKZUGSDRUCK" | Alarm-Ton ~5 s |
| Max-Einsatzzeit überschritten | Rotes Badge „⚠ MAX-EINSATZZEIT" | **Dauerton** bis manuelle Quittierung |

Jedes Badge hat einen **„✓ Quit."-Button** zum Bestätigen.

> **1/3-Warnung wird zurückgesetzt**, sobald eine neue Lagemeldung eingetragen oder ein neuer Druck gemeldet wird.

---

## Lagemeldung absetzen

Button **📢 Lagemeldung** in der Truppliste → Freitext eingeben → „Meldung absetzen".

Die letzte Meldung mit Zeitstempel erscheint in der Truppliste und im Protokoll-PDF.

Auch das Eintragen eines neuen Drucks gilt automatisch als Lagemeldung (zählt für die 1/3-Frist).

---

## Druckupdates

Während des Einsatzes: Pro Mitglied auf **„Druck"** klicken → Wert aus dem Picker wählen.

Druckwerte werden in der Meldungs-Chronik aufgezeichnet (Zeit, Mitglied, bar, optionale Notiz).

---

## Status-Wechsel

| Von | Nach | Bedeutung |
|-----|------|-----------|
| Bereit | Im Einsatz | Trupp betritt den Gefahrenbereich |
| Im Einsatz | Rückzug | Rückzugsdruck erreicht oder Rückruf |
| Rückzug | Zurück | Trupp hat Gefahrenbereich verlassen |
| Zurück | Erholt | Trupp ist wieder einsatzbereit |

---

## Abschlussdruck eintragen

Nach dem Einsatz: Restdrücke über den Druck-Picker je Mitglied eintragen.

---

## PDF-Export

### Einzelprotokoll (Trupp)

In der Truppliste: **📄 PDF**-Button → vollständiges A4-Protokoll mit:
- Stammdaten (Name, Funkrufname, Auftrag, Standort, Gerätetype, Einsatzdauer)
- Mitgliedertabelle (Anfangsdruck, Soll-Rückzugsdruck, Enddruck, AGT-Qualifikation)
- Zeitachse (1/3-Zeitpunkt soll/ist, Rückzug, Zurück)
- vollständige Meldungs-Chronik (Druck, Notiz, Uhrzeit)
- Quittierungsnachweis

Auch im **Archiv** (Detail-Ansicht) ist ein PDF-Link je Trupp verfügbar.

### Gesamtbericht

Im Abschlussbericht des Einsatzes ist eine eigene Seite **„Atemschutz-Protokoll"** enthalten – mit identischem vollständigen Inhalt für alle Trupps.

---

## Technische Anforderungen (Leitfaden)

| Anforderung | Umsetzung |
|---|---|
| Digitaluhr mit laufender Zeit | ✓ Stoppuhr pro Trupp ab Einsatzbeginn |
| Kurzzeituhr mit geplanter Einsatzzeit | ✓ Timer „mm:ss / Plan-min" mit Fortschrittsbalken |
| Warnsignal nach 1/3 der Einsatzzeit, laut ≥ 10 s | ✓ Ton + Badge |
| Warnsignal bei Rückzugsdruck | ✓ |
| Warnsignal Dauerton bei Max-Zeit bis Quittierung | ✓ |
| Felder: Name, Funkrufname, Gerätetype | ✓ (name, unit_name, bottle_preset) |
| Felder: Auftrag, Standort | ✓ |
| Druck bei Einsatzbeginn / Einsatzziel / Einsatzende | ✓ (start_press / pressure_logs / back_press) |
| Letzte Meldung (Zeitpunkt + Text) | ✓ |
| PDF-Export mit aller Dokumentation | ✓ |
