# Plan: Einsatzinfo an Objektkontakte (Mail / SMS), je Objekt konfigurierbar

**Ziel:** Wenn ein Einsatz mit einem Objekt verknüpft ist, sollen die am Objekt
hinterlegten Kontakte automatisch eine Einsatzinfo per **E-Mail** und/oder **SMS**
erhalten. Konfiguration **je Objekt** (Ein/Aus, Vorlage, Übungs-/Stichwortfilter) und
**je Kontakt** (welcher Kanal, welche Nummer).

Stand der Recherche: 2026-08-26, `main` @ `aed5643`, Alembic-Head `0222`.

---

## 0. Grundsatzentscheidungen (bitte so umsetzen)

| Frage | Entscheidung | Begründung |
|---|---|---|
| Auslöser | Nur `ObjektEinsatz.status == "bestaetigt"` | Geo-Treffer sind immer nur `vorschlag` (`objekt_matching_service.py:8`) und können das falsche Objekt sein — eine Fehlmeldung an einen externen Betrieb wäre gravierend. |
| Default | Alles **aus** (`kontakt_info_enabled=False`, beide Kontakt-Kanäle `False`) | Opt-in, wie `einsatzinfo_sms_enabled` (`master.py:519`). Kein Bestandsobjekt darf nach der Migration plötzlich senden. |
| Übungen | Eigener Schalter je Objekt, Default **aus** | Muster `einsatzinfo_sms_send_exercise` (`master.py:524`). |
| `{link}`-Platzhalter | **Gibt es nicht** | Der No-Login-Link `/alarm/{alarm_token}` (`incident_notify.py:154`) ist FF-intern und darf nie an externe Objektkontakte gehen. |
| `{meldung}` | Platzhalter existiert, steht aber **nicht in der Default-Vorlage** | Alarmtext kann personenbezogene Melderdaten enthalten (DSGVO). Im UI als Warnhinweis kennzeichnen. |
| Doppelversand | Verhindert durch Log-Tabelle mit `UNIQUE(incident_id, objekt_kontakt_id, kanal)` | Matching läuft mehrfach (Erst-Match, Geo-Nachlauf nach Geocoding, DIBOS-BMA-Nachtrag, manuelles Bestätigen) — alle Einstiegspunkte rufen denselben idempotenten Dispatcher. |
| Fehlversuche | Bleiben als Zeile mit `status="fehler"` stehen und werden bei einem erneuten Lauf **aktualisiert**, nicht dupliziert | Erlaubt manuellen Retry über die Board-Schaltfläche. |

---

## 1. Datenmodell

### 1.1 `app/models/objekt.py` — neue Spalten an `Objekt` (Klasse ab Zeile 149)

Direkt nach `anfahrtsweg` (Zeile 178) einfügen:

```python
    # ── Einsatzinfo an Objektkontakte (Mail/SMS) ──────────────────────────────
    # Master-Schalter je Objekt; ohne ihn wird NIE an Objektkontakte gesendet.
    kontakt_info_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # True = auch bei Uebungseinsaetzen senden (Praefix [UEBUNG]/[ÜBUNG])
    kontakt_info_uebung: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Kommagetrennte Alarmtyp-Codes ("B1,B2,B3"); leer/NULL = alle Stichworte
    kontakt_info_stichworte: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Objektspezifischer Mail-Betreff; NULL -> OrgSettings -> systemweiter Default
    kontakt_info_betreff: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Objektspezifischer Text (Mail-Body + SMS); NULL -> OrgSettings -> systemweiter Default
    kontakt_info_template: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**Pflicht:** die fünf Feldnamen zusätzlich in `OBJEKT_KOPIERBARE_FELDER`
(`app/models/objekt.py:67-70`) eintragen — sonst gehen sie beim Arbeitskopie-Zyklus
(`erstelle_arbeitskopie` / `uebernimm_arbeitskopie`, `objekt_service.py:192/370`) verloren.

### 1.2 `app/models/objekt.py` — neue Spalten an `ObjektKontakt` (Klasse ab Zeile 493)

Nach `erreichbarkeit` (Zeile 520) einfügen:

```python
    # Einsatzinfo-Kanaele je Kontakt (siehe objekt_kontakt_notify.py)
    benachrichtigung_mail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    benachrichtigung_sms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Welche der Nummern aus telefone_json fuer SMS genutzt wird; NULL = erste Nummer
    benachrichtigung_telefon: Mapped[str | None] = mapped_column(String(30), nullable=True)
