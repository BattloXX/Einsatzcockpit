"""Zentraler Admin-Navigationskatalog. Autorisierung bleibt in den Routern."""

from dataclasses import dataclass, replace
from typing import Literal


@dataclass(frozen=True)
class AdminTab:
    id: str
    label: str
    href: str
    aktiv_praefixe: tuple[str, ...]
    exakt: bool = False
    rollen: tuple[str, ...] = ()
    modulflag: str | None = None

    def ist_aktiv(self, path: str) -> bool:
        return any(path == p if self.exakt else path.startswith(p) for p in self.aktiv_praefixe)


@dataclass(frozen=True)
class AdminEintrag:
    id: str
    label: str
    gruppe: str
    icon: str
    href: str
    aktiv_praefixe: tuple[str, ...]
    tooltip: str = ""
    rollen: tuple[str, ...] = ()
    sichtbarkeit: Literal["voll", "alle", "sysadmin", "org_admin"] = "voll"
    modulflag: str | None = None
    tabs: tuple[AdminTab, ...] = ()
    badge_provider: str | None = None
    neues_fenster: bool = False
    icon_art: Literal["icon", "modul_icon", "text"] = "icon"
    exakt: bool = False
    subheading: str = ""
    inaktiv_praefixe: tuple[str, ...] = ()
    aktive_gruppe: str | None = None

    def ist_aktiv(self, path: str) -> bool:
        return (
            not self.neues_fenster
            and not any(path.startswith(p) for p in self.inaktiv_praefixe)
            and any(path == p if self.exakt else path.startswith(p) for p in self.aktiv_praefixe)
        )


@dataclass(frozen=True)
class AdminGruppe:
    key: str
    titel: str
    eintraege: tuple[AdminEintrag, ...]
    standard_offen: bool = True
    ueberschrift: bool = True
    aktiv_praefixe: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdminNavigation:
    gruppen: tuple[AdminGruppe, ...]
    aktive_gruppe: str
    aktiver_eintrag: AdminEintrag | None
    aktiver_tab: AdminTab | None

    def eintrag(self, eintrag_id: str) -> AdminEintrag | None:
        return next((e for g in self.gruppen for e in g.eintraege if e.id == eintrag_id), None)

    def aktiver_tab_fuer(self, eintrag_id: str, path: str) -> AdminTab | None:
        # Infoscreen-Tabs werden auch beim separaten Sidebar-Link "Externe Zugaenge" angezeigt.
        eintrag = self.eintrag(eintrag_id)
        return next((t for t in eintrag.tabs if t.ist_aktiv(path)), None) if eintrag else None


_INFOSCREENS_TABS = (
    AdminTab("verwaltung", "Alarm-Infoscreen", "/infoscreen-alarm/verwaltung", ("/infoscreen-alarm",)),
    AdminTab(
        "infoscreen", "Wetter-Infoscreen", "/admin/settings/wetter/infoscreen", ("/admin/settings/wetter/infoscreen",)
    ),
    AdminTab("statistik", "Statistik", "/admin/settings/statistik", ("/admin/settings/statistik",)),
    AdminTab(
        "strassensperren_infoscreen",
        "Straßensperren",
        "/admin/settings/strassensperren-infoscreen",
        ("/admin/settings/strassensperren-infoscreen",),
        modulflag="strassensperren_enabled",
    ),
)

_SMS_TABS = (
    AdminTab("sms_senden", "SMS senden", "/admin/sms-senden", ("/admin/sms-senden",), exakt=True),
    AdminTab("sms_provider", "SMS-Versandweg", "/admin/sms-provider", ("/admin/sms-provider",), exakt=True),
    AdminTab("sms_empfang", "SMS-Empfang", "/admin/sms-empfang", ("/admin/sms-empfang",), exakt=True),
    AdminTab("einsatzinfo_sms", "SMS-Einsatzinfo", "/admin/einsatzinfo-sms", ("/admin/einsatzinfo-sms",), exakt=True),
)

