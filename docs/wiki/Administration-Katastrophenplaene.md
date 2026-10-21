# Einsatz- & Katastrophenpläne (Administration)

Anwender-Doku: [Einsatz- & Katastrophenpläne](Anwender-Katastrophenplaene).
Das Modul hieß bis Oktober 2026 „Katastrophenpläne“; intern (Rollen-Codes `katplan_*`,
Einstellungen, MCP-Werkzeugnamen) bleiben die Namen gleich.

## Aktivierung

1. **System** (nur `system_admin`): **Einstellungen → System → Einsatz- &
   Katastrophenpläne** (`katplan_module_enabled`).
2. **Organisation** (Org-Admin): **Einstellungen → Einsatz- & Katastrophenpläne → für
   diese Organisation aktivieren** (`OrgSettings.katplan_modul_aktiv`).

Erst wenn beide Schalter aktiv sind, erscheinen Menüeintrag und Kachel. Alle Daten
(Pläne, Quellen, Anhänge, Versionen) sind an die eigene Organisation gebunden.

Datenbank: Migrationen **0294–0299** sowie **0302** (Planart), **0303** (Prüfzyklus) und
**0304** (Akteure) (`alembic upgrade head`). Bestandspläne erhalten die Planart
„Katastrophenplan“; die Planart lässt sich in den Stammdaten ändern.

## Rollen und Rechte

| Rolle | Umfang |
|-------|--------|
| `readonly`, `recorder`, `breathing_supervisor`, `incident_leader`, `objekt_verwalter` | Freigegebene Pläne, Versionen, Suche, Karte, Quellen der Freigabe und PDF lesen |
| `katplan_verwalter` (Planverwalter) | zusätzlich Arbeitsstand bearbeiten: Kapitel, Phasen, Elemente, Verknüpfungen, Anhänge, Quellen, KI-Import-Review; einreichen |
| `katplan_freigeber` (Planfreigeber) | zusätzlich zurückweisen, freigeben, archivieren, reaktivieren, Akteurskatalog pflegen, jede Überprüfung bestätigen; erhält Eskalations-Mails |
| `org_admin`, `admin`, `system_admin` | wie Planfreigeber |

Rollen vergibt der Org-Admin unter **Benutzer → Rollen**
([Benutzer und Rollen](Administration-Benutzer-und-Rollen)).

- Ein **Vier-Augen-Prinzip** gibt es nicht. Ein Freigeber kann eigene Änderungen direkt
  freigeben.
- Offene **Qualitätshinweise** werden angezeigt, sperren die Freigabe aber nicht.
- Den **Arbeitsstand** sehen nur Planverwalter und Planfreigeber.
- Die **Überprüfung bestätigen** darf die im Plan eingetragene prüfende Person, auch ohne
  Freigaberolle, sowie jeder Planfreigeber.
- **Neue Kontakte** aus dem Telefon-Assistenten anlegen dürfen nur Benutzer mit
  Kontaktpflege-Recht (`kontakt_verwalter`, `objekt_verwalter`, Admins). Alle anderen
  können nur bestehende Kontakte verknüpfen.

## Akteure

Unter **Einsatz- & Katastrophenpläne → Akteure verwalten** (`/plaene/akteure`,
Planfreigeber) wird der Katalog der zuständigen Stellen gepflegt. Beim ersten Aufruf wird
er mit Feuerwehr, Netzbetreiber, Behörde, Polizei, Rettungsdienst, Gemeinde/Bauhof und
Sonstige befüllt. Eigene Einträge (z. B. „Wasserverband“) bekommen Bezeichnung, Farbe,
Icon und Reihenfolge. Verwendete Akteure lassen sich nur deaktivieren, nicht löschen;
freigegebene Versionen zeigen weiterhin die Bezeichnung zum Freigabezeitpunkt.

## Vertraulichkeit und Kontaktdaten

Vertraulich markierte Elemente und Anhänge sehen nur Planverwalter, Planfreigeber,
Einsatzleiter (`incident_leader`) und Admins. Die Regel gilt in Oberfläche, Suche, PDF und MCP.

**Telefonnummern verknüpfter Kontakte:**

- Wer das Kontaktverzeichnis sehen darf, sieht alle Nummern.
- Alle anderen sehen nur Dienstnummern von Kontakten, die als **einsatzrelevant**
  gekennzeichnet sind. Es lohnt sich deshalb, die Kennzeichnung in der Kontaktverwaltung
  zu pflegen.

Objektdetails und Objektdokumente erscheinen nur bei aktivem Objektmodul und Objekt-Leserecht.

## Adresssuche und Organisationsort

Die Adresssuche im Editor und die Option `verorten` der MCP-Werkzeuge nutzen den
Geokodierdienst (Nominatim, `NOMINATIM_*`). Fehlt der Ort in der Adresse eines Elements,
wird der **Ort der Organisation** ergänzt
([Einstellungen](Administration-Einstellungen) → Kontaktdaten, Feld **Ort**). Es lohnt sich,
ihn zu pflegen, damit gleichnamige Straßen anderer Gemeinden nicht vorgeschlagen werden.
Aus der Suche gesetzte Orte gelten immer als **ungeprüft**.