```

Zwei Dinge sind dadurch **automatisch** korrekt und dürfen nicht "repariert" werden:
- `_kopiere_kindzeile()` (`objekt_service.py:172`) kopiert generisch alle Column-Attrs →
  Arbeitskopie überträgt die Flags ohne Codeänderung.
- Der BMA-Import überschreibt nur die Felder aus `_kontakt_felder()`
  (`app/services/bma_import/bma_sync.py:69-76`) → die Flags bleiben bei jedem Sync stehen.
  **Bitte per Test absichern** (siehe §7).

### 1.3 Neue Tabelle `objekt_kontakt_benachrichtigung`

Ans Ende von `app/models/objekt.py` (nach `ObjektKartenObjekt`), Muster:
`AtemschutzPruefBenachrichtigung`.

```python
OBJEKT_INFO_KANAELE = {"mail": "E-Mail", "sms": "SMS"}
OBJEKT_INFO_GESENDET = "gesendet"
OBJEKT_INFO_FEHLER = "fehler"


class ObjektKontaktBenachrichtigung(TenantScoped, Base):
    """Protokoll + Idempotenzschutz je (Einsatz, Kontakt, Kanal)."""
    __tablename__ = "objekt_kontakt_benachrichtigung"
    __table_args__ = (
        UniqueConstraint("incident_id", "objekt_kontakt_id", "kanal",
                         name="uq_objekt_kontakt_benachrichtigung"),
        Index("ix_okb_org_incident", "org_id", "incident_id"),
        Index("ix_okb_org_objekt", "org_id", "objekt_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False)
    # SET NULL: der Kontakt darf spaeter geloescht werden, das Protokoll bleibt
    objekt_kontakt_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("objekt_kontakt.id", ondelete="SET NULL"), nullable=True)
    kanal: Mapped[str] = mapped_column(String(10), nullable=False)
    kontakt_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    empfaenger: Mapped[str] = mapped_column(String(200), nullable=False)  # Mail o. Nummer
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OBJEKT_INFO_GESENDET)
    fehlertext: Mapped[str | None] = mapped_column(String(500), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    gesendet_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    ausgeloest_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
```

Registrieren:
- `app/models/__init__.py` — Import + `__all__` (analog Zeile 123 / 230).
- `app/core/tenant.py` — Tabellenname in `_TENANT_TABLE_NAMES` neben `"objekt_kontakt"` (Zeile 123).
- `app/services/org_export_service.py:81-85` — in die Gruppe `"objekte"` aufnehmen.

### 1.4 `app/models/master.py` — Org-Defaults an `OrgSettings`

Neben `objekt_ki_klassifikation_enabled` (Zeile 459):

```python
    # Org-Standardvorlage fuer die Einsatzinfo an Objektkontakte (NULL -> System-Default)
    objekt_kontakt_info_betreff: Mapped[str | None] = mapped_column(String(200), nullable=True)
    objekt_kontakt_info_template: Mapped[str | None] = mapped_column(Text, nullable=True)
```

### 1.5 Migration `alembic/versions/0223_objekt_kontakt_einsatzinfo.py`

`revision = "0223"`, `down_revision = "0222"`. Stil wie `0222_gsl_live_push.py`
(schlichte `op.add_column`, kein `batch_alter_table`).

- 5 × `op.add_column("objekt", ...)` — Booleans `nullable=False, server_default="0"`,
  danach mit `op.alter_column(..., server_default=None)` wieder entfernen (MySQL/SQLite).
- 3 × `op.add_column("objekt_kontakt", ...)` — dito.
- 2 × `op.add_column("org_settings", ...)`.
- `op.create_table("objekt_kontakt_benachrichtigung", ...)` inkl. der beiden Indizes und
  des Unique-Constraints.
- `downgrade()` spiegelbildlich.

---

## 2. Service `app/services/objekt_kontakt_notify.py` (neu)

Kopfkommentar analog `sms_dispatch_service.py` / `incident_notify.py`: eigener
Einstiegspunkt, wird **immer erst nach dem Commit** der Verknüpfung aufgerufen,
idempotent, best effort (wirft nie).

```python
_DEFAULT_BETREFF = "Einsatz der {feuerwehr} bei {objekt}"
_DEFAULT_TEMPLATE = (
    "Am {datum} um {zeit} Uhr wurde die {feuerwehr} zu einem Einsatz "
    "({stichwort}) bei {objekt}, {adresse}, alarmiert. "
    "Bitte wenden Sie sich vor Ort an die Einsatzleitung."
)
```

Platzhalter (Rendering über das bestehende tolerante
`sms_dispatch_service.render_template()`, `sms_dispatch_service.py:52` — unbekannte Keys
werden zu `""`):
`{objekt}` `{objektnummer}` `{vulgoname}` `{stichwort}` `{adresse}` `{ort}` `{meldung}`
`{einsatzgrund}` `{datum}` `{zeit}` `{feuerwehr}` `{kontakt}` `{leitstellennummer}`.
Lokalzeit für `{datum}`/`{zeit}` über `FireDept.timezone` mit Fallback `Europe/Vienna` —
exakt das Muster aus `sms_dispatch_service.py:371-379`.

Funktionen:

```python
def default_kontakt_info_betreff() -> str
def default_kontakt_info_template() -> str
def loese_betreff(objekt, org_settings) -> str      # Objekt -> Org -> System
def loese_template(objekt, org_settings) -> str     # Objekt -> Org -> System
def stichwort_erlaubt(objekt, alarm_type_code) -> bool
def sms_nummer(kontakt) -> str | None               # benachrichtigung_telefon oder telefone[0]
def sammle_ziele(objekt) -> list[tuple[ObjektKontakt, str, str]]   # (kontakt, kanal, empfaenger)
def baue_kontext(db, incident, objekt, kontakt, org) -> dict
async def dispatch_objekt_einsatzinfo(
    incident_id: int, *, objekt_ids: list[int] | None = None,
    force: bool = False, triggered_by_user_id: int | None = None,
) -> dict
```

`dispatch_objekt_einsatzinfo` im Detail:

1. Eigene Session: `db = SessionLocal(); set_tenant_context(db, None)` — wie
   `dispatch_einsatzinfo` (`sms_dispatch_service.py:337-338`). **Achtung CLAUDE.md:** ohne
   Tenant-Filter → jede Query explizit `.filter(... .org_id == org_id)` bzw.
   `incident_id`-gebunden; **keine** Bulk-`update()`/`delete()`.
2. `incident = db.get(Incident, incident_id)`; ohne Incident oder ohne
   `primary_org_id` → return.
3. Modul-Gate: `objekt_service.objekt_effective_enabled(org_id, db)` → sonst return
   (Muster `objekt_matching_service.py:333-335`).
4. Verknüpfungen laden:
   `ObjektEinsatz` mit `incident_id == incident_id`, `status == OBJEKT_EINSATZ_BESTAETIGT`,
   `org_id == org_id`, `selectinload(ObjektEinsatz.objekt).selectinload(Objekt.kontakte)`;
   optional auf `objekt_ids` einschränken.
5. Je Objekt Gates (bei `force=True` werden **nur** 6a–6c übersprungen, nie 6d):
   - a) `objekt.kontakt_info_enabled`
   - b) `incident.is_exercise and not objekt.kontakt_info_uebung` → skip
   - c) `stichwort_erlaubt(objekt, incident.alarm_type_code)`
   - d) Kanalflag + gültiger Empfänger am Kontakt (Mail über
     `mail_service._looks_like_email`, `mail_service.py:314`)
6. Idempotenz: bestehende `ObjektKontaktBenachrichtigung` für
   `(incident_id, objekt_kontakt_id, kanal)` laden.
   `status == "gesendet"` → überspringen. `status == "fehler"` → Zeile wiederverwenden
   (Retry), keine neue anlegen.
7. Versand:
   - **Mail:** `smtp_cfg = _org_smtp_cfg(db, org_id) or get_smtp_cfg(db)`,
     `msg = _build_message(to=..., subject=..., body_txt=..., body_html="<pre>…</pre>", smtp_cfg=smtp_cfg)`,
     `await deliver(db, org_id, msg, smtp_cfg)` — 1:1 das Muster aus
     `atemschutz_pruefung_service.py:68-79`. Ein Empfänger pro Nachricht (kein
     Sammel-To/BCC — Objektkontakte dürfen sich gegenseitig nicht sehen).
   - **SMS:** einmalig `ctx = resolve_sms_config(org_id, db)` und
     `sms_available(org_id, db)`; ist kein Provider da, SMS-Ziele komplett überspringen
     **ohne** Log-Zeile (damit ein späterer Lauf noch senden kann) und einmal
     `logger.debug` — analog `sms_dispatch_service.py:333-336`.
     Sonst `await send_sms(org_id, nummer, text, ctx=ctx)`.
     SMS-Text: Präfix `"[UEBUNG] "` bei `is_exercise`, bewusst ohne Umlaute
     (SMS-Zeichensatz, wie `_DEFAULT_GSL_ALARM_TEXT`, `sms_dispatch_service.py:41`).
     Mail-Betreff bei Übung mit `"[ÜBUNG] "`.
8. Je Ziel Log-Zeile schreiben/aktualisieren (`status`, `fehlertext[:500]`, `text`,
   `gesendet_am` = naive UTC, `ausgeloest_von_id`), `db.commit()` nach jedem Ziel
   (Zwischen-Commits wie `sms_dispatch_service.py:425-437`, damit ein Fehler am Ende nicht
   das ganze Protokoll verliert).
9. `write_audit(db, "objekt.kontakt_info_gesendet", org_id=..., user_id=triggered_by_user_id,
   incident_id=..., entity_type="objekt", entity_id=objekt.id,
   payload={"mail": n, "sms": n, "fehler": n, "force": force})`.
10. Alles in `try/except Exception: logger.exception(...)`, `finally: db.close()`.
    Rückgabe `{"gesendet": int, "fehler": int, "uebersprungen": int}` (für Tests/Route).

---

## 3. Auslöser (alle rufen denselben Dispatcher)

1. **Auto-Matching** — `app/services/objekt_matching_service.py:324`
   `match_incident_background()`: nach `db.commit()` **und** den bestehenden Broadcasts,
   und zwar **außerhalb** des `if neu:`-Blocks (ein reiner Geo-Nachlauf kann eine schon
   vorher bestätigte Verknüpfung betreffen):
   ```python
   from app.services.objekt_kontakt_notify import dispatch_objekt_einsatzinfo
   await dispatch_objekt_einsatzinfo(incident_id)
   ```
   Der eigene `try/except` bleibt außen herum bestehen.

2. **DIBOS-BMA-Nachtrag** — `app/services/dibos/dibos_enrich.py:646`
   `_match_objekt_by_dibos_bma()` läuft synchron im Worker-Thread, der Commit passiert
   erst später (`dibos_enrich.py:695`). Daher: die betroffenen `incident.id` in einer
   Liste `objekt_match_ids` sammeln, im Rückgabedict von `enrich_events_for_org()`
   (`dibos_enrich.py:701-706`) mitgeben und in `enrich_and_broadcast()` **nach**
   `await asyncio.to_thread(...)` (ab `dibos_enrich.py:735`) je ID
   `await dispatch_objekt_einsatzinfo(incident_id)` aufrufen, in `try/except` gekapselt.
   (Nicht `run_coroutine_threadsafe` verwenden — der saubere Punkt liegt hinter dem Commit.)

3. **Manuelles Verknüpfen** — `app/routers/ui_objekt.py:2476`
   `einsatz_manuell_verknuepfen`: Parameter `background_tasks: BackgroundTasks` ergänzen
   und nach `db.commit()`
   `background_tasks.add_task(dispatch_objekt_einsatzinfo, incident_id, triggered_by_user_id=user.id)`.

4. **Vorschlag bestätigen** — `app/routers/ui_objekt.py:2524`
   `einsatz_match_bestaetigen`: identisch (BackgroundTasks funktioniert auch bei
   `def`-Routen), eingeschränkt auf das betroffene Objekt:
   `objekt_ids=[verknuepfung.objekt_id]`.

5. **Manuell auslösen (neue Route)** — `app/routers/ui_objekt.py`, direkt nach
   `einsatz_match_bestaetigen`:
   ```
   POST /objekte/einsatz-panel/{incident_id}/{verknuepfung_id}/benachrichtigen
   ```
   Rollen `_MATCH_ROLLEN` (`ui_objekt.py:2397`), `require_objekt_enabled`,
   `background_tasks.add_task(dispatch_objekt_einsatzinfo, incident_id,
   objekt_ids=[verknuepfung.objekt_id], force=True, triggered_by_user_id=user.id)`,
   `write_audit(db, "objekt.kontakt_info_manuell", ...)`, Antwort wie die Nachbarrouten:
   `templates.TemplateResponse(request, _panel_template(view), _panel_context(...))`.
   `force=True` ignoriert Objekt-Schalter/Übung/Stichwort (bewusster Handgriff der
   Einsatzleitung) und wiederholt Fehlversuche — die Kanalflags am Kontakt gelten weiter.

---

## 4. UI — Objektpflege

### 4.1 Kanäle je Kontakt (`app/templates/objekt/_kontakte.html`)

- **Anzeigezeile:** hinter Name/Erreichbarkeit Badges rendern, wenn aktiv:
  `✉️ Einsatzinfo` bzw. `💬 SMS`.
- **Bearbeiten- und Neu-Formular:** Das Grid ist heute 6-spaltig
  (`grid-template-columns:1fr 1.2fr 1.2fr 1fr 1fr auto`). Um es lesbar zu halten: die
  bestehende Zeile so lassen und darunter eine zweite Zeile mit
  `grid-column:1 / -1` einziehen, die enthält:
  - `<input type="checkbox" name="benachrichtigung_mail" value="1">` „Einsatzinfo per E-Mail"
  - `<input type="checkbox" name="benachrichtigung_sms" value="1">` „Einsatzinfo per SMS"
  - `<input type="text" name="benachrichtigung_telefon" maxlength="30">`
    „SMS an Nummer (leer = erste Nummer)"
- **CLAUDE.md-Pflicht:** ausschließlich gerade ASCII-Anführungszeichen in Attributen.
  CSRF-Feld `_csrf` ist in beiden Formularen bereits vorhanden — beibehalten.

### 4.2 Routen `kontakt_neu` / `kontakt_speichern` (`ui_objekt.py:1582` / `:1621`)

Drei neue `Form`-Parameter (`benachrichtigung_mail: str = Form("")`,
`benachrichtigung_sms: str = Form("")`, `benachrichtigung_telefon: str = Form("")`),
Auswertung mit `in ("1", "true", "on")` (Muster `ui_settings.py:486`).
In `kontakt_speichern` in das bestehende `daten`-Dict aufnehmen, damit der feldgenaue
`write_objekt_change`-Diff (Zeile 1652-1660) sie automatisch protokolliert.
Bei `benachrichtigung_sms=True` ohne verwertbare Nummer: kein Fehler, aber im Partial
einen sichtbaren Hinweis („SMS aktiv, aber keine Telefonnummer hinterlegt").

### 4.3 Neuer Abschnitt „Benachrichtigung" am Objekt

- Template `app/templates/objekt/_benachrichtigung.html` (Muster `_wohnanlage.html`:
  Anzeige + Alpine-`edit`-Formular, HTMX-Target `#abschnitt-benachrichtigung`).
  Inhalt:
  - Checkbox „Objektkontakte bei einem Einsatz auf diesem Objekt benachrichtigen"
  - Checkbox „Auch bei Übungen senden"
  - Text „Nur bei Stichworten (kommagetrennt, leer = alle)"
  - Text „Mail-Betreff (leer = Standard)" + Textarea „Nachrichtentext (leer = Standard)"
  - Platzhalterlegende + gerenderte **Vorschau** mit Beispielwerten
  - Liste der Empfänger, die aktuell greifen würden (Name + Kanal + Ziel), mit Hinweis
    wenn `kontakt_info_enabled` an ist, aber kein Kontakt einen Kanal aktiviert hat
  - Dauerhafter DSGVO-Hinweis: „Der Platzhalter `{meldung}` gibt den Alarmtext
    unverändert an externe Empfänger weiter und kann Melderdaten enthalten."
- Routen in `ui_objekt.py` neben den Kontakt-Routen:
  `GET /{objekt_id}/benachrichtigung` (Rollen `_LESE_ROLLEN`) und
  `POST /{objekt_id}/benachrichtigung` (Rolle `objekt_verwalter`), beide mit
  `require_objekt_enabled`, Speichern über `aktualisiere_felder(db, objekt, daten,
  bereich="benachrichtigung", user_id=user.id)` (`objekt_service.py:112`), damit das
  Änderungsprotokoll greift; Antwort = das Partial mit `_detail_context(...)`.
- `app/templates/objekt/detail.html`: Nav-Eintrag nach Zeile 118
  (`<a href="#abschnitt-benachrichtigung">Benachrichtigung</a>`) und die Section
  nach dem Kontakte-Block (Zeile 146-149) im gleichen Muster
  (`hx-get="/objekte/{{ objekt.id }}/benachrichtigung" hx-trigger="load" hx-swap="innerHTML"`).

### 4.4 Org-Standardvorlage (`app/templates/admin/settings.html` + `ui_settings.py`)

Im bestehenden `{% if objekt_sys_enabled %}`-Block (settings.html ab Zeile 551, neben
Geo-Radius und KI-Klassifikation) zwei Felder ergänzen:
`objekt_kontakt_info_betreff_raw`, `objekt_kontakt_info_template_raw` (leer = System-Default).
Auswertung in `ui_settings.py` im Objekt-Zweig (bei Zeile 478-486);
leerer String → `None` speichern.

---

## 5. UI — Einsatz/Board

`_panel_context()` (`ui_objekt.py:2400`) um die Protokollzeilen erweitern: je
`ObjektEinsatz` die zugehörigen `ObjektKontaktBenachrichtigung` des Einsatzes
(gefiltert auf `incident_id` **und** `objekt_id`) zählen und als
`benachrichtigungen: dict[int, dict]` (`{objekt_id: {"gesendet": n, "fehler": n}}`) in den
Context legen.

In **beiden** Templates, die diesen Context nutzen
(`incident/_objekt_panel.html` und `incident/_ei_objekt_section.html`, siehe
`_panel_template()` `ui_objekt.py:2451`), pro bestätigter Verknüpfung ergänzen:
- Statuszeile: `✉️ 2 benachrichtigt` bzw. `⚠️ 1 Fehler` (nichts anzeigen, wenn 0/0 und
  das Objekt keine Benachrichtigung konfiguriert hat);
- bei `darf_verknuepfen` ein Button „Kontakte benachrichtigen" (HTMX-POST auf die Route
  aus §3.5, `hx-target="#objekt-panel" hx-swap="outerHTML"` im Sidebar-Template bzw. das
  Target, das `_ei_objekt_section.html` schon für Bestätigen/Lösen verwendet, dazu
  `hx-confirm="Einsatzinfo jetzt an die Objektkontakte senden?"`).
