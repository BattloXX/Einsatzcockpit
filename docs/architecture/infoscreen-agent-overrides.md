# Infoscreen-Agent: Uebersteuerungen und URL-Allowlist (S5)

Stand: 2026-11-07. Verwaltung unter Infoscreens > Geraete & Plaene > Uebersteuerung.
Keine neue Rolle: `org_admin`, mit den bestehenden Admin-Alias-/Sysadmin-Rechten
von `require_role`. Keine MCP- oder Sysadmin-Erweiterung. Keine Migration erforderlich.

## Entscheidungsmatrix

Die erste zutreffende Zeile gewinnt. Alle Intervalle sind halboffen `[von, bis)`.
Uebersteuerungen gelten nur fuer die eigene Organisation und das zugeordnete
Display, bei NULL fuer alle Displays dieser Organisation. Bei mehreren gueltigen
Overrides derselben Art gewinnt der spaetere Beginn, danach die groessere ID.

| Bedingung | Modus / Wirkung |
| --- | --- |
| Geraet gesperrt, Display inaktiv oder Alarm-Token fehlt | `locked`, schwarze Sperrseite, Monitor an |
| GSL aktiv, `gsl_override_erlaubt`, gueltiger `gsl_page` | `override`, gewaehlte Seite, Monitor an |
| GSL aktiv | `gsl`, bestehender Alarm-Infoscreen, Monitor an |
| Regulaerer Einsatz aktiv | `incident`, bestehender Alarm-Infoscreen, Monitor an |
| Einsatz-/GSL-Ende innerhalb Nachlauf | `nachlauf`, Monitor an |
| Gueltiger `manual` | `manual`, gewaehlte Seite, Monitor an |
| Zeitplan-Slot | `schedule`, Slot-Stromzustand und Rotation |
| Sonst | `default`, bestehender Alarm-Infoscreen, Monitor an |

Ein neuer regulaerer Einsatz unterbricht einen GSL-Seitenoverride bei aktiver GSL
nicht. Ohne aktive GSL wirkt dieser nie. Das letzte GSL-Ende wird separat geladen:
Ein `gsl_page`, dessen Beginn vor/auf diesem Ende liegt, bleibt unwirksam, auch
wenn vor seinem Ablauf eine neue GSL beginnt. Geplante Overrides mit Beginn nach
diesem Ende koennen in einer dann aktiven GSL wirken. Ende oder Aufheben beendet
die Wirkung ebenfalls. `manual` bleibt hinter GSL, Einsatz und Nachlauf.
`valid_until` liegt spaetestens am naechsten Override-Beginn/-Ende, ggf. frueher
an einer Plan-/Nachlaufgrenze. GSL-/Einsatzwechsel invalidieren ueber die S3-Hooks.

**Korrektur gegen S2:** Dort wirkte ein erlaubter `gsl_page` auch ohne aktive GSL;
S5 verlangt die aktive GSL und verhindert das Wiederaufleben nach deren Ende.

## Eingabe und UI-Texte

- "Uebersteuerung anlegen": Display oder "Alle Displays der Organisation", Art,
  Seite, Referenz, Von, Bis und Pflicht-Begruendung (maximal 500 Zeichen).
- "Zeiten in [Org-Zeitzone]. Pflicht-Ablauf nach maximal 8 Stunden."
  Lokale Formulareingaben werden ueber `local_input_to_utc` als naive UTC gespeichert.
  Dauer wird in UTC geprueft; Offset-Eingaben sind unzulaessig.
- "Wetter fuer 30 min" / "Wetter fuer 2 h": nur freigegebene Displays;
  gleiche Validierung, Pflicht-Begruendung, Audit und Versionierung.
- "Aufheben": eigene Pflicht-Begruendung, Zeit und Benutzer-ID speichern;
  bereits aufgehobene Overrides liefern 422.
- "Display X zeigt jetzt: Wetter bis [lokale Zeit]": Resolver-Vorschau alle
  15 Sekunden per HTMX, ohne `state()`/Revisionsschreibzugriff. Das ist die
  serverseitige Sollwirkung; Heartbeat-Diagnose bestaetigt die Geraetewirkung separat.
- "Begruendungen ohne Einsatzdetails oder personenbezogene Angaben eingeben."

Historie zeigt aktuelle/geplante sowie in den letzten 30 Tagen abgelaufene oder
aufgehobene Eintraege. Alle Formulare haben CSRF und gezielte HTMX-Swaps.
S4-Formularlayout bleibt bei <=760px einspaltig.