_ZUGAENGE_TABS = (
    AdminTab("geraete_login", "Geräte-Login", "/admin/geraete-login", ("/admin/geraete-login",)),
    AdminTab("api_keys", "API-Keys", "/admin/api-keys", ("/admin/api-keys",)),
    AdminTab("lagekarte_tokens", "Lagekarte-Tokens", "/admin/lagekarte-tokens", ("/admin/lagekarte-tokens",)),
)

_FOERDERSTRECKE_TABS = (
    AdminTab("foerderpumpen", "Pumpen", "/admin/foerderpumpen", ("/admin/foerderpumpen",)),
    AdminTab("foerderschlaeuche", "Schläuche", "/admin/foerderschlaeuche", ("/admin/foerderschlaeuche",)),
    AdminTab("foerderkalibrierung", "Kalibrierung", "/admin/foerderkalibrierung", ("/admin/foerderkalibrierung",)),
)

_EINSATZVORLAGEN_TABS = (
    AdminTab("auftragsvorlagen", "Auftragsvorlagen", "/admin/auftragsvorlagen", ("/admin/auftragsvorlagen",)),
    AdminTab("meldungsvorlagen", "Meldungsvorlagen", "/admin/meldungsvorlagen", ("/admin/meldungsvorlagen",)),
    AdminTab("lage_hinweise", "Lage-Hinweise", "/admin/lage-hinweise", ("/admin/lage-hinweise",)),
    AdminTab("default_meldungen", "Default-Meldungen", "/admin/default-meldungen", ("/admin/default-meldungen",)),
)