Kein `location.reload()` (CLAUDE.md).

*Optional, nur wenn ohne Mehraufwand machbar:* in `objekt/_einsaetze.html` je
Einsatzzeile die Anzahl der Benachrichtigungen mit anzeigen.

---

## 6. Was ausdrücklich NICHT gebaut wird

- Kein Versand bei `status="vorschlag"`.
- Keine Entwarnungs-/Abschlussmail bei Einsatzende (eigenes Thema).
- Keine Teams-/Push-Kanäle für Objektkontakte.
- Kein Sammelversand an mehrere Adressen in einer Mail.
- Kein `{link}` auf `/alarm/{token}`.

---

## 7. Tests — `tests/test_objekt_kontakt_benachrichtigung.py` (neu)

Fixture-Muster: In-Memory-SQLite + `@compiles(BigInteger, "sqlite")` +
`set_tenant_context(db, None)` wie in `tests/test_objekt_einsatz_verknuepfung.py:14-60`.
`deliver`, `send_sms` und `sms_available` per `monkeypatch` ersetzen (kein echter Versand),
Aufrufe in einer Liste mitschreiben; `asyncio.run(...)` für den Dispatcher.

1. `render_template` mit unbekanntem Platzhalter → leerer String, kein `KeyError`.
2. Vorlagen-Kaskade: Objekt-Vorlage schlägt Org-Vorlage schlägt System-Default.
3. `kontakt_info_enabled=False` → kein Versand, keine Log-Zeile.
4. `is_exercise=True` + `kontakt_info_uebung=False` → kein Versand;
   mit `True` → Versand, Mail-Betreff beginnt mit `[ÜBUNG]`, SMS-Text mit `[UEBUNG]`.