## Serverseitige Seitenaufloesung

| Kind | Referenz und Route |
| --- | --- |
| `weather` | Bestehender Wetter-Token, org-gefilterter Hash-Lookup, `/wetter/infoscreen/{token}`. Leere Referenz verwendet nur den Token der konfigurierten Wetter-URL, verwirft deren Host. |
| `statistik` | Bestehender Statistik-Token, org-gefilterter Hash-Lookup und Aktivierung pruefen, `/infoscreen/statistik/{token}` |
| `custom_allowlisted` | Vollstaendige HTTPS-URL, exakter normalisierter Allowlist-Host und erneute DNS-Pruefung |
| `lagekarte`, `einsatzstellen` | Nicht angeboten: keine eigenstaendige tokenbasierte GSL-Infoscreen-Seite vorhanden |

`ui_major_incident.py` stellt angemeldete Lage-/Kartenrouten bereit.
`_gsl_infoscreen_daten` liefert Kartenmarker und Einsatzstellen an den bestehenden
Alarm-Infoscreen, aber keine separat adressierbare tokenbasierte Seite. Deshalb
auch keine Lagekarte-Schnellaktion. S5 erstellt keine neuen Public-Routen.
Interne Pfade entstehen ausschliesslich aus festen Praefixen und verifizierten
Tokens. Gespeicherte Referenzen werden bei der Aufloesung erneut gegen Token und
Org geprueft, damit ein geloeschter Token nicht weiter wirkt. Tokens und Custom-URLs
erscheinen weder in der Uebersicht/Live-Anzeige noch im Audit.

## Allowlist und Grenzen

Nur Domainnamen, IDNA-normalisiert (Unicode und Punycode), maximal 253 Zeichen.
Keine IP-Literale, localhost, .localhost, .local, .internal, leeren/ungueltigen Labels
oder numerischen Domain-Endungen. Beim Speichern alle DNS-Antworten pruefen;
leere Antwort, DNS-Fehler sowie private, Loopback-, Link-Local-, reservierte und
Multicast-Adressen werden abgelehnt. Custom-URL: maximal 2048 Zeichen, HTTPS,
nur Port 443, keine Userinfo, Steuerzeichen, Leerzeichen oder Backslashes.
Beim Override-Anlegen erneut aufloesen, um DNS-Aenderungen seit Hostfreigabe zu
erkennen. Entfernen eines Hosts macht dessen Overrides beim naechsten Resolverlauf
unwirksam. `/config` liefert die org-gefilterten Hosts; der eigene Server-Host stammt aus
`effective_public_base_url`, niemals aus dem Request-Host-Header.

Die Speicherpruefung pinnt keine IP fuer Chromium: DNS-Rebinding nach dem Speichern
und Weiterleitungen muessen Agent/Kiosk ebenfalls bei Navigation begrenzen (E9).
Der Server ruft Custom-URLs nicht ab. Lesende Resolver-/Live-Abfragen machen keine
DNS-Netzaufrufe. Hostfreigaben gelten exakt, ohne Wildcards oder Subdomainfreigabe.

## Transaktion, Audit und Aufbewahrung

Jede erfolgreiche Mutation schreibt die Aenderung und den atomaren, explizit
org-gefilterten `config_version`-Increment in derselben Transaktion. Danach
`publish_config_changed` fuer die betroffenen Displays. Org-weite Aenderungen
erhoehen auch Geraete ohne Displays. Keine Revisionserhoehung beim UI-Lesen.
Override-Audit speichert `created_by`, Begruendung und technische IDs/Kinds;
Aufheben ebenso mit `aufgehoben_von`. Keine URL, Tokens, Namen, IP oder sonstigen
personenbezogenen Zusatzdaten. Freitext-Begruendungen liegen in der Verantwortung
der Administration. Aufheben aendert die urspruengliche Begruendung nicht.

Abgelaufene Overrides ignoriert der Resolver sofort. Der bestehende taegliche
Retention-Loop entfernt Eintraege mit `bis < UTC-jetzt - 30 Tage`, in org-gefilterten
Batches (Default 500), getrennt von der 90-Tage-Diagnose-Retention. Diese historische
Bereinigung aendert keine wirksame Konfiguration und erzeugt keinen Wake-Hinweis.
