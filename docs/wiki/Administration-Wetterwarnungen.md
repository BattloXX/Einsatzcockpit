# Wetterwarnungen & Benachrichtigungen

← [Zurück zur Startseite](Home)

> URL: `/admin/settings/wetter#warnungen`  
> Zugänglich für: `org_admin`, `admin`, `system_admin`

Das Wetterwarnung-Modul wertet alle **5 Minuten** automatisch die aktuellen Wetterdaten aus und sendet bei Überschreiten konfigurierter Schwellwerte eine Benachrichtigung per **E-Mail** und/oder **MS-Teams-Webhook**. Jede Warnregel ist einzeln aktivierbar und in den Schwellwerten editierbar.

---

## Wie funktioniert es?

```
Quellen                Loop (alle 5 min)          Ausgabe
──────────────────────────────────────────────────────────
Lokale Station    ──►  build_weather_picture()  ──► E-Mail
Nowcast (15 min)  ──►  evaluate_rule() × 11    ──► MS Teams
Forecast (+6/12/24h)──►  state_machine()
Amtl. Warnungen   ──►  dispatch_alert()
```

**Zweistufig:** Vorwarnung (aus Prognose) → Akut (bei Eintritt). Eskalation wird immer sofort zugestellt, auch wenn der Cooldown läuft.

**Hysterese:** Ein akuter Alarm wird erst nach 2 aufeinanderfolgenden Zyklen (~10 min) ohne Überschreitung wieder beendet — verhindert Flattern bei Böen.

**Dedup:** Amtliche Warnungen werden per Hash (Typ + Gültigkeit + Stufe) dedupliziert; die gleiche Warnung wird nur einmal weitergeleitet.

---

## Einrichtung

### 1. Empfänger konfigurieren

| Feld | Beschreibung |
|------|-------------|
| **E-Mail-Verteiler** | Eine oder mehrere Adressen (kommagetrennt), z.B. `einsatzleitung@feuerwehr.at` |
| **MS-Teams-Webhook-URL** | Incoming-Webhook aus Teams → Kanal → Connectors → „Incoming Webhook" |
| **Bodensee-Temp. Override** | Manuelle Seewassertemperatur für die Lake-Effekt-Regel (gültig 14 Tage). Leer = automatischer Klimatologie-Fallback. |

Mit **Test-Mail senden** / **Test-Teams-Card** kann der Versand sofort geprüft werden.

### 2. Regeln aktivieren

Jede Regel kann einzeln per Toggle aktiviert werden. Standardmäßig sind alle Regeln **deaktiviert**. Empfohlene Startkonfiguration für Wolfurt:

| Regel | Aktivieren? | Hinweis |
|-------|-------------|---------|
| Sturm | ✓ | bei exponierten Gebäuden/Standorten |
| Starkregen | ✓ | immer sinnvoll |
| Schneefall | ✓ | Winter |
| Glatteis | ✓ | Herbst–Frühling |
| Gewitter | ✓ | immer sinnvoll |
| Lake-Effekt | ✓ | Wolfurt/Rheintal besonders betroffen |
| Amtlich | ✓ | Relay aller ZAMG-Warnungen ab Level 2 |
| Föhn | ✓ | typisch Vorarlberg (Brandgefahr, Sturm) |
| Waldbrand-Bereitschaft | je nach Saison | Sommer/Trockenheit |
| Tauwetter | ✓ | Winter/Frühling |
| Downburst | ✓ | bei Level 3+ amtliche Warnung |

### 3. Schwellwerte anpassen

Die Schwellwerte werden als JSON gespeichert. Alle Werte sind in **m/s** (Wind/Böe), **mm/h** (Regen), **°C** (Temperatur), **%** (Feuchte) angegeben.

---

## Regelkatalog

### Sturm

Böen-Warnung, zweistufig: Prognose → gemessene Böe.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `vorwarn_gust_ms` | `17.0` | Böen-Prognose (m/s) in ≤6 h → Vorwarnung (≈ 61 km/h, Bft 8) |
| `akut_gust_ms` | `25.0` | Gemessene Böe (m/s) → Akut-Alarm (≈ 90 km/h, Bft 10) |
| `hysterese_ms` | `3.0` | (reserviert, für künftige Hysterese-Konfiguration) |

