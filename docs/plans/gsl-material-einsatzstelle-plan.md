# Plan: GSL – An der Einsatzstelle belassenes Material im Geräteverleih

Stand: 2026-10-11

## Ziel

Wenn eine Einheit Material an einer Einsatzstelle zurücklässt (Tauchpumpe im Keller,
Sandsäcke, Absperrmaterial, Notstromaggregat), wird das als Ausleihe im Geräteverleih
der Lage dokumentiert, damit es später wieder eingesammelt wird. Erfassen kann man das an zwei
Stellen:

1. **Einheit** über das Tablet bzw. den GK-/QR-Zugang (`/einheit`), auch offline über die Outbox.
2. **Führung über das Board**, im Stellen-Detail (`incident_major/_site_detail.html`).

Zur Erfassung gehören Positionen, ein Foto als Beweis und optional eine SMS an den Empfänger vor Ort
(Hauseigentümer/Bürger).

## Bestand (Analyse)

- `VerleihAusleihe` (`app/models/verleih.py`) hat bereits `site_id`, `name`, `adresse`,
  `telefon`, `pin`, `sms_ausleih_gesendet`, `erinnerung_geplant_at`, Positionen und Fotos
  (`VerleihFoto`). Eine Ausleihe ohne Benutzer (Tablet/GK) ist nicht vorgesehen
  (`created_by_user_id`, `VerleihFoto.uploaded_by` sind User-FKs).
- `ui_verleih.py::verleih_neu` erledigt Anlage, Journal (SiteLogEntry bzw. Stabsjournal),
  PIN-SMS, Fotos, Broadcast `verleih:changed` und Autodruck (`autoprint_verleih_background`)
  in einem einzigen Route-Handler. Diese Logik ist nicht wiederverwendbar.
- Den SMS-Text liefert `svc.get_sms_ausleih_text` (Org-Vorlage `gsl_verleih_sms_ausleih_text`). Das Senden läuft über
  `sms_service.send_sms`. Erinnerungs-SMS: `services/verleih_erinnerung.py` (greift automatisch,
  sobald `erinnerung_geplant_at` und `telefon` gesetzt sind).
- Tablet: `ui_einheit.py` mit Idempotenz über `fuehre_aktion_aus` (EinheitAktion.client_uuid),
  Rechteprüfung `einheit_darf` + `ZUGANG_AKTIONEN`, `_schreibbar` (Simulation) und `_autor`.
  Die Outbox (`static/js/einheit_outbox.js`) kennt die Typen `status|meldung|foto|ressource_*`.
  Fotos laufen in einer eigenen, parallelen Spur.
- Das Feature-Flag `get_mi_features(db, org_id)["geraeteverleih"]` gilt pro Org.
- Bürgermeldung (`CitizenReport.site_id`) liefert `reporter_name`, `reporter_contact`, `strasse`, `ort`.
  Damit lässt sich der Empfänger vorbelegen.

## Entscheidungen

- **E1 Keine neue Tabelle.** „Material vor Ort“ ist eine normale `VerleihAusleihe` mit
  `site_id` und den neuen Feldern `quelle` und `einheit_id`. Sie erscheint damit automatisch in Liste, Druck,
  Erinnerung, Rückgabe und Autodruck.
- **E2 Neue Spalten (Migration 0275):**
  - `verleih_ausleihe.quelle` String(12) NOT NULL, Default `"verleih"`. Werte: `verleih|board|einheit`.
  - `verleih_ausleihe.einheit_id` Integer FK `lage_einheit.id` ON DELETE SET NULL, nullable:
    die Einheit, der das Material gehört bzw. die es hinterlassen hat.
  - `verleih_ausleihe.created_by_name` String(160), nullable (Autor ohne User, z. B. „QR-Zugang RLF Hard“).
  - `verleih_foto.author_name` String(160), nullable.
- **E3 Empfänger optional.** Vor Ort ist oft niemand anzutreffen. Bleibt `name` leer, speichert der Server
  `"Vor Ort – <Bezeichnung der Stelle>"` (gekürzt auf 200 Zeichen). Die Vorbelegung kommt aus der
  Bürgermeldung, die Adresse aus der Stelle (`strasse hausnr, ort`).
- **E4 Positionen** sind Freitext plus Menge. Optional kann ein Artikel aus dem Verleih-Katalog gewählt werden (`artikel_id`),
  aber nur, wenn die Einheit `is_from_org` ist oder die Erfassung über das Board läuft. Fremdes Material von GK-Einheiten
  wird nie mit dem eigenen Katalog verknüpft.
