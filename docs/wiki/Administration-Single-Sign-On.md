# Single Sign-On (Microsoft Entra ID)

← [Zurück zur Startseite](Home)

> URL: `/admin/sso`  
> Zugänglich für: `org_admin`, `system_admin`

SSO erlaubt die Anmeldung mit dem **Microsoft-365-Konto** der eigenen Organisation. Jede Org verwaltet ihre eigene App-Registrierung im eigenen Entra-Tenant (kein geteiltes Secret). Neuen Benutzern wird beim ersten Login automatisch ein Konto angelegt (JIT-Provisioning), die Rolle ergibt sich aus der Entra-Gruppe.

---

## Voraussetzungen

- Rolle im Microsoft-Entra-Tenant: **Anwendungsadministrator** oder **Globaler Administrator**
- Die **Redirect-URI** der eigenen Org (im Tool unter **Einstellungen → Single Sign-On** sichtbar):
  ```
  https://<host>/sso/<slug>/callback
  ```
  Beispiel: `https://einsatzcockpit.com/sso/ff-wolfurt/callback`
- Zugang zum **Microsoft Entra Admin Center**: https://entra.microsoft.com

---

## Einrichtung in Microsoft Entra (Überblick)

### Schritt 1 – App-Registrierung anlegen

1. Entra Admin Center → **Identität → Anwendungen → App-Registrierungen → Neue Registrierung**
2. **Name**: z.B. `Einsatzcockpit`
3. **Kontotypen**: „Nur Konten in diesem Organisationsverzeichnis" (Single Tenant)
4. **Redirect-URI**: Plattform **Web**, Wert = exakte Redirect-URI aus dem Tool (kein Slash am Ende)
5. **Registrieren**

### Schritt 2 – IDs notieren

Auf der App-Übersicht (beide GUIDs für das Tool bereithalten):
- **Anwendungs-ID (Client)** → Client ID
- **Verzeichnis-ID (Tenant)** → Tenant ID

### Schritt 3 – Client Secret erstellen

**Zertifikate & Geheimnisse → Neuer geheimer Clientschlüssel**

- Beschreibung: z.B. `ELHW SSO`
- Gültigkeit: 24 Monate empfohlen (**Ablaufdatum notieren!**)
- Den angezeigten **Wert** sofort kopieren — er wird nur einmal angezeigt. Nicht die „Secret-ID" verwenden.

> Läuft das Secret ab, schlägt jeder Login fehl. Rotation rechtzeitig im Kalender vermerken.

### Schritt 4 – API-Berechtigungen

**API-Berechtigungen → Berechtigung hinzufügen → Microsoft Graph → Delegiert**

Auswählen:
- `openid`
- `profile`
- `email`
- `User.Read`
- *(optional)* `GroupMember.Read.All` — nur nötig bei Gruppen-Overage (Benutzer in sehr vielen Gruppen)

Danach: **„Administratorzustimmung erteilen"** klicken.

### Schritt 5 – Gruppen-Anspruch konfigurieren

**Tokenkonfiguration → Gruppenanspruch hinzufügen**

- Empfohlen: **„Der Anwendung zugewiesene Gruppen"** (hält Tokens klein)
- Format: **Gruppen-Objekt-ID (Group ID)** — diese GUID wird im Tool ins Mapping eingetragen

### Schritt 6 – Sicherheitsgruppen anlegen

In **Entra → Gruppen** je eine Sicherheitsgruppe pro App-Rolle anlegen:

| Beispiel-Gruppe | Rolle im Tool |
|----------------|--------------|
| `ELHW-Admin` | Administrator |
| `ELHW-Einsatzleiter` | Einsatzleiter |
| `ELHW-Mannschaft` | Schriftführer |
| `ELHW-Lesen` | Beobachter |

Die **Objekt-ID** jeder Gruppe notieren (in der Gruppen-Übersicht sichtbar).

### Schritt 7 – Zugriff beschränken (empfohlen)

**Unternehmensanwendungen → deine App → Eigenschaften**

- **„Zuweisung erforderlich?"** = **Ja** → Speichern
- **Benutzer und Gruppen** → die angelegten Gruppen der App zuweisen

So können sich nur freigegebene Personen anmelden.

---

## Einrichtung im Tool

**Einstellungen → Single Sign-On** (URL: `/admin/sso`)