ADMIN_GRUPPEN = (
    AdminGruppe(
        "stammdaten",
        "Stammdaten",
        (
            AdminEintrag(
                "fahrzeuge",
                "Ressourcen",
                "stammdaten",
                "firetruck",
                "/admin/fahrzeuge",
                ("/admin/fahrzeuge",),
                tooltip="Eigene und fremde Ressourcen: Fahrzeuge, Geräte, Einheiten",
            ),
            AdminEintrag(
                "mitglieder",
                "Mitglieder",
                "stammdaten",
                "users",
                "/admin/mitglieder",
                ("/admin/mitglieder",),
                tooltip="Mannschaftsliste mit Qualifikationen",
                sichtbarkeit="alle",
            ),
            AdminEintrag(
                "gruppen",
                "Gruppen",
                "stammdaten",
                "users-group",
                "/admin/gruppen",
                ("/admin/gruppen",),
                tooltip="Benannte Empfaengergruppen (fuer SMS-Versand, Benachrichtigungen, etc.)",
            ),
            AdminEintrag(
                "qualifikationen",
                "Qualifikationen",
                "stammdaten",
                "award",
                "/admin/qualifikationen",
                ("/admin/qualifikationen",),
                tooltip="AGT, GK, MA u.a. — Codes für Mitglieder",
                sichtbarkeit="alle",
            ),
            AdminEintrag(
                "alarmtypen",
                "Alarmtypen",
                "stammdaten",
                "urgent",
                "/admin/alarmtypen",
                ("/admin/alarmtypen",),
                tooltip="Bekannte Einsatzstichwörter und ihre Eigenschaften",
                subheading="Alarm-Reaktionen",
            ),
            AdminEintrag(
                "ausrueckordnung",
                "Ausrückordnung",
                "stammdaten",
                "clipboard-list",
                "/admin/ausrueckordnung",
                ("/admin/ausrueckordnung",),
                tooltip="Welche Fahrzeuge rücken bei welchem Stichwort aus?",
            ),
            AdminEintrag(
                "einsatzvorlagen",
                "Einsatzvorlagen",
                "stammdaten",
                "files",
                "/admin/auftragsvorlagen",
                (
                    "/admin/auftragsvorlagen",
                    "/admin/meldungsvorlagen",
                    "/admin/lage-hinweise",
                    "/admin/default-meldungen",
                ),
                tooltip=(
                    "Auftrags- und Meldungsvorlagen, Lage-Hinweise und automatische Default-Meldungen pro "
                    "Alarmstichwort"
                ),
                tabs=_EINSATZVORLAGEN_TABS,
            ),
        ),
    ),
    AdminGruppe(
        "verwaltung",
        "Verwaltung",
        (
            AdminEintrag(
                "einsatz_import",
                "Einsatz-Import",
                "verwaltung",
                "inbox",
                "/admin/einsatz-import",
                ("/admin/einsatz-import",),
                tooltip="Historische Leitstellen-Excel-Exporte importieren",
            ),
            AdminEintrag(
                "benutzer",
                "Benutzer",
                "verwaltung",
                "user-cog",
                "/admin/benutzer",
                ("/admin/benutzer",),
                tooltip="Login-Accounts, Rollen, Passwörter",
            ),
            AdminEintrag(
                "verleih_artikel",
                "Geräteverleih",
                "verwaltung",
                "package",
                "/admin/verleih-artikel",
                ("/admin/verleih",),
                tooltip="Geräteverleih Artikelstammdaten und Stücklisten",
                subheading="Ausrüstung",
            ),
            AdminEintrag(
                "wasserstellen",
                "Wasserstellen",
                "verwaltung",
                "droplet",
                "/admin/wasserstellen",
                ("/admin/wasserstellen",),
                tooltip="Löschwasser-Stammdaten: Hydranten, Saugstellen, Löschteiche — pflegen und aus CSV importieren",
            ),
            AdminEintrag(
                "foerderstrecke",
                "Förderstrecke",
                "verwaltung",
                "foerderstrecke",
                "/admin/foerderpumpen",
                ("/admin/foerderpumpen", "/admin/foerderschlaeuche", "/admin/foerderkalibrierung"),
                tooltip=(
                    "Förderstrecke: eigene Pumpen mit Kennlinie und Schläuche verwalten, Vorlagen wie TS "
                    "1600/TS 1200 übernehmen, Übungsmessungen kalibrieren"
                ),
                modulflag="foerderstrecke_enabled",
                icon_art="modul_icon",
                tabs=_FOERDERSTRECKE_TABS,
            ),
            AdminEintrag(
                "probenplanung_probearten",
                "Probearten",
                "verwaltung",
                "calendar-event",
                "/admin/probenplanung/probearten",
                ("/admin/probenplanung/probearten",),
                tooltip="",
                subheading="Probenplanung",
                modulflag="probenplanung_enabled",
            ),
            AdminEintrag(
                "probenplanung_vorlagen",
                "Checklisten-Vorlagen",
                "verwaltung",
                "clipboard-list",
                "/admin/probenplanung/vorlagen",
                ("/admin/probenplanung/vorlagen",),
                tooltip="",
                modulflag="probenplanung_enabled",
            ),
            AdminEintrag(
                "probenplanung_oeffentlich",
                "Öffentlicher Plan",
                "verwaltung",
                "world",
                "/admin/probenplanung/oeffentlich",
                ("/admin/probenplanung/oeffentlich",),
                tooltip="",
                modulflag="probenplanung_enabled",
            ),
            AdminEintrag(
                "zugaenge",
                "Zugänge & Tokens",
                "verwaltung",
                "lock-password",
                "/admin/geraete-login",
                ("/admin/geraete-login", "/admin/api-keys", "/admin/lagekarte-tokens"),
                tooltip="Geräte-Login-Token, API-Keys für externe Systeme und Lagekarte-Tokens verwalten",
                subheading="Zugänge und Tokens",
                tabs=_ZUGAENGE_TABS,
            ),
            AdminEintrag(
                "push_nachrichten",
                "Push-Nachrichten",
                "verwaltung",
                "speakerphone",
                "/admin/push-nachrichten",
                ("/admin/push-nachrichten",),
                tooltip="Manuelle Push-Nachrichten an alle oder einzelne Benutzer",
            ),
            AdminEintrag(
                "audit",
                "Audit-Log",
                "verwaltung",
                "history",
                "/admin/audit",
                ("/admin/audit",),
                tooltip="Vollständiges Aktionsprotokoll",
            ),
            AdminEintrag(
                "medien",
                "Medienverwaltung",
                "verwaltung",
                "photo",
                "/admin/medien",
                ("/admin/medien",),
                tooltip="Alle hochgeladenen Medien: Größe, Alter, Typ – sortierbar und filterbar",
                sichtbarkeit="org_admin",
            ),
            AdminEintrag(
                "sms",
                "SMS",
                "verwaltung",
                "mail",
                "/admin/sms-senden",
                ("/admin/sms-senden", "/admin/sms-provider", "/admin/sms-empfang", "/admin/einsatzinfo-sms"),
                tooltip="SMS senden sowie Versandweg, Empfang und Einsatzinfo konfigurieren",
                tabs=_SMS_TABS,
            ),
            AdminEintrag(
                "infoscreens",
                "Infoscreens",
                "verwaltung",
                "device-tv",
                "/infoscreen-alarm/verwaltung",
                ("/infoscreen-alarm", "/admin/settings/wetter/infoscreen", "/admin/settings/statistik"),
                tooltip="Alarm-, Wetter- und Statistik-Infoscreens verwalten",
                tabs=_INFOSCREENS_TABS,
            ),
            AdminEintrag(
                "gateway",
                "Gateway / Druck",
                "verwaltung",
                "printer",
                "/gateway",
                ("/gateway",),
                tooltip="Drucker-Gateway im Gerätehaus: Pairing, Drucker, Druckregeln und Druckverlauf",
                modulflag="gateway_enabled",
            ),
        ),
        aktiv_praefixe=("/admin/push", "/admin/probenplanung"),
    ),
    AdminGruppe(
        "einstellungen",
        "Einstellungen",
        (
            AdminEintrag(
                "settings",
                "Organisation",
                "einstellungen",
                "building-bank",
                "/admin/settings",
                ("/admin/settings",),
                tooltip="Organisations-Stammdaten, Logo, Farbe",
                exakt=True,
            ),
            AdminEintrag(
                "gsl_einstellungen",
                "GSL-Einstellungen",
                "einstellungen",
                "urgent",
                "/admin/gsl-einstellungen",
                ("/admin/gsl-einstellungen",),
                tooltip="GSL: Module, Lagemeldung, Geräteverleih je Org konfigurieren",
            ),
            AdminEintrag(
                "settings_wetter",
                "Wetter",
                "einstellungen",
                "wetter",
                "/admin/settings/wetter",
                ("/admin/settings/wetter",),
                tooltip="Wetterstation, Pegel, Warnungen und Wetter-Aktivierung",
                exakt=True,
                icon_art="modul_icon",
            ),
            AdminEintrag(
                "sso",
                "SSO / Microsoft 365",
                "einstellungen",
                "key",
                "/admin/sso",
                ("/admin/sso",),
                tooltip="Microsoft 365 / Entra ID Single Sign-On konfigurieren",
                subheading="Schnittstellen",
            ),
            AdminEintrag(
                "lis",
                "LIS-Anbindung",
                "einstellungen",
                "antenna",
                "/admin/lis",
                ("/admin/lis",),
                tooltip="Leitstellenanbindung (LIS/IPR): Verbindung ein-/ausschalten, Zugangsdaten hinterlegen",
            ),
            AdminEintrag(
                "dibos",
                "DIBOS/Elvis-Tracing",
                "einstellungen",
                "radio",
                "/admin/dibos",
                ("/admin/dibos",),
                tooltip="DIBOS EventHub / Elvis-Anbindung: rein lesendes Tracing, Zugangsdaten hinterlegen",
            ),
            AdminEintrag(
                "mail",
                "Mail-Versand",
                "einstellungen",
                "mail",
                "/admin/mail",
                ("/admin/mail",),
                tooltip="Eigener SMTP-Server und/oder Office 365 / Microsoft Graph je Organisation",
                inaktiv_praefixe=("/admin/mail/eingang", "/admin/mail-weiterleitungen"),
            ),
            AdminEintrag(
                "mail_eingang",
                "Posteingang",
                "einstellungen",
                "inbox",
                "/admin/mail/eingang",
                ("/admin/mail/eingang",),
                tooltip="Eingegangene Mails aller Empfangswege und was mit ihnen passiert ist",
                badge_provider="mail_eingang_offene_count",
            ),
            AdminEintrag(
                "teams_alarmierung",
                "Teams-Einsatzinfo",
                "einstellungen",
                "message",
                "/admin/teams-alarmierung",
                ("/admin/teams-alarmierung",),
                tooltip=(
                    "Vollständige Alarmierung als Teams-Karte: Webhook-Basis-Modus + optionale "
                    "Bot-Erweiterung mit Zusage/Absage"
                ),
            ),
            AdminEintrag(
                "einsatzinfo_mail",
                "Einsatzinfo-Mail",
                "einstellungen",
                "mail",
                "/admin/einsatzinfo-mail",
                ("/admin/einsatzinfo-mail",),
                tooltip=(
                    "Automatische Einsatz-Mail bei Alarmeingang - Verteiler und Vorlagen "
                    "wie bei der SMS-Einsatzinfo"
                ),
                aktive_gruppe="verwaltung",
            ),
            AdminEintrag(
                "wordpress_berichte",
                "WordPress-Berichte",
                "einstellungen",
                "world",
                "/admin/wordpress-berichte",
                ("/admin/wordpress-berichte",),
                tooltip="Automatische Website-Entwürfe beim Abschluss eines Einsatzes konfigurieren",
                aktive_gruppe="stammdaten",
            ),
            AdminEintrag(
                "org_backup",
                "Datensicherung",
                "einstellungen",
                "device-floppy",
                "/admin/org-backup",
                ("/admin/org-backup",),
                tooltip="Alle Org-Daten als Archiv sichern (Download / Remote-Ziel)",
                aktive_gruppe="stammdaten",
            ),
            AdminEintrag(
                "ki_anfragen",
                "KI-Anfragen-Log",
                "einstellungen",
                "KI",
                "/admin/ki-anfragen",
                ("/admin/ki-anfragen",),
                tooltip="Tokenverbrauch und Fehler der KI-Anfragen anzeigen",
                icon_art="text",
            ),
            AdminEintrag(
                "systemstatus",
                "Systemstatus",
                "einstellungen",
                "traffic-lights",
                "/admin/systemstatus",
                ("/admin/systemstatus",),
                tooltip="Status externer Gateways und Monitoring-Tokens verwalten",
            ),
            AdminEintrag(
                "strassensperren_infoscreen",
                "Infoscreen",
                "einstellungen",
                "device-desktop",
                "/strassensperren/infoscreen",
                (),
                tooltip="Interne Vollbildanzeige der aktuellen Straßensperren öffnen",
                subheading="Straßensperren",
                modulflag="strassensperren_enabled",
                neues_fenster=True,
            ),
            AdminEintrag(
                "settings_strassensperren_infoscreen",
                "Externe Zugänge",
                "einstellungen",
                "link",
                "/admin/settings/strassensperren-infoscreen",
                ("/admin/settings/strassensperren-infoscreen",),
                tooltip="Externe Statusseiten und Infoscreens verwalten",
                modulflag="strassensperren_enabled",
            ),
            AdminEintrag(
                "strassensperren_einstellungen",
                "Teams",
                "einstellungen",
                "message",
                "/strassensperren/einstellungen",
                ("/strassensperren/einstellungen",),
                tooltip="Microsoft-Teams-Benachrichtigungen für Straßensperren konfigurieren",
                modulflag="strassensperren_enabled",
            ),
            AdminEintrag(
                "settings_planspiele",
                "Einstellungen & Adresspool",
                "einstellungen",
                "settings",
                "/admin/settings/planspiele",
                ("/admin/settings/planspiele",),
                tooltip="",
                exakt=True,
                subheading="Planspiele",
                modulflag="planspiel_enabled",
                rollen=("org_admin", "admin", "system_admin", "uebungsleiter"),
            ),
            AdminEintrag(
                "settings_planspiele_szenarien",
                "Szenariokatalog",
                "einstellungen",
                "masks-theater",
                "/admin/settings/planspiele/szenarien",
                ("/admin/settings/planspiele/szenarien",),
                tooltip="",
                modulflag="planspiel_enabled",
                rollen=("org_admin", "admin", "system_admin", "uebungsleiter"),
            ),
            AdminEintrag(
                "settings_planspiele_stichworte",
                "Stichwort-Referenz",
                "einstellungen",
                "books",
                "/admin/settings/planspiele/stichworte",
                ("/admin/settings/planspiele/stichworte",),
                tooltip="",
                modulflag="planspiel_enabled",
                rollen=("org_admin", "admin", "system_admin", "uebungsleiter"),
            ),
            AdminEintrag(
                "ki_prompts",
                "KI-Prompts",
                "einstellungen",
                "sparkles",
                "/admin/ki-prompts",
                ("/admin/ki-prompts",),
                tooltip="KI-System-Prompts verwalten und versionieren",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "systemdiagnose",
                "Systemdiagnose",
                "einstellungen",
                "server",
                "/admin/systemdiagnose",
                ("/admin/systemdiagnose",),
                tooltip="",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "system_einstellungen",
                "System",
                "einstellungen",
                "settings",
                "/admin/system-einstellungen",
                ("/admin/system-einstellungen",),
                tooltip="VAPID, Push-Konfiguration, globale Parameter",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "mail_weiterleitungen",
                "Mail-Weiterleitungen",
                "einstellungen",
                "mail",
                "/admin/mail-weiterleitungen",
                ("/admin/mail-weiterleitungen",),
                tooltip="",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "system_server_log",
                "Server-Log",
                "einstellungen",
                "clipboard-list",
                "/admin/system/server-log",
                ("/admin/system/server-log",),
                tooltip="Live-Log des Server-Prozesses — Level-Filter, Auto-Refresh",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "seiten",
                "Seiten",
                "einstellungen",
                "world",
                "/admin/seiten",
                ("/admin/seiten",),
                tooltip="Öffentliche Seiten per WYSIWYG bearbeiten",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "system_update",
                "System-Update",
                "einstellungen",
                "upload",
                "/admin/system/update",
                ("/admin/system/update",),
                tooltip="Neues Release per ZIP einspielen",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "backup",
                "Backup",
                "einstellungen",
                "package",
                "/admin/backup",
                ("/admin/backup",),
                tooltip="Stammdaten als JSON sichern oder wiederherstellen",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "db_backups",
                "DB-Dumps",
                "einstellungen",
                "archive",
                "/admin/db-backups",
                ("/admin/db-backups",),
                tooltip="Serverweite Datenbank-Dumps und deren Aufbewahrung verwalten",
                sichtbarkeit="sysadmin",
                aktive_gruppe="stammdaten",
            ),
            AdminEintrag(
                "organisations",
                "Alle Orgs",
                "einstellungen",
                "world",
                "/admin/organisations",
                ("/admin/organisations",),
                tooltip="Alle Organisationen verwalten (Multi-Org)",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "system_orgs",
                "System-Konsole",
                "einstellungen",
                "uebersicht",
                "/admin/system/orgs",
                ("/admin/system/orgs",),
                tooltip="KPI-Übersicht aller Orgs mit Schnellzugriff",
                sichtbarkeit="sysadmin",
                icon_art="modul_icon",
            ),
            AdminEintrag(
                "system_quotas",
                "Quota-Verwaltung",
                "einstellungen",
                "package",
                "/admin/system/quotas",
                ("/admin/system/quotas",),
                tooltip="Speicher- und KI-Token-Quota je Organisation festlegen",
                sichtbarkeit="sysadmin",
            ),
            AdminEintrag(
                "medien_system",
                "Medienverwaltung",
                "einstellungen",
                "photo",
                "/admin/medien",
                ("/admin/medien",),
                tooltip=(
                    "Alle hochgeladenen Medien jeder Organisation: Größe, Alter, Typ – sortier- und "
                    "filterbar (Org per Umschalter wählbar)"
                ),
                sichtbarkeit="sysadmin",
            ),
        ),
        standard_offen=False,
        aktiv_praefixe=("/admin/settings", "/admin/system", "/admin/konfig"),
    ),
)