---

### Starkregen

Niederschlags-Intensitätswarnung.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `vorwarn_mmh` | `15.0` | Nowcast-Peak (mm/h) in ≤60 min → Vorwarnung |
| `akut_mmh` | `25.0` | Gemessene Niederschlagsrate (mm/h) → Akut-Alarm |
| `akut_3h_mm` | `30.0` | (reserviert für 3h-Summierung) |

---

### Schneefall

Starker Schneefall bei niedrigen Temperaturen.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `temp_max_c` | `1.0` | Max. Temperatur (°C) für Schneefähigkeit |
| `vorwarn_mmh` | `3.0` | Prognose-Niederschlag (mm/h) bei T ≤ temp_max_c → Vorwarnung |
| `akut_mmh` | `5.0` | Gemessene Rate (mm/h) bei T ≤ temp_max_c → Akut (≈ 5 cm/h Schnee) |

---

### Glatteis

Gefrierregen und Reifglätte.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `temp_max_c` | `1.0` | Max. Temperatur für Gefrierregen-Risiko (°C) |
| `temp_min_reif_c` | `-6.0` | Min. Temperatur für Reifglätte-Erkennung (°C) |
| `spread_max_k` | `0.5` | Max. Taupunkt-Spread (T − Td in K) für Reifglätte bei T < 0 |

Auslösung: T zwischen `temp_min_reif_c` und `temp_max_c` **und** (Niederschlag > 0 **oder** Taupunkt-Spread ≤ `spread_max_k` bei T < 0).

---

### Gewitter

Relay amtlicher Gewitter-Warnungen + Nowcast.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `min_level` | `2` | Mindeststufe amtliche Warnung (1–4; 2 = erhebliche Gefährdung) |

---

### Lake-Effekt (Bodensee)

Lokal begrenzte, intensive Schneeschauer vom Bodensee — für das Wolfurter Rheintal relevant bei Westanströmung.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `temp_max_c` | `1.0` | Max. Lufttemperatur (°C) — muss schneefähig sein |
| `delta_t_min` | `12.0` | Mindest-Temperaturdifferenz Bodensee − Luft (K) |
| `dir_min` | `260.0` | Untere Grenze Windrichtungssektor (°, vom See) |
| `dir_max` | `330.0` | Obere Grenze Windrichtungssektor (°, vom See) |
| `v_min` | `2.0` | Mindestwindgeschwindigkeit (m/s) — zu schwach = kein Transport |
| `v_max` | `14.0` | Maximalwindgeschwindigkeit (m/s) — zu stark = Schauer abgeräumt |
| `rh_min` | `80.0` | Mindestluftfeuchte (%) als Feuchtenachweis |

**Bodensee-Temperatur:** Wird aus dem manuellen Override in den Einstellungen bezogen (gültig 14 Tage). Ohne Override greift die monatliche Klimatologie (Jan–Dez: 5, 4.5, 5.5, 9, 14, 19, 22, 22, 19, 14, 10, 7 °C).

---

### Amtlich (ZAMG-Relay)

Leitet beliebige amtliche Warnungen von GeoSphere Austria / ZAMG weiter.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `min_level` | `2` | Mindeststufe (1–4) der weiterzuleitenden Warnungen |
| `nur_typen` | `[]` | Liste von Warntypen (leer = alle). Mögliche Werte: `"WIND"`, `"RAIN"`, `"SNOW"`, `"ICE"`, `"THUNDERSTORM"` |

Beispiel für nur Wind und Regen: `{"min_level": 2, "nur_typen": ["WIND", "RAIN"]}`

---

### Föhn

Süd-Föhn: Sturmböen + Trockenheit → Brandgefahr, Dachlasten.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `dir_min` | `150.0` | Südwind-Sektor, untere Grenze (°) |
| `dir_max` | `210.0` | Südwind-Sektor, obere Grenze (°) |
| `vorwarn_gust_ms` | `13.0` | Prognose-Böe (m/s) in ≤6 h → Vorwarnung |
| `akut_gust_ms` | `15.0` | Gemessene Böe (m/s) bei Südrichtung + Trockenheit → Akut |
| `rh_max_pct` | `40.0` | Max. Luftfeuchte (%) für Akut-Erkennung |

---

