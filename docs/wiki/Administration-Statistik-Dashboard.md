# Statistik-Dashboard

← [Zurück zur Startseite](Home)

## Aufruf

Menü → **Statistik** (oder `/statistik`)

Zugänglich für: Admin, Einsatzleiter

## KPI-Kacheln (oben)

| Kachel | Bedeutung |
|--------|-----------|
| **Einsätze gesamt** | Anzahl aller Einsätze im Zeitraum |
| **Durchschnittsdauer** | Mittlere Einsatzdauer in Minuten |
| **Atemschutz-Einsätze** | Anzahl Einsätze mit AS-Überwachung |
| **Häufigste Kategorie** | Technik oder Feuer |

## Diagramme

### Einsätze pro Monat

Balkendiagramm (Chart.js): Anzahl der Einsätze je Monat im gewählten Zeitraum.

### Durchschnittsdauer je Stichwort

Balkendiagramm: Mittlere Einsatzdauer für jedes Stichwort (T1–T7, F1–F4).

### Atemschutz-Einsatzzeiten je Mitglied

Tabelle: Kumulierte AS-Einsatzzeit je Mitglied. Nützlich für die Tauglichkeits-Nachverfolgung (gesetzliche Anforderung in Österreich).

### Häufigste Einsatzgründe

Wort-Wolke oder Balkendiagramm: Häufigste Begriffe aus dem Feld „Meldung"/„Einsatzgrund".

## Zeitraum-Filter

Oben rechts: Zeitraum auswählen (aktuelles Jahr, letztes Jahr, letzter Monat, benutzerdefiniert).

## Übungen ein-/ausblenden

Toggle oben: **Übungen einschließen**

Standard: Übungen werden **nicht** in der Statistik gezählt. Mit dem Toggle können sie für eine Übungsauswertung eingeblendet werden.

## CSV-Export

**Daten exportieren** → CSV-Datei mit allen Rohdaten für externe Auswertung (z.B. Excel).

## Hinweis zu laufenden Einsätzen

Laufende (nicht abgeschlossene) Einsätze werden in der Statistik nicht berücksichtigt.
