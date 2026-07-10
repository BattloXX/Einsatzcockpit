# Mail-Versand (SMTP / Office 365)

← [Zurück zur Startseite](Home)

> URL: `/admin/mail`  
> Zugänglich für: `org_admin`, `system_admin`

Jede Organisation kann ihren E-Mail-Versand (Passwort-Reset, Willkommensmail, Schadenmeldung, Atemschutz-Defektmeldung, Wetterwarnung, ...) individuell umstellen: entweder auf einen **eigenen SMTP-Server** oder auf **Office 365 / Microsoft Graph**. Ohne eigene Konfiguration nutzt die Organisation weiterhin den globalen System-SMTP (System-Einstellungen, nur `system_admin`).

---

## Fallback-Kette

Bei jedem Mailversand wird in dieser Reihenfolge geprüft:

```
1. Office 365 / Microsoft Graph
   → aktiviert UND vollständig konfiguriert?  Ja → senden, fertig.
                                               Nein/Fehler → weiter zu 2.
2. Eigener SMTP-Server der Organisation
   → aktiviert UND vollständig konfiguriert?  Ja → senden, fertig.
                                               Nein → weiter zu 3.
3. Globaler System-SMTP (System-Einstellungen)
```

**Wichtig:** Schlägt der Versand über Office 365 fehl (z. B. abgelaufenes Secret, falsche Berechtigung), wird **automatisch** auf SMTP zurückgefallen — die Mail geht trotzdem raus, sofern SMTP (eigen oder global) funktioniert. Ein Fehlschlag wird nur dann sichtbar, wenn *auch* die letzte verfügbare Stufe fehlschlägt.

---

## Voraussetzungen für Office 365

- Rolle im Microsoft-Entra-Tenant: **Anwendungsadministrator** oder **Globaler Administrator**
- Zugang zum **Microsoft Entra Admin Center**: https://entra.microsoft.com
- Ein Postfach (z. B. `einsatz@feuerwehr-beispiel.at`), das als Absender dienen soll — **kein** persönliches Postfach eines einzelnen Mitglieds

Im Unterschied zu [Single Sign-On](Administration-Single-Sign-On) verwendet der Mailversand **App-only-Zugriff** (Client-Credentials) statt einer Benutzeranmeldung — es ist **kein** Redirect-URI und **keine** Benutzerinteraktion nötig. Die App-Registrierung darf für SSO und Mailversand gemeinsam genutzt werden, sollte aus Sicherheitsgründen aber besser **getrennt** angelegt werden (unterschiedliche Berechtigungen, unterschiedlicher Blast-Radius).

---

## Einrichtung in Microsoft Entra / Azure (Überblick)

### Schritt 1 – App-Registrierung anlegen

1. Entra Admin Center → **Identität → Anwendungen → App-Registrierungen → Neue Registrierung**
2. **Name**: z. B. `Einsatzcockpit Mailversand`
3. **Kontotypen**: „Nur Konten in diesem Organisationsverzeichnis" (Single Tenant)
4. **Redirect-URI**: leer lassen (nicht benötigt)
5. **Registrieren**

### Schritt 2 – IDs notieren

Auf der App-Übersicht (beide GUIDs für das Tool bereithalten):
- **Anwendungs-ID (Client)** → Client ID
- **Verzeichnis-ID (Tenant)** → Tenant ID

### Schritt 3 – Client Secret erstellen

**Zertifikate & Geheimnisse → Neuer geheimer Clientschlüssel**

- Beschreibung: z. B. `Mailversand`
- Gültigkeit: 24 Monate empfohlen (**Ablaufdatum notieren!**)
- Den angezeigten **Wert** sofort kopieren — er wird nur einmal angezeigt. Nicht die „Secret-ID" verwenden.

> Läuft das Secret ab, greift automatisch der SMTP-Fallback — der Mailversand reißt also nicht sofort ab, aber Office 365 wird bis zur Erneuerung nicht mehr genutzt.

### Schritt 4 – API-Berechtigung (Application, nicht Delegated!)

**API-Berechtigungen → Berechtigung hinzufügen → Microsoft Graph → Anwendungsberechtigungen (Application permissions)**

- `Mail.Send`

Danach: **„Administratorzustimmung erteilen"** klicken. Ohne diesen Schritt schlägt jeder Sendeversuch mit einem Berechtigungsfehler fehl.

> **Nicht** die Delegated-Variante von `Mail.Send` auswählen — die App agiert eigenständig (App-only), nicht im Namen eines angemeldeten Benutzers.

### Schritt 5 – Absender-Postfach einschränken (dringend empfohlen)

Ohne weitere Einschränkung darf eine App mit `Mail.Send` (Application) **aus jedem Postfach im Tenant** senden. Über eine **Application Access Policy** (Exchange Online PowerShell) wird der Zugriff auf das gewünschte Absender-Postfach begrenzt:

```powershell
# Einmalig verbinden
Connect-ExchangeOnline

# Mail-aktivierte Sicherheitsgruppe anlegen und das Absender-Postfach hinzufügen
New-DistributionGroup -Name "EinsatzcockpitMailSender" -Type Security
Add-DistributionGroupMember -Identity "EinsatzcockpitMailSender" -Member "einsatz@feuerwehr-beispiel.at"

# Zugriff der App auf genau diese Gruppe beschränken
New-ApplicationAccessPolicy `
  -AppId "<Client-ID aus Schritt 2>" `
  -PolicyScopeGroupId "EinsatzcockpitMailSender" `
  -AccessRight RestrictAccess `
  -Description "Einsatzcockpit darf nur aus dem Einsatz-Postfach senden"

# Testen (kann laut Microsoft bis zu 30 Minuten dauern, bis die Policy greift)
Test-ApplicationAccessPolicy -AppId "<Client-ID>" -Identity "einsatz@feuerwehr-beispiel.at"
```

