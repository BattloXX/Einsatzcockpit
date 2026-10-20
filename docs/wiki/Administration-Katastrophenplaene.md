# Katastrophenpläne (Administration)

Anwender-Doku: [Katastrophenpläne](Anwender-Katastrophenplaene).

## Aktivierung und Rechte

Systemadministratoren aktivieren das Modul in den Systemeinstellungen (`katplan_module_enabled`). Danach aktiviert der Org-Admin **Katastrophenpläne** in den Organisationseinstellungen (`katplan_modul_aktiv`). Beide Schalter müssen aktiv sein; Inhalte bleiben an die eigene Organisation gebunden.

| Berechtigung | Umfang |
|-------------|--------|
| Einsatzrollen einschließlich `readonly` | Freigegebene Pläne, Historie, Suche, Karte und PDF lesen |
| `katplan_verwalter` | Zusätzlich Arbeitsstand, Quellen, Anhänge und KI-Vorschläge bearbeiten |
| `katplan_freigeber` | Zusätzlich einreichen, zurückweisen, freigeben und archivieren |
| Organisations-/Systemadministration | Verwaltung entsprechend den zentralen Rollenrechten |

Die Freigabe erfordert kein Vier-Augen-Prinzip. Offene Qualitätshinweise werden angezeigt und sperren die Freigabe nicht. Der Arbeitsstand bleibt für Leser unsichtbar.

## Vertraulichkeit und Kontaktdaten

Vertrauliche Elemente und Anhänge sind nur für die zentral berechtigten Rollen sichtbar (Planverwalter, Freigeber, Einsatzleitung und Administration). Diese Regel gilt auch für Suche, PDF und MCP. Telefonangaben unterliegen zusätzlich den Kontaktrechten: eingeschränkte Leser erhalten nur zulässige Einsatztelefonnummern. Objektunterlagen benötigen das aktive Objektmodul und das passende Objektrecht.

## KI-Import und MCP

Der KI-Import hat eigene System- und Org-Schalter; zusätzlich muss das Katplan-Modul aktiv sein. KI-Vorschläge werden erst durch Übernahme zu Elementen. Telefonnummern, Mengen und Koordinaten fachlich prüfen. Das Speichern des Elements in der UI bestätigt die menschliche Prüfung.

MCP bietet lesende Werkzeuge für die sichtbaren Fassungen sowie Schreibwerkzeuge für den Arbeitsstand. Schreibzugriffe benötigen Bearbeitungsrechte und werden mit Herkunft `mcp` protokolliert. MCP kann keine Freigabe auslösen. OAuth- und Modulrechte gelten zusätzlich zu den Planrechten.

## Wiedervorlage und Speicher

Setze am Plan ein Wiedervorlagedatum. Die periodische Prüfung erkennt fällige Pläne und verschickt eine Wiedervorlage-Mail an aktive Freigeber mit E-Mail-Adresse; fehlen diese, an die Organisationsadministration. Das Modul und eine Mail-Konfiguration müssen aktiv sein. Eine neue Freigabe bzw. ein geänderter Termin setzt die Erinnerung zurück. Prüfe bei fehlenden Mails die Mail-Konfiguration der Organisation.

Quellen und Anhänge belegen die Speicherquota der Organisation. PDF-Quellen enthalten neben dem Original verarbeitete Seiten und Vorschaubilder. Bei ausgeschöpfter Quota schlägt ein Upload fehl. Referenzierte Quellen werden beim Entfernen archiviert, damit freigegebene Versionen weiterhin nachvollziehbar bleiben.

Siehe auch: [Entwicklung](Entwickler-Katastrophenplaene).