| Feld | Beschreibung |
|------|-------------|
| **Tenant ID** | Verzeichnis-ID aus Schritt 2 |
| **Client ID** | Anwendungs-ID aus Schritt 2 |
| **Client Secret** | Wert aus Schritt 3 (wird verschlüsselt gespeichert) |
| **Erlaubte Domains** | Optional: nur Konten dieser Mail-Domain, z.B. `wolfurt.at` |
| **Standard-Rolle** | Rolle für Benutzer ohne passende Gruppe (oder Anmeldung ablehnen) |
| **Gruppen-Mapping** | Je Zeile: Objekt-ID der Gruppe → Rolle auswählen (+ optionales Label) |
| **Nur SSO (lokalen Login deaktivieren)** | Nur aktivieren, wenn ein lokaler Break-Glass-Admin bleibt |
| **Single Sign-On aktivieren** | Hauptschalter |

Nach dem Speichern: **„Verbindung testen"** prüft die Erreichbarkeit des Tenants.

---

## Was beim ersten Login passiert (JIT)

1. Benutzer klickt auf **„Mit Microsoft 365 anmelden"** auf der Login-Seite.
2. Microsoft leitet nach erfolgreicher Authentifizierung zurück.
3. Das Tool prüft den `tid`-Claim (muss zum konfigurierten Tenant passen) und die Gruppen.
4. Kein Konto vorhanden → Konto wird **automatisch erstellt**, Name und E-Mail aus Microsoft übernommen.
5. Rolle wird aus dem Gruppen-Mapping abgeleitet. Bei jedem weiteren Login wird die Rolle **erneut abgeglichen** (Entra ist Quelle der Wahrheit).
6. Existiert bereits ein lokales Konto mit gleicher E-Mail → wird **verknüpft** (kein Duplikat).

---

## Fehlerbehebung

| Meldung / Symptom | Ursache und Lösung |
|-------------------|--------------------|
| **AADSTS50011** – Redirect-URI stimmt nicht | Redirect-URI in Entra muss exakt der aus dem Tool entsprechen (https, korrekter Slug, kein End-Slash) |
| **AADSTS65001** – keine Zustimmung | Administratorzustimmung in Schritt 4 erteilen |
| **AADSTS7000215 / AADSTS700016** – ungültiger Clientschlüssel | Secret-**Wert** statt Secret-ID eingetragen, oder Secret abgelaufen → neues Secret erstellen und im Tool hinterlegen |
| **AADSTS50105** – Benutzer nicht zugewiesen | „Zuweisung erforderlich = Ja" aktiv, aber Benutzer/Gruppe der App nicht zugewiesen (Schritt 7) |
| Login klappt, aber **falsche/keine Rolle** | Gruppen-Objekt-ID im Mapping prüfen; Gruppen-Claim konfiguriert? Benutzer Mitglied der Gruppe? |
| **„Tenant stimmt nicht überein"** | Tenant ID im Tool muss zur App-Registrierung passen |
| Rollen fehlen bei vielen Gruppen | Gruppen-Overage: `GroupMember.Read.All` Berechtigung erteilen oder in Schritt 5 „der Anwendung zugewiesene Gruppen" verwenden |

---

## Sicherheitshinweise

- **Secret-Ablauf im Kalender vermerken** und rechtzeitig rotieren (neues Secret → im Tool eintragen → altes löschen).
- **Break-Glass**: Mindestens einen lokalen Admin-Zugang mit Passwort behalten, falls Entra oder Internet nicht verfügbar ist. Deshalb „Nur SSO" nur aktivieren, wenn ein solcher Notfallzugang existiert.
- **Client Secret** wird im Tool Fernet-verschlüsselt gespeichert — nur das Secret, nicht die anderen IDs, ist schützenswert.
- **Bedingter Zugriff (Conditional Access)** und **MFA** in Entra gelten automatisch — empfohlen.
- Die Rolle `system_admin` kann über das Gruppen-Mapping **nicht** vergeben werden (wird technisch ausgeschlossen).

---

## Checkliste

- [ ] App-Registrierung (Single Tenant) angelegt
- [ ] Redirect-URI exakt aus dem Tool übernommen
- [ ] Tenant ID + Client ID notiert
- [ ] Client Secret erstellt, Wert kopiert, Ablaufdatum vermerkt
- [ ] API-Berechtigungen + Administratorzustimmung erteilt
- [ ] Gruppen-Anspruch (Group ID) konfiguriert
- [ ] Sicherheitsgruppen + Objekt-IDs notiert
- [ ] (Optional) Zuweisung erforderlich + Gruppen der App zugewiesen
- [ ] Werte + Gruppen-Mapping im Tool eingetragen, SSO aktiviert
- [ ] Verbindungstest erfolgreich
- [ ] Test-Login mit einem Microsoft-Konto der Org durchgeführt