- **E5 SMS:** Ist eine Telefonnummer angegeben und die Option „SMS an Empfänger“ gesetzt (Default an, wenn eine Nummer da ist), geht
  die Ausleih-SMS raus (Text über `get_sms_ausleih_text`, neue Variable `{stelle}` = Bezeichnung
  der Stelle; die Default-Vorlage bleibt unverändert). Optional „PIN generieren“ wie bisher. Die SMS läuft nach dem
  Commit als BackgroundTask, damit Replays der Outbox nie doppelt senden. Das Ergebnis landet in
  `sms_ausleih_gesendet` und als Chronikeintrag („SMS an Empfänger gesendet/fehlgeschlagen“).
- **E6 Fotos** werden als `VerleihFoto` gespeichert (eine einzige Ablage). Das Stellen-Detail und das Tablet zeigen sie
  über die Ausleihe an.
- **E7 Chronik:** Bei jeder Anlage entsteht ein `SiteLogEntry` mit kind `massnahmen`, Text
  `"Material belassen: 2× Tauchpumpe, 20× Sandsack (Empfänger: …)"`, `einheit_id`
  und Autor (`_autor`). Bei einer Rücknahme: `"Material zurückgenommen: …"`.
- **E8 Rechte:** Board: Rollen wie `verleih_neu` (incident_leader, admin, org_admin, recorder),
  nur bei aktiver Lage. Tablet: neue Aktionen `material` und `material_zurueck` in
  `ZUGANG_AKTIONEN` (GK und QR dürfen beides, analog zu `foto`). `material_zurueck` ist nur für Ausleihen
  derselben Einheit erlaubt. Ist das Feature-Flag `geraeteverleih` der Lage-Org aus, antwortet
  der Server mit 403 `verleih_deaktiviert`, und Board und Tablet blenden die Funktion aus. Die Simulation folgt `_schreibbar`.
- **E9 Sichtbarkeit am Tablet:** `auftrag_detail` liefert `material` für die Stelle. Beim
  GK-Zugang (`quelle == "zugang"`) sind das nur die Einträge der eigenen Einheit (gleiche Regel wie Chronik/Fotos).
  Telefonnummer und PIN gehen nie an das Tablet.
- **E10 Offline:** Die Outbox bekommt die Typen `material`, `material_zurueck` und `material_foto`. `material_foto`
  trägt `payload.material_client_uuid` und wird erst gesendet, wenn der referenzierte
  `material`-Eintrag nicht mehr in der Outbox steht (also bestätigt ist). Der Server löst die Ausleihe über
  `EinheitAktion.client_uuid → entity_id` auf. Ist sie unbekannt, kommt 409 `material_unbekannt`.

## Pakete (je ein PR, nacheinander gegen main)

### P1 – Modell, Migration, Service
- Migration `0275_verleih_material_einsatzstelle.py` (E2). MariaDB-tauglich mit
  `server_default="verleih"`. Downgrade entfernt die Spalten.
- Modell: neue Felder, Relationship `einheit` (viewonly, lazy select).
- Service `app/services/verleih_service.py`:
  - `material_an_einsatzstelle(db, *, lage, site, positionen, name, telefon, adresse, notizen,
    einheit, quelle, user_id, author_name, pin) -> VerleihAusleihe`: validiert, dass die Stelle zur
    Lage gehört, wendet E3/E4 an, ruft `create_ausleihe` auf und schreibt den Chronikeintrag (E7). Kein Commit
    nach außen nötig, `create_ausleihe` committet bereits. Die Funktion darf das übernehmen.
  - `material_zuruecknehmen(db, ausleihe, *, author_name, user_id, einheit_id)`: `return_all` +
    Chronikeintrag.
  - `async sende_material_sms(ausleihe_id, org_id, pin_senden: bool)`: eigene Session, lädt die Ausleihe neu,
    sendet die Ausleih-SMS (+ PIN-Zeile), setzt `sms_ausleih_gesendet`, schreibt den Chronikeintrag.
  - `get_sms_ausleih_text` ersetzt zusätzlich `{stelle}`.
  - `save_verleih_foto(..., author_name=None)`.
- Tests: `tests/test_verleih_material_service.py` (Anlage mit/ohne Name, Chronik, Katalog-
  Regel E4, Rücknahme, SMS-Text mit `{stelle}`, SMS über gemocktes `send_sms`),
  `tests/test_migration_0275_verleih_material.py` nach dem Muster vorhandener Migrations-Tests.

### P2 – Board (Stellen-Detail)
- Route `GET /lage/{id}/stellen/{site_id}/material/neu` liefert das Formular-Partial
  `incident_major/_site_material_form.html` (in `#siteDetailContent` oder als aufklappbarer Bereich).
- Route `POST /lage/{id}/stellen/{site_id}/material` (multipart: Positionen, Empfänger,
  Telefon, `sms_senden`, `pin_senden`, `einheit_id` aus den an der Stelle disponierten Einheiten,
  Fotos). Danach Detail neu rendern, `site:card_changed` und `verleih:changed` broadcasten,
  Autodruck anstoßen.