5. Verknüpfung mit `status="vorschlag"` → kein Versand; nach `bestaetigt` → Versand.
6. `kontakt_info_stichworte="B1,B2"` filtert `T1` weg, lässt `B2` durch (case-insensitiv,
   Leerzeichen tolerant).
7. Kontakt ohne `benachrichtigung_mail` / ohne gültige E-Mail → keine Mail-Zeile.
8. `benachrichtigung_telefon` gewinnt über `telefone[0]`; ohne beides → keine SMS-Zeile.
9. Mail-Pfad: genau ein `deliver`-Aufruf, Empfänger/Betreff/Text korrekt, Log-Zeile
   `status="gesendet"`.
10. SMS-Pfad analog über `send_sms`.
11. **Idempotenz:** Dispatcher zweimal aufrufen → `deliver` genau einmal aufgerufen,
    genau eine Log-Zeile.
12. **Retry:** `deliver` wirft → Log-Zeile `status="fehler"`; zweiter Lauf mit
    `force=True` und funktionierendem `deliver` → dieselbe Zeile wird auf `"gesendet"`
    aktualisiert (kein Duplikat, `UNIQUE` hält).
13. `sms_available=False` → keine SMS-Log-Zeile (nicht `"fehler"`), Mail läuft trotzdem.
14. **Tenant-Isolation:** Objekt+Kontakt in Org B, Einsatz in Org A → kein Versand an B.
15. **Arbeitskopie:** `erstelle_arbeitskopie` → `uebernimm_arbeitskopie` erhält die fünf
    `Objekt`-Felder und die drei `ObjektKontakt`-Flags (ergänzend zu
    `tests/test_objekt_arbeitskopie.py`).