Ohne diesen Schritt funktioniert der Versand trotzdem — es ist eine reine Sicherheitshärtung, damit ein kompromittiertes Secret nicht zum Versand aus beliebigen Postfächern des Tenants missbraucht werden kann.

---

## Einrichtung im Tool

**Einstellungen → Mail-Versand** (URL: `/admin/mail`)

### Eigener SMTP-Server

| Feld | Beschreibung |
|------|-------------|
| **Eigenen SMTP-Server verwenden** | Hauptschalter; ohne Aktivierung gilt der globale System-SMTP |
| **Host** | z. B. `smtp.office365.com`, `smtp.strato.de`, Provider-abhängig |
| **Port** | `587` (STARTTLS, üblich) oder `465` (implizites TLS) |
| **Timeout (Sek.)** | Verbindungs-Timeout, Standard 15 |
| **Benutzername** | SMTP-Login |
| **Passwort** | Wird Fernet-verschlüsselt gespeichert — nie im Klartext angezeigt |
| **Absenderadresse** | Erscheint im „Von"-Feld der Mail |
| **STARTTLS** | Bei Port 465 ohne Wirkung (implizites TLS wird automatisch verwendet) |

### Office 365 / Microsoft Graph

| Feld | Beschreibung |
|------|-------------|
| **Office 365 aktiviert** | Hauptschalter; wird zuerst versucht, bevor auf SMTP zurückgefallen wird |
| **Tenant-ID** | Verzeichnis-ID aus Schritt 2 |
| **App (Client)-ID** | Anwendungs-ID aus Schritt 2 |
| **Client-Secret** | Wert aus Schritt 3 (wird Fernet-verschlüsselt gespeichert) |
| **Absender-Postfach** | Das in Schritt 5 freigegebene Postfach, z. B. `einsatz@feuerwehr-beispiel.at` |

Nach dem Speichern jeweils **„Test-Mail senden"** nutzen (Empfänger optional — leer = an das eigene From-/Absender-Postfach).

---

## Posteingang abrufen (Vorbereitung — noch nicht aktiv)

Beide Formulare enthalten zusätzlich Felder für einen **künftigen** Abruf eingehender Mails aus demselben Postfach (IMAP beim eigenen SMTP-Server, Microsoft Graph `Mail.Read` bei Office 365). Diese Felder werden bereits gespeichert, **es findet aber noch kein automatischer Abruf oder keine Verarbeitung statt** — die Funktion ist reine Vorbereitung für eine spätere Ausbaustufe. Für Office 365 wäre dafür zusätzlich die Application-Permission `Mail.Read` in Azure nötig (analog Schritt 4, nur mit `Mail.Read` statt `Mail.Send`).

---

## Fehlerbehebung

| Meldung / Symptom | Ursache und Lösung |
|-------------------|--------------------|
| Test-Mail (O365) schlägt fehl: `invalid_client` / `AADSTS7000215` | Client-Secret falsch eingetragen (Wert statt Secret-ID) oder abgelaufen → neues Secret erstellen |
| Test-Mail (O365) schlägt fehl: `AADSTS700016` | Tenant-ID oder App (Client)-ID falsch |
| `ErrorAccessDenied` / 403 beim Senden | Administratorzustimmung für `Mail.Send` (Schritt 4) fehlt, oder die Application Access Policy (Schritt 5) lässt das gewählte Absender-Postfach nicht zu |
| Versand funktioniert sporadisch, dann wieder nicht | Wahrscheinlich läuft der SMTP-Fallback: Office 365 schlägt fehl (z. B. Secret bald abgelaufen), SMTP springt ein — Fehlerursache bei Office 365 wie oben prüfen |
| Test-Mail (eigener SMTP) schlägt fehl | Host/Port/STARTTLS prüfen; viele Provider blockieren Login von IPs außerhalb bekannter Länder/Netze — ggf. beim Provider ein „App-Passwort" statt des normalen Kontopassworts verwenden |
| Änderung der Application Access Policy wirkt nicht sofort | Laut Microsoft bis zu 30 Minuten Verzögerung — `Test-ApplicationAccessPolicy` zur Kontrolle nutzen |

---

## Sicherheitshinweise

- **Client-Secret / SMTP-Passwort** werden im Tool jeweils Fernet-verschlüsselt gespeichert.
- **Secret-Ablauf im Kalender vermerken** und rechtzeitig rotieren (neues Secret → im Tool eintragen → altes in Azure löschen). Läuft es unbemerkt ab, fällt der Versand automatisch (und unauffällig) auf SMTP zurück.
- **Application Access Policy (Schritt 5) unbedingt einrichten** — ohne sie darf die App-Registrierung mit `Mail.Send` aus jedem Postfach des gesamten Tenants senden, nicht nur aus dem vorgesehenen.
- Eine separate App-Registrierung für den Mailversand (statt Wiederverwendung der SSO-App) reduziert den Schaden bei einem kompromittierten Secret.

---

## Checkliste

- [ ] App-Registrierung (Single Tenant, App-only) angelegt
- [ ] Tenant-ID + Client-ID notiert
- [ ] Client-Secret erstellt, Wert kopiert, Ablaufdatum vermerkt
- [ ] `Mail.Send` (Application, **nicht** Delegated) + Administratorzustimmung erteilt
- [ ] Application Access Policy auf das Absender-Postfach eingeschränkt
- [ ] Werte im Tool unter `/admin/mail` eingetragen, Office 365 aktiviert
- [ ] Test-Mail über Office 365 erfolgreich
- [ ] (Optional) eigenen SMTP-Server als zusätzlichen Fallback konfiguriert und getestet