- Route `POST /lage/{id}/stellen/{site_id}/material/{ausleihe_id}/zurueck`.
- `_site_detail.html`: Abschnitt „Material vor Ort“ (nur bei aktivem Feature) vor „Verlauf“.
  Er listet die Ausleihen der Stelle (offen zuerst) mit Positionen, Einheit, Empfänger,
  Foto-Thumbnails (`/lage/{id}/verleih/{aid}/foto/{fid}/bild`), Status-Chip, Button „Zurückgenommen“
  und Link „Im Verleih öffnen“. Dazu der Button „+ Material belassen“. Die Vorbelegung (Bürgermeldung, Adresse) übernimmt
  die Route.
- `_site_card.html`: Badge „📦 n“ bei offenem Material (Zählung gebündelt im Board-Kontext,
  kein N+1).
- Mobil (≤760px): Formular einspaltig, Kamera-Button wie beim bestehenden Foto-Upload
  (`openCamera`).
- Tests: `tests/test_gsl_material_board.py` (Formular rendert, Anlage inkl. Foto und Chronik,
  Feature aus → 403/kein Abschnitt, fremde Lage → 404, Rücknahme, Badge auf der Karte).

### P3 – Tablet-API
- `ZUGANG_AKTIONEN` += `material`, `material_zurueck`.
- `POST /einheit/api/auftrag/{dispatch_id}/material` (JSON, `fuehre_aktion_aus`, Antwort
  `entity_type="verleih_ausleihe"`). SMS über BackgroundTask nur beim ersten Ausführen.
- `POST /einheit/api/auftrag/{dispatch_id}/material_foto` (multipart, Idempotenz analog
  `foto`, Ausleihe über `material_client_uuid`, nur wenn diese zur selben Einheit und Stelle gehört).
- `POST /einheit/api/auftrag/{dispatch_id}/material_zurueck` (JSON `ausleihe_id`).
- `auftrag_detail`: `material` (E9), Foto-URLs über eine neue Einheiten-Route
  `/einheit/medien/verleih/{foto_id}` (Thumb + Bild, Scoping wie `_medium`).
- `zustand`: `verleih_aktiv` (Feature-Flag) und `material_katalog` (nur für `is_from_org`-Einheiten:
  id, artikel_nr, bezeichnung, verfügbar).
- Broadcasts: `_senden(...)` + `verleih:changed`.
- Tests in `tests/test_einheit_material_api.py`: Anlage, Replay ohne zweite SMS, GK sieht nur
  eigenes Material, `material_zurueck` fremder Einheit → 403, Foto mit unbekannter
  material_client_uuid → 409, Feature aus → 403, Simulation in Echtlage → 403,
  Cross-Org-Zugang (Muster `test_einheit_zugang.py`).

### P4 – Tablet-UI + Outbox
- `einheit_outbox.js`: Typen aus E10. `material_foto` wartet auf seinen `material`-Eintrag,
  URL-Bildung für die neuen Typen. Die Outbox-Liste zeigt sprechende Labels.
- `einheit.html`/`einheit_modus.js`: Button „📦 Material“ im Auftrag (nur bei `verleih_aktiv`),
  Flyout mit Positionszeilen (Freitext + Menge, Katalog-Datalist bei Bedarf), Empfänger,
  Telefon, Checkbox SMS, Kamera (mehrere Fotos), Notiz. Die Liste „Material an dieser Stelle“ hat
  den Button „Zurückgenommen“. Vor `outbox.erfassen` den Zustand per `JSON.parse(JSON.stringify(...))`
  kopieren (DataCloneError-Lehre).
- JS-Tests in `tests/js/einheit_outbox.test.mjs`: Reihenfolge Material → Material-Foto, Foto wird
  nicht gesendet, solange das Material aussteht, Konflikt beim Material blockiert das Foto nicht für immer
  (Foto wird dann ebenfalls `konflikt`).

### P5 – Verleih-Ansicht + Doku
- `verleih/_ausleihe_card.html` und `_ausleihe_detail.html`: Chip „Vor Ort · <Einheit>“ bei
  `quelle != "verleih"` bzw. `einheit_id`. Im Detail: „Erfasst von <created_by_name>“, Foto-Autor.
- `verleih/list.html`: Filter „Vor Ort belassen“ (Ausleihen mit `site_id`).
- Druck (`verleih/druck.html`): Einheit und Stelle anzeigen.
- Handbuch/Doku-Abschnitt ergänzen (falls `docs/` eine GSL-Benutzerdoku hat).

## Prüfung je Paket
`ruff check app/`, `mypy app/ --ignore-missing-imports`, gezielte pytest-Dateien,
`npm run test:js` bei P4. Die volle Suite läuft nur in der CI. Der Wächtertest `test_board_kein_reload_static`
muss grün bleiben (es kommt kein neuer Broadcast-Typ dazu).

## Offen / später
- Gerätetest am Tablet und auf dem Handy, echte SMS über das Gateway.
- Optional: MCP-Tool für Material vor Ort.
