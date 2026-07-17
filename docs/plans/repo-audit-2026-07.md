# Repository-Audit 2026-07 — Befunde & Maßnahmenplan

Stand: main `f5195bd` (#220), Audit vom 2026-07-17.
Geprüfte Bereiche: Sicherheit, Bugs/Robustheit, Performance, Usability.

## Gesamturteil

Die Codebase ist in ungewöhnlich gutem Zustand: Fail-Closed-Tenant-Listener
(`app/core/tenant.py`), Magic-Byte-Prüfung bei Uploads, timing-sicherer Login
mit Dummy-bcrypt (SEC-10), Lockout, sauberes Double-Submit-CSRF, signierte
Kurzzeit-Token statt genereller API-Flächen, Startup-Validierung der Secrets.
Frühere Security-Durchgänge (SEC-1 … SEC-11) sind im Code dokumentiert.
Es gibt **keine kritischen, sofort ausnutzbaren Lücken** aus statischer Sicht.
Die folgenden Punkte sind priorisierte Verbesserungen, kein Alarmbefund.

---

## A. Sicherheit

### A1 (hoch, Aufwand mittel-groß): CSP erlaubt `unsafe-inline` + `unsafe-eval`
`app/middleware/security_headers.py:27` — bei einer einzigen XSS-Lücke greift
die CSP praktisch nicht. Blocker: **423 `onclick=`-Handler** in Templates plus
Inline-`<script>`-Blöcke. Weg: schrittweise Event-Delegation/`addEventListener`
statt `onclick`, dann Nonce-basierte CSP je Response; `unsafe-eval` prüfen
(vermutlich nur für Alpine nötig → Alpine CSP-Build erwägen).

### A2 (mittel, Aufwand klein): Rate-Limits sind pro Worker, nicht global
slowapi läuft mit In-Memory-Storage. Bei `-w 2+` hat jeder Worker ein eigenes
Kontingent → effektives Limit = Limit × Worker (Login-Brute-Force: 10/min wird
zu 20+/min). `REDIS_URL` existiert bereits für den WS-Bus → slowapi auf
`storage_uri=REDIS_URL` umstellen, wenn gesetzt.

### A3 (mittel, Aufwand klein): `TRUST_PROXY_HEADERS` default `true`
`app/main.py:545` — ohne vorgelagerten Reverse-Proxy ist `X-Forwarded-For`
spoofbar: Rate-Limit-Bypass + gefälschte Audit-IPs. Default auf `false` drehen
oder beim Start warnen, wenn kein Proxy erkennbar ist; Deploy-Doku ergänzen.

### A4 (mittel, Aufwand klein): Device-Session ohne Token-Bezug (SEC-5-Rest)
`app/main.py:427-441` — das Device-Cookie (unbegrenzt gültig) speichert keine
`device_token_id`; Widerruf wirkt nur, wenn der User **gar kein** aktives Gerät
mehr hat. Bei 2 Geräten bleibt ein gestohlenes Cookie gültig.
Fix: `device_token_id` in den Session-Payload aufnehmen und gezielt prüfen
(bestehende Cookies via Fallback weiter akzeptieren, neue binden).

### A5 (niedrig, Aufwand trivial): Nicht-konstante Secret-Vergleiche
- `app/routers/api_import.py:42` `x_import_key != settings.IMPORT_API_KEY`
  → `hmac.compare_digest`.
- `app/middleware/csrf.py:71` `_constant_time_eq` leakt die Länge →
  durch `hmac.compare_digest` ersetzen.

### A6 (niedrig, organisatorisch): `api_import`-Router entfernen
Als TEMPORAER markiert (EUS-Migration). Wenn die Migration abgeschlossen ist:
Datei + `include_router` + `IMPORT_API_KEY` entfernen (steht so im Docstring).

### A7 (Dauerthema): SEC-11-Fläche systematisch testen
Anonyme Endpunkte (Public-Token, QR, PIN, Infoscreen, Wetter-JSON) laufen ohne
Tenant-Filter und müssen selbst scopen. Empfehlung: parametrisierter Test, der
jede öffentliche Route mit einem Token/Objekt der „falschen" Org aufruft und
404/403 erwartet — als Regressionsnetz für künftige Public-Routen.

### A8 (dokumentieren): Tenant-Listener greift nur bei SELECT
`_add_tenant_filter` prüft `is_select` — `UPDATE`/`DELETE`-Statements auf
Tenant-Tabellen sind nicht automatisch gefiltert. Ist heute durch das Muster
„erst laden (gefiltert), dann mutieren" abgedeckt; als Architektur-Regel in
CLAUDE.md festhalten, damit kein direktes `query(...).update()` entsteht.

### A9 (bewusster Tradeoff, dokumentieren): unbefristete Foto-/QR-Signaturen
`sign_fahrt_foto_token` und die QR-Signer sind bewusst nicht zeitlich begrenzt
(DB kontrolliert Gültigkeit bzw. Teams braucht Langlebigkeit). Für die
Fahrt-Foto-URLs wäre ein sehr langes `max_age` (z. B. 1 Jahr) ein günstiger
Kompromiss: geleakte URLs sterben irgendwann, Teams-Verläufe bleiben nutzbar.

---

## B. Performance

### B1 (hoch, strukturell): Sync-DB in `async def`-Routen blockiert den Event-Loop
671 Routen sind `async def`, nutzen aber die synchrone SQLAlchemy-Session —
jeder DB-Roundtrip friert den ganzen Worker ein (alle parallelen Requests,
**alle WebSockets**, alle Background-Loops). Genau die Live-Charakteristik
(Board, Wallboard, GSL) leidet darunter zuerst, sobald eine Query langsam wird.

Pragmatischer Weg (kein Umbau auf async-SQLAlchemy nötig):
1. DB-lastige `async def`-Routen **ohne `await`-Bedarf** auf `def` umstellen —
   FastAPI führt sie dann im Threadpool aus. Kandidaten zuerst: Board-/
   Fragment-/Listen-/Archiv-Routen mit vielen Queries.
2. Routen, die `await` brauchen (Broadcasts, Uploads), lassen den DB-Teil via
   `run_in_threadpool` laufen oder senden den Broadcast nach dem DB-Block.
3. Anyio-Threadpool-Limit (`RunVar` default 40) und DB-Pool (`pool_size=10,
   max_overflow=20`) gemeinsam dimensionieren.

### B2 (hoch, Aufwand klein): Background-Loops blockieren denselben Event-Loop
13 asyncio-Tasks (LIS, DIBOS, Wetter, Retention, Autoclose, Reminder …) machen
synchrone DB-Arbeit direkt im Loop; `weather_alert_dispatch.py:150` macht sogar
einen **synchronen** `httpx.post` (Timeout 10 s) — 10 s Voll-Stillstand des
Workers im Fehlerfall. Fix: DB-Blöcke der Loops in `asyncio.to_thread(...)`
kapseln (Muster existiert schon 16×), den sync `httpx.post` auf
`httpx.AsyncClient` umstellen.

### B3 (mittel, Aufwand klein): Session-Middleware macht DB-Query für jede Anfrage
`app/main.py:397` — auch `/static/*`, `/sw.js`, `/favicon.ico` lösen mit
Session-Cookie einen User-Lookup (+ Rollen) aus. Fix: früh im Middleware-Body
`if path.startswith(("/static/", "/sw.js", "/favicon"))` → direkt durchreichen.

### B4 (mittel, Aufwand klein): Modul-Status = bis zu 6 Queries pro Request
`_resolve_current_org` ruft 6 `_set_*_state`-Helfer; mehrere laden `OrgSettings`
separat, dazu SystemSettings in den `*_effective_enabled`-Helfern. Fix:
**einen** `OrgSettings`-Load pro Request teilen (an `request.state` hängen),
SystemSettings mit kurzem TTL-Cache (30–60 s) versehen. Spart 4–6 Queries auf
jedem einzelnen Seitenaufruf/HTMX-Fragment.

### B5 (mittel, Aufwand klein): CSRF-Middleware puffert Upload-Bodies komplett im RAM
`app/middleware/csrf.py:129-140` — bei jedem unsafe Request wird der gesamte
Body gepuffert, **bevor** der Header-Token geprüft wird. Ein 100-MB-Objekt-PDF
(`OBJEKT_PDF_MAX_BYTES`) liegt damit doppelt im Speicher. Fix: wenn
`X-CSRF-Token`-Header vorhanden ist, Token sofort prüfen und den Body
ungepuffert durchstreamen; nur beim Form-Feld-Fallback puffern. (JS-Uploads
setzen den Header bereits via `csrf.js`.)

### B6 (niedrig): WS-Broadcast seriell
`broadcast.py:_deliver_local` sendet nacheinander; ein hängender Client bremst
die Zustellung an alle anderen im selben Kanal. Fix: `asyncio.gather` mit
`wait_for`-Timeout pro Socket (z. B. 2 s), Fehler → Socket aufräumen.

### B7 (Empfehlung): Query-Sichtbarkeit statt Blindflug
`slow_query`-Logging aktivieren (SQLAlchemy `before/after_cursor_execute`,
Schwelle z. B. 300 ms) + im Sysadmin-Bereich anzeigen. Erst messen, dann
gezielt Indizes/N+1 fixen — bei 200+ Modellen ist das effizienter als ein
statischer Index-Audit.

---

## C. Bugs / Robustheit

### C1: `_bootstrap_admin` schluckt alle Fehler still
`app/main.py:308-310` — `except Exception: db.rollback()` ohne Log. Ein echtes
Seed-Problem (z. B. Migrationsstand) bliebe unsichtbar. Fix: `logger.exception`
ergänzen (Race mit zweitem Worker bleibt abgefangen).

### C2: 328 × `except Exception` — Stichprobe auf stille Schlucker
Die meisten sind bewusst fail-safe (Modul-Status, Wetter). Einmalig
durchsehen, dass jeder Handler mindestens `logger.debug/warning` hat; stille
`pass` ohne Log als Lint-Regel verbieten (`ruff` S110/S112 aktivieren).

### C3: Multipart-CSRF-Parser findet Token nur vor 8-KB-Grenze? — Nein, aber:
Der manuelle Multipart-Scan (`csrf.py:154-179`) findet `_csrf` überall im Body,
scheitert aber, wenn das Feld einen `Content-Type`-Subheader hat oder der
Client `name=_csrf` ohne Anführungszeichen sendet (erlaubt laut RFC). Browser
tun das nicht — Robustheits-, kein Sicherheitsproblem (fail-closed). Mit B5
(Header-first) verschwindet die Fläche fast vollständig.

### C4: Testnetz für Public-Routen (= A7) und Loop-Resilienz
Die Background-Loops fangen Exceptions pro Iteration — gut. Ergänzend: ein
Watchdog-Logging, wenn eine Loop-Iteration länger als das Intervall dauert
(Frühwarnung für B2-Blockaden).

---

## D. Usability

### D1: Barrierefreiheit-Basics (Aufwand klein, hoher Nutzen im Einsatz)
Nur 20 von 294 Templates nutzen `aria-*`. Konkret und billig:
- `aria-label` auf alle Icon-only-Buttons (Board-Aktionen, ✕-Schließen, 📍…),
- sichtbarer `:focus-visible`-Ring im Design-System (Tastatur/Handschuh-Bedienung),
- `aria-live="polite"` auf Toast-Container und Board-Ticker (Screenreader,
  aber auch: verhindert Fokus-Klau),
- Drag&Drop (Sortable) braucht eine Tastatur-Alternative — mindestens
  „nach oben/unten"-Menüpunkte auf Karten.

### D2: Formular-Fehlertoleranz im Einsatzstress
Stichprobe zeigt gute HTMX-Fehler-Toasts. Ergänzen: bei WS-Verbindungsverlust
zeigen Board/Dashboard bereits Live-Indikatoren — dieselbe Anzeige auf den
GSL-Seiten vereinheitlichen (ein gemeinsames Snippet statt je Seite eigene
Logik).

### D3: PWA/Offline-Verhalten
`sw.js`-Cache-Fallen sind dokumentiert (Session-Memory). Empfehlung: beim
Deploy die SW-Version automatisch aus dem Git-Hash generieren statt manueller
`ec-v1`-Bumps — verhindert die „alte UI nach Update"-Klasse von Vorfällen.

### D4: Inline-`onclick` (423×) auch als UX-/Wartbarkeitsthema
Deckungsgleich mit A1 — bei der schrittweisen Umstellung pro Template gleich
`aria-label` (D1) mit erledigen, dann fasst man jede Datei nur einmal an.

---

## Maßnahmenplan (PR-Reihenfolge)

Reihenfolge nach Nutzen/Aufwand; jeder PR unabhängig mergebar, Tests grün.

| PR | Inhalt | Befunde | Aufwand |
|----|--------|---------|---------|
| 1 | Quick-Wins Sicherheit: `compare_digest` (Import-Key, CSRF), `TRUST_PROXY_HEADERS`-Default/-Warnung, `_bootstrap_admin`-Logging | A5, A3, C1 | S |
| 2 | Request-Overhead: Static-Skip in Session-Middleware, Modul-Status auf 1 OrgSettings-Load + SystemSettings-TTL-Cache | B3, B4 | S–M |
| 3 | CSRF Header-first: Body-Streaming statt RAM-Pufferung bei vorhandenem `X-CSRF-Token` | B5, C3 | S |
| 4 | Background-Loops entblocken: DB via `to_thread`, sync `httpx.post` → async; Iterations-Dauer-Warnung | B2, C4 | M |
| 5 | Rate-Limit auf Redis-Storage bei gesetztem `REDIS_URL` | A2 | S |
| 6 | Device-Session an `device_token_id` binden (mit Fallback für Bestandscookies) | A4 | S–M |
| 7 | Event-Loop-Entlastung Routen, Tranche 1: Board-/Fragment-/Archiv-Routen `async def`→`def` bzw. `run_in_threadpool`; Threadpool/DB-Pool dimensionieren | B1 | M–L |
| 8 | WS-Broadcast parallel mit Timeout + Slow-Query-Logging (Messbasis für weitere Tranchen) | B6, B7 | S–M |
| 9 | Public-Routen-Tenant-Testnetz (parametrisierter Cross-Org-Test) + CLAUDE.md-Regel „kein `query().update()` auf Tenant-Tabellen" | A7, A8 | M |
| 10 | A11y-Basics: `aria-label`-Sweep Icon-Buttons, `:focus-visible`, `aria-live`-Toasts, Tastatur-Alternative fürs Board | D1 | M |
| 11+ | CSP-Härtung inkrementell: `onclick`→Delegation pro Template-Gruppe (zusammen mit D1-Sweep), am Ende Nonce-CSP, `unsafe-eval` prüfen (Alpine-CSP-Build) | A1, D4 | L (verteilt) |
| — | Organisatorisch: `api_import` nach Migrationsende entfernen; Fahrt-Foto-Token-`max_age` entscheiden | A6, A9 | S |

**Nicht empfohlen:** Umbau auf async-SQLAlchemy (hohes Risiko, wenig Zusatznutzen
gegenüber PR 7), pauschaler Index-Audit ohne Messdaten (B7 zuerst).
