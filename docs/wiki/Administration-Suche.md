# Zentrale Suche administrieren

Unter **Einstellungen → Zentrale Suche** schaltet ein Organisationsadministrator die
Suche für seine Organisation ein. Die Aktivierung plant für jede Indexquelle einen
Neuaufbau in der Warteschlange; die Anfrage selbst indexiert nichts synchron. Bei systemweit
ausgeschalteter Suche ist der Schalter deaktiviert und die Statusseite liefert 404.

**Indexstatus und Neuaufbau** öffnet `/einstellungen/suche`. Die Seite zeigt den
Org-Schalter, Anzahl der Dokumente und Textabschnitte sowie die letzte Indexierung je Quelle.
Eine lange PDF-Seite kann mehrere Textabschnitte erzeugen. Offene Aufträge umfassen `pending` und
`retry`; die älteste offene Zeile zeigt einen möglichen Rückstand. Die letzten zehn
Fehler zeigen Quelle, ID und Versuchszahl mit gekürztem Fehlertext, nur für die eigene Org.

**Index neu aufbauen** plant je Quelle einen Auftrag; bereits offene Neuaufbauten werden
nicht doppelt eingeplant. Der Worker verarbeitet sie bei aktivem Org-Schalter.
**Fehlgeschlagene erneut versuchen** setzt eigene `failed`-Zeilen auf `retry`, den
Versuchszähler auf 0 und den nächsten Versuch auf jetzt. Die Anzeige aktualisiert sich
ohne Neuladen. Vorher die Fehlerursache beheben, etwa fehlende Quelldaten oder DB-Probleme.
Nach acht erfolglosen Versuchen bleibt eine Zeile als `failed` stehen.

Alternativ im Systemkontext per CLI:

```sh
python -m app.cli suche-neuaufbau --org <id>
python -m app.cli suche-neuaufbau --alle
python -m app.cli suche-neuaufbau --org <id> --quelle objekt
```

`--org` und `--alle` sind Alternativen. `--quelle` erlaubt `objekt`,
`objekt_dokument_seite`, `katplan_element` und `katplan_seite`. Die CLI verarbeitet
synchron und committet je Quelle. Der Index ist aus den Fachquellen rekonstruierbar;
Backups der Originaldaten und Medien bleiben erforderlich. Nach Wiederherstellung
oder Änderungen an Adaptern kann ein Neuaufbau den Index wieder abgleichen.
Telefonnummern werden vor dem Indexieren entfernt. Suchtexte werden nicht geloggt.

## Konfiguration in .env

| Flag | Standard | Wirkung |
| --- | --- | --- |
| `SEARCH_ENABLED` | `true` | Systemweite Freigabe, global aus blendet Suche aus und sperrt Routen |
| `SEARCH_INDEX_WORKER_ENABLED` | `true` | Hintergrundverarbeitung der Warteschlange |
| `SEARCH_MCP_ENABLED` | `true` | Suchwerkzeug über MCP, zusätzlich zum Org-Schalter |
| `SEARCH_SEMANTIC_ENABLED` | `false` | Reserviert; semantische Stufe noch nicht umgesetzt |
| `SEARCH_ABGLEICH_MINUTEN` | `60` | Intervall des zusätzlichen Quellenabgleichs |

Bei Rückständen prüfen: Org-Schalter, Worker-Flag, Leader-Prozess, DB-Verbindung,
ältester Auftrag und Fehleranzeige. Ein deaktivierter Worker verarbeitet keine
Neuaufbauaufträge. Nicht mehr berechtigte Inhalte werden durch die Live-Prüfung schon
vor der nächsten Indexaktualisierung aus Suchtreffern entfernt.

## Laufzeiten messen

Auf dem Testsystem eine UTF-8-Datei mit einer Anfrage je Zeile bereitstellen:

```sh
.venv/bin/python scripts/suche_messung.py --org 1 --user 42 --anfragen /tmp/anfragen.txt --wiederholungen 5
```

`--user` ist die ID eines aktiven Benutzers der Org; dessen Fachrechte gelten weiter.
Das Skript nutzt eine eigene Session im Systemkontext und ruft die reguläre `suche()`
auf. Die Ausgabe enthält nur Anfragenummern, Trefferzahlen und p50/p95/max in ms je
Stufe und insgesamt. Stufenzeiten sind inklusive verschachtelter Aufrufe und dürfen
nicht addiert werden; bei Nachladen werden Aufrufe derselben Stufe je Lauf summiert.
Suchtexte, SQL-Parameter und Exceptiondetails werden nicht ausgegeben. Teilergebnisse
werden gezählt und müssen bei der Auswertung berücksichtigt werden.