16. **BMA-Import:** `_sync_kontakte` auf einen Kontakt mit gesetzten Flags → Flags
    unverändert (ergänzend in `tests/test_objekt_pr*.py`-Stil oder hier).
17. **Routen:** `GET/POST /objekte/{id}/benachrichtigung` — Leser darf lesen, nicht
    speichern (403); `objekt_verwalter` darf beides; Speichern schreibt einen
    `ObjektChange`-Eintrag. TestClient-Muster aus `tests/conftest.py`.

Ausführen: `.venv/bin/python -m pytest tests/ -q` (mindestens die neuen Dateien plus
`tests/test_objekt_*.py`, `tests/test_sms*.py`, `tests/test_public_tenant_isolation.py`).

---

## 8. Reihenfolge der Umsetzung

1. Modelle + Migration 0223 + Registrierungen (tenant.py, models/__init__.py, org_export).
2. Service `objekt_kontakt_notify.py` + Tests 1–14 (rein serverseitig, ohne UI).
3. Auslöser 1–4 verdrahten + Tests.
4. UI Objektpflege (§4) + Routentests.
5. UI Einsatz/Board (§5) + manuelle Route (§3.5).
6. `CHANGELOG.md`: neue Zeile in der Highlights-Tabelle (CalVer, heutiges Datum).

## 9. Abschluss-Check

- [ ] Keine typografischen Anführungszeichen in Templates/Attributen
- [ ] Alle POST-Formulare mit `_csrf`
- [ ] Kein `location.reload()`, HTMX-Teilupdates
- [ ] Mobile (≤760px) für die neue Objekt-Section und die Board-Buttons geprüft
- [ ] Keine Bulk-`update()`/`delete()` auf Tenant-Tabellen
- [ ] Datetimes: DB naiv UTC, Anzeige über `|local_datetime`
- [ ] `alembic upgrade head` läuft (SQLite-Testlauf reicht)