Für die MCP-Serienverortung (`katplan_elemente_verorten`) lassen sich zuerst Kandidaten
anzeigen und anschließend einzeln auswählen. Das hilft bei Brücken, Durchlässen und
Bachabschnitten ohne Hausadresse. Die Titelsuche begrenzt ihr Suchgebiet auf ±0,08 Grad
um die Organisationskoordinaten, wenn beide gesetzt sind: **Einstellungen → Karte /
Lagekarte.info → Fallback-Breitengrad (lat)** und **Fallback-Längengrad (lng)**
(`fallback_lat`, `fallback_lng`). Ohne Koordinaten erfolgt die Suche mit dem Organisationsort.
Mehrdeutige Adressen setzt `verorten` nicht automatisch. Nach Übernahme die Lage vor Ort
prüfen und im Editor als geprüft markieren.

## KI-Import

- **Voraussetzungen:** Der zentrale KI-Assistent ist aktiv (`ai_enabled` und API-Schlüssel
  bzw. BYOK der Organisation). Zusätzlich muss unter **Einstellungen → Katastrophenpläne →
  KI-Import für Planquellen aktivieren** der Org-Schalter `katplan_ki_import_enabled`
  gesetzt sein. Standardmäßig ist er aus.
- **Datenschutz:** Für die Analyse werden Seitentext und, falls vorhanden, Seitenbilder einer
  Planquelle an den KI-Anbieter übertragen, einschließlich darin enthaltener Namen und
  Telefonnummern. Vor dem Aktivieren klären, ob das für die betroffenen Dokumente zulässig ist.
- **Prompt:** Der variable Teil ist unter **KI-Prompts → Katastrophenplan-Import** je
  Organisation anpassbar. Fester Rahmen: JSON-Antwort, keine Erfindungen, Koordinaten nie
  erzeugen, Telefonnummern und Mengen nur wörtlich.
- **Review:** Vorschläge werden erst durch ausdrückliche Übernahme zu Elementen (Herkunft
  `ai_suggested`). Telefonnummern werden nie übernommen. Kontakte und Objekte werden nur
  verknüpft, wenn ein Bearbeiter das Häkchen setzt. Die Qualitätsanzeige meldet „KI
  ungeprüft“, bis das Element einmal in der Oberfläche gespeichert wurde.

## MCP

Lesende Werkzeuge stehen allen Leserollen zur Verfügung, schreibende nur Planverwaltern,
Planfreigebern und Admins. Schreibzugriffe ändern ausschließlich den Arbeitsstand und werden
im Planprotokoll mit Herkunft `mcp` geführt. Kein Werkzeug kann einreichen oder freigeben.
Details: [MCP-Server](Administration-MCP-Server).

## Prüfzyklus und Erinnerungen

Jeder Plan hat eine prüfende Person (ECP-Benutzer), ein Prüfintervall (Standard je Planart:
AEP 12, Maßnahmen- und Katastrophenplan 24 Monate, je Plan überschreibbar, 1–60 Monate)
und das Datum der nächsten Überprüfung. Freigabe und **Überprüfung bestätigen** setzen das
Datum auf heute + Intervall.

Einmal täglich prüft der Server die Termine und schreibt je Stufe genau eine Mail:

| Stufe | Zeitpunkt | Empfänger |
|---|---|---|
| Vorab | 14 Tage vor dem Termin | prüfende Person |
| Fällig | am Termin | prüfende Person |
| Eskalation | 30 Tage nach dem Termin | Planfreigeber und prüfende Person |

Fehlt die prüfende Person (oder hat sie keine E-Mail-Adresse), gehen Vorab- und
Fälligkeits-Mails an die Planfreigeber, ersatzweise an die Org-Admins. Voraussetzung sind ein
aktives Modul und eine funktionierende Mail-Konfiguration der Organisation
([Mail-Versand](Administration-Mail-Versand)). In Liste, Kopf und Dashboard erscheinen fällige
Überprüfungen rot.

## Speicher und Datensicherung

Planquellen (Original, Einzelseiten-PDFs, Seitenbilder) und Anhänge (Original und Vorschau)
liegen unter `MEDIA_STORAGE_DIR/<org_id>/katplan/` und belegen die Speicherquota der
Organisation. **Speicher neu berechnen** zählt Quellen und Anhänge mit. Bei ausgeschöpfter
Quota schlägt ein Upload mit Hinweis fehl.

- Eine Quelle, die noch von einem Element oder einer Freigabe verwendet wird, wird beim
  Entfernen nur archiviert. So bleiben freigegebene Versionen nachvollziehbar.
- Entfernte Anhänge bleiben gespeichert, solange eine Version sie referenziert.

Siehe auch: [Entwicklung](Entwickler-Katastrophenplaene).
