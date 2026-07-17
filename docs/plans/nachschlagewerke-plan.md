# Nachschlagewerke (Gefahrgut, Rettungsdatenblätter, Ausbreitung)

Stand: 2026-07-17 — umgesetzt in 9 PRs. Vorbilder: rescueTABLET (Gefahrgut + Rettungskarten),
Alamos FE2 (Evakuierungsradien + Ausbreitung).

Offlinefähiges Nachschlagewerk für Einsatzkräfte unter dem Präfix `/nachschlagewerke`,
zweistufig schaltbar (SystemSettings `nachschlagewerke_module_enabled` == "true" AND
`OrgSettings.nachschlagewerke_module_enabled`, Muster Objekt/UAS/Gateway).

## Leitplanken (eingehalten)

- Feature-Flag: `app/core/dependencies.py` (`_SYSTEM_FLAG_KEYS`, `request.state.nachschlagewerke_enabled`),
  Guard `require_nachschlagewerke_enabled` in `app/routers/ui_nachschlagewerke.py`.
- Modelle eager in `app/models/__init__.py`; Migrationen `NNNN_*.py` (0164, 0165).
- Gefahrgut-/Rettungskarten-Daten sind **global (kein TenantScoped)** — geteiltes Nachschlagewerk.
- Deutsch (AT), nur ASCII-Quotes in Attributen/JS, HTMX-Swap statt reload, CSRF, DB=UTC.
- Persistente Sync-/Cache-Daten außerhalb Repo: `NACHSCHLAGEWERK_DATA_DIR` (Default `app_storage/nachschlagewerk`).

## PR-Übersicht

- **PR 0** — Grundgerüst: OrgSettings-Flag (Migration 0164), Guard, Landing `/nachschlagewerke/`,
  Admin-Toggles (System + Org), Nav (Desktop/Mobile). `nachschlagewerk_service.py` (Flag-Helfer).
- **PR 1** — Gefahrgut-Suche: `gefahrgut_service.suche()` (UN-Präfix ODER Stoffname-Substring,
  umlaut-tolerant), `eintrag_un()`/`alle_eintraege()`; ERI-Ansicht + ERICard/BAM-Deep-Links.
  `_csv_pfad()` bevorzugt gesyncte Datei vor gebündeltem Seed.
- **PR 2** — Täglicher Sync-Loop `nachschlagewerk_sync.py` (03:00): httpx-Fetch der BAM/ADR-CSV,
  Validierung (>=50 Zeilen), atomarer Ersatz, Cache-Invalidierung. Quelle via
  `NACHSCHLAGEWERK_GEFAHRGUT_URL` (leer = Seed bleibt).
- **PR 3** — Offline: `GET /gefahrgut/index.json` + SW-Cache-Bucket `ec-nachschlagewerk-v1`
  (activate-Whitelist, `/nachschlagewerk-cache/` cache-first, CACHE v5->v6); `nachschlagewerke.js`
  clientseitige Suche über den Index (offline nutzbar, Detail inline).
- **PR 4** — Rettungsdatenblätter Modell `RettungsdatenblattCache` (Migration 0165) +
  `rettungskarten_service.finde_oder_hole()` (Cache-Hit ODER on-demand `httpx.Client`-Einzelabruf,
  PDF-Magic-Byte/Größe, Ablage `rettungskarten/{uuid}/original.pdf`); Deep-Link-Fallback
  (Euro Rescue). Quelle via `NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE` (leer = nur Deep-Links).
- **PR 5** — Rettungskarten-UI + `GET /nachschlagewerk-cache/rettungskarten/{id:int}/original.pdf`
  (FileResponse, unveränderliche URL -> SW cache-first, offline nach 1. Aufruf).
- **PR 6** — Evakuierungsradius: Feature-Typ `gefahrenradius`, `evakuierung_service.zonen()`
  (ERG-2020-generisch: klein 50 / groß 100 / Tank-Brand 100+800 m). JS: je Zone ein
  eigenständiges Kreis-Feature (Persistenz/Sync/Druck geerbt), Werkzeug per Flag gegated.
- **PR 7** — Ausbreitungs-Kegel: Feature-Typ `ausbreitung`, `ausbreitung_service.plume_polygon()`
  (windbezogener Downwind-Kegel als GeoJSON-Polygon; Richtung = wind_from + 180). Endpoint
  `GET /einsatz/{id}/lagefuehrung/ausbreitung.json` (echter Wind aus weather_service).
- **PR 8** — Gaußsches Modell: `ausbreitung_service.gauss_footprint()` (Briggs-rural
  σy/σz je Pasquill-Klasse A-F, Isokonzentrations-Footprint C0=Q/(π·u·σy·σz)); Endpoint-Param
  `modell=gauss` (Quellstärke g/s, Stabilität, Grenzwert mg/m³), Windgeschw. aus weather_service.
- **PR 9** — Diese Doku, Memory, Volllauf (1431 Tests grün).

## Datenquellen (Herkunft & Recht)

| Zweck | Quelle | Modus |
|---|---|---|
| Gefahrgut-Stammdaten | BAM „Datenbank GEFAHRGUT" / ADR-Tabelle A (dl-de/by-2.0) | täglicher Sync, redistributierbar |
| ERI-Karten | CEFIC ERICards | Deep-Link (keine Spiegelung) |
| Evakuierungsabstände | ERG 2020 (generisch) | tabellenbasiert |
| Rettungsdatenblätter | Euro Rescue (ACEA)/Hersteller | on-demand fetch + cache, kein Voll-Spiegel |

Sicherheitsentscheidung: der Gefahrgut-Seed und die Evakuierungs-/Stabilitätstabellen werden
**nicht** handgetippt substanzspezifisch erweitert (Kemler/Klasse/TIH sind einsatzkritisch) —
der vollständige, autoritative Datensatz kommt ausschließlich über den Sync (PR 2).

## Konfiguration

`NACHSCHLAGEWERK_DATA_DIR`, `NACHSCHLAGEWERK_SYNC_ENABLED`, `NACHSCHLAGEWERK_GEFAHRGUT_URL`,
`NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE`, `NACHSCHLAGEWERK_RETTUNGSKARTEN_MAX_BYTES`
(siehe `.env.example`).

## Offene Punkte

- Exakte Download-URL der vollständigen BAM/ADR-Tabelle vor Produktivnutzung setzen
  (`NACHSCHLAGEWERK_GEFAHRGUT_URL`).
- Rettungskarten-Quelle (`..._URL_TEMPLATE`) ist ToS-abhängig — best-effort mit Deep-Link-Fallback,
  keine Bulk-Spiegelung.