def admin_navigation(request, user) -> AdminNavigation:
    """Filtert nur die Navigation; erteilt keine Zugriffsrechte."""
    if user is None:
        return AdminNavigation((), "stammdaten", None, None)
    rollen = {rolle.code for rolle in user.roles}
    voll = bool(rollen & {"admin", "org_admin", "system_admin"})
    sysadmin = "system_admin" in rollen

    def erlaubt(element) -> bool:
        return (not element.rollen or bool(rollen.intersection(element.rollen))) and (
            not element.modulflag or bool(getattr(request.state, element.modulflag, False))
        )

    gruppen = []
    for gruppe in ADMIN_GRUPPEN:
        eintraege = []
        for eintrag in gruppe.eintraege:
            if not erlaubt(eintrag):
                continue
            if eintrag.sichtbarkeit != "alle" and not voll:
                continue
            if eintrag.sichtbarkeit == "sysadmin" and not sysadmin:
                continue
            if eintrag.sichtbarkeit == "org_admin" and sysadmin:
                continue
            # Ohne sichtbaren Sysadmin-Link bleibt Mail-Versand wie bisher aktiv.
            ausschluss = eintrag.inaktiv_praefixe
            if eintrag.id == "mail" and not sysadmin:
                ausschluss = ("/admin/mail/eingang",)
            eintraege.append(
                replace(eintrag, tabs=tuple(tab for tab in eintrag.tabs if erlaubt(tab)), inaktiv_praefixe=ausschluss)
            )
        if eintraege:
            gruppen.append(replace(gruppe, eintraege=tuple(eintraege), ueberschrift=voll))
    path = request.url.path
    aktiv = next((e for g in gruppen for e in g.eintraege if e.ist_aktiv(path)), None)
    # Auch ausgeblendete Modul-Links bestimmten bisher die Gruppenoeffnung.
    gruppen_eintrag = aktiv or next(
        (e for g in ADMIN_GRUPPEN for e in g.eintraege if e.ist_aktiv(path)), None
    )
    fallback = next(
        (g.key for g in ADMIN_GRUPPEN if any(path.startswith(p) for p in g.aktiv_praefixe)),
        ADMIN_GRUPPEN[0].key,
    )
    aktive_gruppe = (
        (gruppen_eintrag.aktive_gruppe or gruppen_eintrag.gruppe) if gruppen_eintrag else fallback
    )
    if path == "/admin/hilfe":
        aktive_gruppe = "hilfe"
    tab = next((t for g in gruppen for e in g.eintraege for t in e.tabs if t.ist_aktiv(path)), None)
    return AdminNavigation(tuple(gruppen), aktive_gruppe, aktiv, tab)
