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
| `SEARCH_SEMANTIC_ENABLED` | `false` | Zusätzliche Vektorstufe, siehe Semantische Suche |
| `SEARCH_ABGLEICH_MINUTEN` | `60` | Intervall des zusätzlichen Quellenabgleichs |

Bei Rückständen prüfen: Org-Schalter, Worker-Flag, Leader-Prozess, DB-Verbindung,
ältester Auftrag und Fehleranzeige. Ein deaktivierter Worker verarbeitet keine
Neuaufbauaufträge. Nicht mehr berechtigte Inhalte werden durch die Live-Prüfung schon
vor der nächsten Indexaktualisierung aus Suchtreffern entfernt.

## Semantische Suche

Die optionale Vektorstufe ergänzt exakte Treffer und Volltext um ähnliche Inhalte aus
Objekten und Plänen. Voraussetzungen: **MariaDB ≥11.7** mit `VECTOR`-Unterstützung,
ein erreichbarer Ollama-Dienst und das Modell:

```sh
ollama pull embeddinggemma
```

Die Suche und der Suchindex müssen für die Organisation aktiv sein. Für den Aufbau
muss außerdem der Index-Worker laufen. In `.env` konfigurieren und die Anwendung mit
den neuen Einstellungen neu starten:

| Variable | Standard | Wirkung |
| --- | --- | --- |
| `SEARCH_SEMANTIC_ENABLED` | `false` | Vektorstufe und automatische Vektor-Jobs aktivieren |
| `SEARCH_EMBED_URL` | `http://localhost:11434` | Ollama-Host im eigenen Netz |
| `SEARCH_EMBED_MODEL` | `embeddinggemma` | Embedding-Modell |
| `SEARCH_EMBED_DIM` | `768` | Vektordimension; Matryoshka-Kürzung z. B. auf 512/256 möglich |
| `SEARCH_EMBED_QUERY_TIMEOUT_S` | `2` | Timeout einer Anfrage in Sekunden |
| `SEARCH_EMBED_BATCH_TIMEOUT_S` | `60` | Timeout beim Indexaufbau in Sekunden |
| `SEARCH_EMBED_BATCH` | `16` | Dokumente pro Embedding-Batch |
| `SEARCH_SEMANTIC_MAX_DISTANZ` | `0.6` | Maximale Cosinus-Distanz; kleiner bedeutet strengere Auswahl |

Vektortabelle initialisieren und Backfill für eine Organisation einplanen:

```sh
.venv/bin/python -m app.cli suche-vektor-init
.venv/bin/python -m app.cli suche-vektor-aufbau --org 1
```

Die Tabelle heißt `search_vector_<modell>_<dim>` (z. B.
`search_vector_embeddinggemma_768`) und liegt außerhalb der Alembic-Migrationen.
Bei Modell- oder Dimensionswechsel erneut initialisieren und aufbauen. Der Backfill
plant `vektor`-Outbox-Jobs; der Worker berechnet fehlende/geänderte Chunks. Provider-Ausfälle
führen zu Retry mit Backoff, während die Textindexierung weiterläuft. Nach wiederholten
Provider-Fehlern schützt ein Circuit-Breaker den Suchlauf. Die Volltextsuche bleibt nutzbar.

**Rollback:** `SEARCH_SEMANTIC_ENABLED=false` setzen und Anwendung neu starten.
Vektortabellen können bestehen bleiben. **Datenschutz:** Dokument- und Suchtexte werden
nur an den konfigurierten Ollama-Host gesendet; bei Betrieb im eigenen Netz bleibt der
Text im eigenen Netz. Live-Quellen (Sperren, Wasserstellen, Einsätze, Kontakte) werden
nicht eingebettet. Suchtexte werden nicht geloggt; bestehende Fachrechte gelten weiter.

### Qualität auswerten

Auf dem Testsystem nach abgeschlossenem Backfill:

```sh
.venv/bin/python scripts/suche_eval.py --org 1 --user 42
.venv/bin/python scripts/suche_eval.py --org 1 --user 42 --modi volltext,hybrid,semantisch --limit 10 --max-distanz 0.6 --review-csv /tmp/suche-review.csv
```

Standardmäßig wird `scripts/data/suche_fragen.jsonl` verwendet (`--fragen` überschreibt
den Pfad). `--user` muss ein aktiver Benutzer der Org sein; dessen Fachrechte und
Kontakt-Verzeichnisrechte gelten. Der Modus `volltext` schaltet die Vektorstufe für
diesen Lauf aus; `hybrid` schaltet sie ein. `semantisch` ruft ebenfalls die Hybrid-Suche
auf und filtert deren Ergebnis auf Treffer mit Grund `semantisch`, unter Beibehaltung
der Rangfolge. Diese Sicht enthält nur die semantischen Treffer innerhalb des
Hybrid-Top-N und hat keine eigene KNN-Rangfolge. Ihre Dauer misst den gesamten Hybrid-Aufruf.

Die Semikolon-Ausgabe enthält je Modus/Gruppe und insgesamt Fragenzahl, Recall@N
(Anteil der Fragen mit passendem Treffer), MRR (Mittelwert von 1/Rang; ohne passenden
Treffer 0), Anteile strenger/schwacher Bewertung und p50/p95 in ms (Nearest-Rank).
`Teilweise` zählt Läufe mit ausgefallenen Suchstufen. Je Modus folgen die Nummern
der Fragen mit leerer Trefferliste. Ohne Vektorfähigkeit, erreichbaren Provider oder
aktive Semantikstufe bricht der jeweilige Modus mit Fehlermeldung ab; andere Modi
laufen weiter. Der Prozess liefert bei einem Abbruch Exitcode 1.

Mit `soll_titel` genügt ein Titel, der einen der Teilstrings ohne Beachtung der
Groß-/Kleinschreibung enthält. Ohne Label zählt nur der erwartete Quellentyp
(bei `objekt` auch `objekt_dokument_seite`, bei `katplan_element` auch `katplan_seite`).
Diese schwache Bewertung weist keine inhaltliche Richtigkeit nach. Die Gruppen
der Live-Quellen getrennt beurteilen: Hier ergänzt die Vektorstufe keine Kandidaten.

Die Standardausgabe enthält keine Fragen oder Treffertitel. Nur `--review-csv`
schreibt Fragetext und Top-5 je Frage/Modus mit Quelle, Titel, Fundstelle und Grund;
bei null Treffern bleibt eine Zeile ohne Trefferfelder. Die Distanzspalte bleibt leer,
solange der Treffervertrag keine Vektordistanz liefert (geografische Entfernung ist
keine Vektordistanz). Die Datei dient zum manuellen Nachlabeln. `--max-distanz`
überschreibt den Schwellwert nur während dieses Prozesses. Dimensionen werden über
`.env` mit jeweils eigener Tabelle und Backfill verglichen. Messung und Kalibrierung
auf dem Testsystem stehen noch aus; Produktivfreigabe erst danach.

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
