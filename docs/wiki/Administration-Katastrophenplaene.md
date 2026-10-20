# Katastrophenpläne (Administration)

Anwender-Doku: [Katastrophenpläne](Anwender-Katastrophenplaene).

## Aktivierung

1. **System** (nur `system_admin`): **Einstellungen → System → Katastrophenpläne**
   (`katplan_module_enabled`).
2. **Organisation** (Org-Admin): **Einstellungen → Katastrophenpläne → für diese
   Organisation aktivieren** (`OrgSettings.katplan_modul_aktiv`).

Erst wenn beide Schalter aktiv sind, erscheinen Menüeintrag und Kachel. Alle Daten
(Pläne, Quellen, Anhänge, Versionen) sind an die eigene Organisation gebunden.

Datenbank: Migrationen **0294–0299** (`alembic upgrade head`).

## Rollen und Rechte

| Rolle | Umfang |
|-------|--------|
| `readonly`, `recorder`, `breathing_supervisor`, `incident_leader`, `objekt_verwalter` | Freigegebene Pläne, Versionen, Suche, Karte, Quellen der Freigabe und PDF lesen |
| `katplan_verwalter` (Planverwalter) | zusätzlich Arbeitsstand bearbeiten: Kapitel, Phasen, Elemente, Verknüpfungen, Anhänge, Quellen, KI-Import-Review; einreichen |
| `katplan_freigeber` (Planfreigeber) | zusätzlich zurückweisen, freigeben, archivieren, reaktivieren; erhält die Wiedervorlage-Mail |
| `org_admin`, `admin`, `system_admin` | wie Planfreigeber |

Rollen vergibt der Org-Admin unter **Benutzer → Rollen**
([Benutzer und Rollen](Administration-Benutzer-und-Rollen)).

- Ein **Vier-Augen-Prinzip** gibt es nicht. Ein Freigeber kann eigene Änderungen direkt
  freigeben.
- Offene **Qualitätshinweise** werden angezeigt, sperren die Freigabe aber nicht.
- Den **Arbeitsstand** sehen nur Planverwalter und Planfreigeber.

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

## Wiedervorlage

Unter **Stammdaten** des Plans wird ein Wiedervorlagedatum gesetzt. Einmal täglich prüft der
Server fällige Pläne. Er schreibt eine Mail an alle aktiven Planfreigeber mit E-Mail-Adresse;
fehlen solche, geht sie an die Org-Admins. Voraussetzung sind ein aktives Modul und eine
funktionierende Mail-Konfiguration der Organisation
([Mail-Versand](Administration-Mail-Versand)). In Liste und Kopf erscheint ein fälliger Termin
rot. Nach der Überarbeitung den Termin neu setzen.

## Speicher und Datensicherung

Planquellen (Original, Einzelseiten-PDFs, Seitenbilder) und Anhänge (Original und Vorschau)
liegen unter `MEDIA_STORAGE_DIR/<org_id>/katplan/` und belegen die Speicherquota der
Organisation. **Speicher neu berechnen** zählt Quellen und Anhänge mit. Bei ausgeschöpfter
Quota schlägt ein Upload mit Hinweis fehl.

- Eine Quelle, die noch von einem Element oder einer Freigabe verwendet wird, wird beim
  Entfernen nur archiviert. So bleiben freigegebene Versionen nachvollziehbar.
- Entfernte Anhänge bleiben gespeichert, solange eine Version sie referenziert.

Siehe auch: [Entwicklung](Entwickler-Katastrophenplaene).