### Waldbrand-Bereitschaft

Trockenheits-Index: Kombination aus Niederschlags-Defizit, Hitze und Wind.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `trocken_tage` | `5` | Anzahl der Tage der Trockenperiode |
| `max_nieder_mm` | `1.0` | Max. Niederschlag (mm) in diesen Tagen — bei Überschreitung kein Alarm |
| `temp_min_c` | `25.0` | Mindesttemperatur (°C) für erhöhte Brandgefahr |
| `rh_max_pct` | `35.0` | Max. Luftfeuchte (%) |
| `wind_min_ms` | `3.0` | Mindestwind (m/s) — befördert Ausbreitung |

**Hinweis:** Erfordert historische Niederschlagsdaten aus der lokalen Wetterstation (Wetter-DB). Ohne Messwerte feuert die Regel nicht.

---

### Tauwetter

Schneller Temperaturanstieg nach Kälteperiode → Schneelasten, Schmelzwasser.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `temp_anstieg_k` | `8.0` | Temperaturanstieg (K) in ≤24 h Prognose → Vorwarnung |
| `temp_schwelle_c` | `2.0` | Aktuell gemessene Temperatur (°C) über der kein Frost mehr herrscht → Akut (kombiniert mit steigendem Pegel) |
| `pegel_trend` | `"steigend"` | (intern — nicht ändern) |

**Pegel-Kopplung:** Der Akut-Alarm wird nur ausgelöst, wenn gleichzeitig ein konfigurierter Pegelstand steigt (Schmelzwasser-Nachweis). Dafür muss mindestens eine Pegelmessstation eingerichtet sein.

---

### Downburst / Schwergewitter

Plötzliche, kleinräumige Sturmböen aus konvektiven Zellen.

| Parameter | Standard | Bedeutung |
|-----------|----------|-----------|
| `min_level` | `3` | Mindeststufe amtliche Schwergewitter-/Sturmwarnung (3 = große Gefährdung) |
| `boe_sprung_ms` | `25.0` | (reserviert für Böensprung-Erkennung) |

---

## Empfänger-Logik

```
Regel-Override (mail_override)        →  hat Vorrang vor Org-Standard
Org-weiter Verteiler (weather_alert_mail) →  Fallback wenn kein Override
```

Beide können gleichzeitig gesetzt sein. Der Override auf Regel-Ebene überschreibt den Org-Standard **vollständig** (kein „Beide senden").

---

## Zustände und Cooldown

| Zustand | Bedeutung |
|---------|-----------|
| `none` | Kein Alarm |
| `vorwarnung` | Prognose-Schwelle überschritten, Ereignis noch nicht eingetreten |
| `akut` | Schwellwert aktuell gemessen (oder amtliche Warnung aktiv) |

**Cooldown** (Standard: 60 Minuten): Im gleichen Zustand wird frühestens nach Ablauf des Cooldowns erneut gesendet. Zustandswechsel (z.B. `vorwarnung → akut`) sendet immer sofort.

---

## Versandprotokoll

Die letzten 20 Versandvorgänge sind direkt in der Einstellungsseite unter der Regelübersicht sichtbar. Jeder Eintrag zeigt Zeit, Regeltyp, Stufe, Kanal und Versandstatus.

---

## Fehlerbehebung

| Problem | Mögliche Ursache |
|---------|-----------------|
| Keine Benachrichtigungen | Regel nicht aktiviert; kein Empfänger konfiguriert; Wetter-Modul deaktiviert |
| Doppelte Meldungen | Cooldown zu kurz (Standard 60 min erhöhen) |
| Keine Lake-Effekt-Meldungen | Windrichtung außerhalb des Sektors; ΔT Bodensee−Luft zu gering (Klimatologie prüfen) |
| Waldbrand feuert nie | Wetter-DB nicht konfiguriert (kein `WEATHER_DATABASE_URL`) oder keine Messwerte der letzten 5 Tage |
| Tauwetter feuert nie | Keine Pegelmessstation konfiguriert oder Pegel nicht steigend |
| Teams-Test schlägt fehl | Webhook-URL abgelaufen (in Teams neu erzeugen) |
| Mail-Test schlägt fehl | SMTP-Konfiguration prüfen (Einstellungen → System → Mail) |
