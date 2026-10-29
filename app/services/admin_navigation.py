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
    badge_provider: str | None = None
    sichtbarkeit: Literal["voll", "alle", "sysadmin", "org_admin"] = "voll"

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
            and (
                any(tab.ist_aktiv(path) for tab in self.tabs)
                if self.tabs
                else any(path == p if self.exakt else path.startswith(p) for p in self.aktiv_praefixe)
            )
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
        eintrag = self.eintrag(eintrag_id)
        return next((t for t in eintrag.tabs if t.ist_aktiv(path)), None) if eintrag else None


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

ADMIN_GRUPPEN = (
    AdminGruppe(
        "stammdaten",
        "Stammdaten",
        (
            AdminEintrag(
                id="personal",
                label="Personal",
                gruppe="stammdaten",
                icon="users",
                href="/admin/mitglieder",
                aktiv_praefixe=("/admin/mitglieder", "/admin/qualifikationen", "/admin/gruppen"),
                tooltip="Mannschaftsliste mit Qualifikationen",
                sichtbarkeit="alle",
                tabs=(
                    AdminTab(
                        sichtbarkeit="alle",
                        id="mitglieder",
                        label="Mitglieder",
                        href="/admin/mitglieder",
                        aktiv_praefixe=("/admin/mitglieder",),
                    ),
                    AdminTab(
                        sichtbarkeit="alle",
                        id="qualifikationen",
                        label="Qualifikationen",
                        href="/admin/qualifikationen",
                        aktiv_praefixe=("/admin/qualifikationen",),
                    ),
                    AdminTab(
                        id="gruppen",
                        label="Gruppen",
                        href="/admin/gruppen",
                        aktiv_praefixe=("/admin/gruppen",),
                        rollen=("admin", "org_admin", "system_admin"),
                    ),
                ),
            ),
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
                id="alarmstichwoerter",
                label="Alarmstichwörter",
                gruppe="stammdaten",
                icon="urgent",
                href="/admin/alarmtypen",
                aktiv_praefixe=(
                    "/admin/alarmtypen",
                    "/admin/ausrueckordnung",
                    "/admin/auftragsvorlagen",
                    "/admin/meldungsvorlagen",
                    "/admin/lage-hinweise",
                    "/admin/default-meldungen",
                ),
                tooltip="Bekannte Einsatzstichwörter und ihre Eigenschaften",
                tabs=(
                    AdminTab(
                        id="alarmtypen",
                        label="Alarmtypen",
                        href="/admin/alarmtypen",
                        aktiv_praefixe=("/admin/alarmtypen",),
                    ),
                    AdminTab(
                        id="ausrueckordnung",
                        label="Ausrückordnung",
                        href="/admin/ausrueckordnung",
                        aktiv_praefixe=("/admin/ausrueckordnung",),
                    ),
                    AdminTab(
                        id="auftragsvorlagen",
                        label="Auftragsvorlagen",
                        href="/admin/auftragsvorlagen",
                        aktiv_praefixe=("/admin/auftragsvorlagen",),
                    ),
                    AdminTab(
                        id="meldungsvorlagen",
                        label="Meldungsvorlagen",
                        href="/admin/meldungsvorlagen",
                        aktiv_praefixe=("/admin/meldungsvorlagen",),
                    ),
                    AdminTab(
                        id="lage_hinweise",
                        label="Lage-Hinweise",
                        href="/admin/lage-hinweise",
                        aktiv_praefixe=("/admin/lage-hinweise",),
                    ),
                    AdminTab(
                        id="default_meldungen",
                        label="Default-Meldungen",
                        href="/admin/default-meldungen",
                        aktiv_praefixe=("/admin/default-meldungen",),
                    ),
                ),
            ),
        ),
    ),
    AdminGruppe(
        "verwaltung",
        "Verwaltung",
        (
            AdminEintrag(
                id="benutzer",
                label="Benutzer & Rechte",
                gruppe="verwaltung",
                icon="user-cog",
                href="/admin/benutzer",
                aktiv_praefixe=("/admin/benutzer", "/admin/audit"),
                tooltip="Login-Accounts, Rollen, Passwörter",
                tabs=(
                    AdminTab(
                        id="benutzer", label="Benutzer", href="/admin/benutzer", aktiv_praefixe=("/admin/benutzer",)
                    ),
                    AdminTab(id="audit", label="Audit-Log", href="/admin/audit", aktiv_praefixe=("/admin/audit",)),
                ),
            ),
            AdminEintrag(
                id="probenplanung",
                label="Probenplanung",
                gruppe="verwaltung",
                icon="calendar-event",
                href="/admin/probenplanung/probearten",
                aktiv_praefixe=(
                    "/admin/probenplanung/probearten",
                    "/admin/probenplanung/vorlagen",
                    "/admin/probenplanung/oeffentlich",
                ),
                modulflag="probenplanung_enabled",
                tabs=(
                    AdminTab(
                        id="probenplanung_probearten",
                        label="Probearten",
                        href="/admin/probenplanung/probearten",
                        aktiv_praefixe=("/admin/probenplanung/probearten",),
                    ),
                    AdminTab(
                        id="probenplanung_vorlagen",
                        label="Checklisten-Vorlagen",
                        href="/admin/probenplanung/vorlagen",
                        aktiv_praefixe=("/admin/probenplanung/vorlagen",),
                    ),
                    AdminTab(
                        id="probenplanung_oeffentlich",
                        label="Öffentlicher Plan",
                        href="/admin/probenplanung/oeffentlich",
                        aktiv_praefixe=("/admin/probenplanung/oeffentlich",),
                    ),
                ),
            ),
            AdminEintrag(
                id="infoscreens",
                label="Infoscreens",
                gruppe="verwaltung",
                icon="device-tv",
                href="/infoscreen-alarm/verwaltung",
                aktiv_praefixe=(
                    "/infoscreen-alarm",
                    "/admin/settings/wetter/infoscreen",
                    "/admin/settings/statistik",
                    "/admin/settings/strassensperren-infoscreen",
                ),
                tooltip="Alarm-, Wetter- und Statistik-Infoscreens verwalten",
                tabs=(
                    AdminTab(
                        id="verwaltung",
                        label="Alarm-Infoscreen",
                        href="/infoscreen-alarm/verwaltung",
                        aktiv_praefixe=("/infoscreen-alarm",),
                    ),
                    AdminTab(
                        id="infoscreen",
                        label="Wetter-Infoscreen",
                        href="/admin/settings/wetter/infoscreen",
                        aktiv_praefixe=("/admin/settings/wetter/infoscreen",),
                    ),
                    AdminTab(
                        id="statistik",
                        label="Statistik",
                        href="/admin/settings/statistik",
                        aktiv_praefixe=("/admin/settings/statistik",),
                    ),
                    AdminTab(
                        id="strassensperren_infoscreen",
                        label="Straßensperren",
                        href="/admin/settings/strassensperren-infoscreen",
                        aktiv_praefixe=("/admin/settings/strassensperren-infoscreen",),
                        modulflag="strassensperren_enabled",
                    ),
                ),
            ),
            AdminEintrag(
                id="alarmierung",
                label="Alarmierung",
                gruppe="verwaltung",
                icon="mail",
                href="/admin/sms-senden",
                aktiv_praefixe=(
                    "/admin/sms-senden",
                    "/admin/sms-provider",
                    "/admin/sms-empfang",
                    "/admin/einsatzinfo-sms",
                    "/admin/einsatzinfo-mail",
                    "/admin/teams-alarmierung",
                    "/admin/push-nachrichten",
                ),
                tooltip="SMS, Einsatzinfo-Mail, Teams-Alarmierung und Push-Nachrichten verwalten",
                tabs=(
                    AdminTab(
                        id="sms_senden",
                        label="SMS senden",
                        href="/admin/sms-senden",
                        aktiv_praefixe=("/admin/sms-senden",),
                        exakt=True,
                    ),
                    AdminTab(
                        id="sms_provider",
                        label="SMS-Versandweg",
                        href="/admin/sms-provider",
                        aktiv_praefixe=("/admin/sms-provider",),
                        exakt=True,
                    ),
                    AdminTab(
                        id="sms_empfang",
                        label="SMS-Empfang",
                        href="/admin/sms-empfang",
                        aktiv_praefixe=("/admin/sms-empfang",),
                        exakt=True,
                    ),
                    AdminTab(
                        id="einsatzinfo_sms",
                        label="SMS-Einsatzinfo",
                        href="/admin/einsatzinfo-sms",
                        aktiv_praefixe=("/admin/einsatzinfo-sms",),
                        exakt=True,
                    ),
                    AdminTab(
                        id="einsatzinfo_mail",
                        label="Einsatzinfo-Mail",
                        href="/admin/einsatzinfo-mail",
                        aktiv_praefixe=("/admin/einsatzinfo-mail",),
                    ),
                    AdminTab(
                        id="teams_alarmierung",
                        label="Teams-Alarmierung",
                        href="/admin/teams-alarmierung",
                        aktiv_praefixe=("/admin/teams-alarmierung",),
                    ),
                    AdminTab(
                        id="push_nachrichten",
                        label="Push-Nachrichten",
                        href="/admin/push-nachrichten",
                        aktiv_praefixe=("/admin/push-nachrichten",),
                    ),
                ),
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
            AdminEintrag(
                id="einsatzdaten",
                label="Einsatzdaten",
                gruppe="verwaltung",
                icon="inbox",
                href="/admin/einsatz-import",
                aktiv_praefixe=("/admin/einsatz-import",),
                tooltip="Historische Leitstellen-Excel-Exporte importieren",
            ),
            AdminEintrag(
                id="loeschwasser",
                label="Löschwasserversorgung",
                gruppe="verwaltung",
                icon="droplet",
                href="/admin/wasserstellen",
                aktiv_praefixe=("/admin/wasserstellen",),
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
                id="verleih",
                label="Geräteverleih",
                gruppe="verwaltung",
                icon="package",
                href="/admin/verleih-artikel",
                aktiv_praefixe=(
                    "/admin/verleih-artikel",
                    "/admin/verleih-stuecklisten",
                    "/admin/verleih-uebersicht",
                    "/admin/verleih-geraetetypen",
                ),
                tooltip="Geräteverleih Artikelstammdaten und Stücklisten",
                tabs=(
                    AdminTab(
                        id="artikel",
                        label="Artikel",
                        href="/admin/verleih-artikel",
                        aktiv_praefixe=("/admin/verleih-artikel",),
                    ),
                    AdminTab(
                        id="stuecklisten",
                        label="Stücklisten",
                        href="/admin/verleih-stuecklisten",
                        aktiv_praefixe=("/admin/verleih-stuecklisten",),
                    ),
                    AdminTab(
                        id="uebersicht",
                        label="Übersicht",
                        href="/admin/verleih-uebersicht",
                        aktiv_praefixe=("/admin/verleih-uebersicht",),
                    ),
                    AdminTab(
                        id="geraetetypen",
                        label="Gerätetypen",
                        href="/admin/verleih-geraetetypen",
                        aktiv_praefixe=("/admin/verleih-geraetetypen",),
                    ),
                ),
            ),
            AdminEintrag(
                id="medien",
                label="Dateien & Medien",
                gruppe="verwaltung",
                icon="photo",
                href="/admin/medien",
                aktiv_praefixe=("/admin/medien",),
                tooltip="Alle hochgeladenen Medien: Größe, Alter, Typ – sortierbar und filterbar",
                sichtbarkeit="org_admin",
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
                id="schnittstellen",
                label="Schnittstellen",
                gruppe="einstellungen",
                icon="key",
                href="/admin/sso",
                aktiv_praefixe=(
                    "/admin/lis",
                    "/admin/dibos",
                    "/admin/sso",
                    "/admin/mail",
                    "/admin/mail/eingang",
                    "/admin/wordpress-berichte",
                ),
                tooltip="LIS, DIBOS, Microsoft SSO, Mail und WordPress konfigurieren",
                tabs=(
                    AdminTab(id="lis", label="LIS", href="/admin/lis", aktiv_praefixe=("/admin/lis",)),
                    AdminTab(id="dibos", label="DIBOS", href="/admin/dibos", aktiv_praefixe=("/admin/dibos",)),
                    AdminTab(id="sso", label="Microsoft SSO", href="/admin/sso", aktiv_praefixe=("/admin/sso",)),
                    AdminTab(
                        id="mail", label="Mail-Versand", href="/admin/mail", aktiv_praefixe=("/admin/mail",), exakt=True
                    ),
                    AdminTab(
                        id="mail_eingang",
                        label="Posteingang",
                        href="/admin/mail/eingang",
                        aktiv_praefixe=("/admin/mail/eingang",),
                        badge_provider="mail_eingang_offene_count",
                    ),
                    AdminTab(
                        id="wordpress_berichte",
                        label="WordPress",
                        href="/admin/wordpress-berichte",
                        aktiv_praefixe=("/admin/wordpress-berichte",),
                    ),
                ),
            ),
            AdminEintrag(
                id="strassensperren",
                label="Straßensperren",
                gruppe="einstellungen",
                icon="message",
                href="/strassensperren/einstellungen",
                aktiv_praefixe=("/strassensperren/einstellungen",),
                tooltip="Microsoft-Teams-Benachrichtigungen für Straßensperren konfigurieren",
                modulflag="strassensperren_enabled",
                tabs=(
                    AdminTab(
                        id="strassensperren_einstellungen",
                        label="Teams-Benachrichtigungen",
                        href="/strassensperren/einstellungen",
                        aktiv_praefixe=("/strassensperren/einstellungen",),
                    ),
                    AdminTab(
                        id="infoscreen_zugaenge",
                        label="Infoscreen & Zugänge",
                        href="/admin/settings/strassensperren-infoscreen",
                        aktiv_praefixe=(),
                    ),
                ),
            ),
            AdminEintrag(
                id="planspiele",
                label="Planspiele",
                gruppe="einstellungen",
                icon="settings",
                href="/admin/settings/planspiele",
                aktiv_praefixe=(
                    "/admin/settings/planspiele",
                    "/admin/settings/planspiele/szenarien",
                    "/admin/settings/planspiele/stichworte",
                ),
                rollen=("org_admin", "admin", "system_admin", "uebungsleiter"),
                modulflag="planspiel_enabled",
                tabs=(
                    AdminTab(
                        id="settings_planspiele",
                        label="Einstellungen & Adresspool",
                        href="/admin/settings/planspiele",
                        aktiv_praefixe=("/admin/settings/planspiele",),
                        exakt=True,
                    ),
                    AdminTab(
                        id="settings_planspiele_szenarien",
                        label="Szenarien",
                        href="/admin/settings/planspiele/szenarien",
                        aktiv_praefixe=("/admin/settings/planspiele/szenarien",),
                    ),
                    AdminTab(
                        id="settings_planspiele_stichworte",
                        label="Stichwort-Regeln",
                        href="/admin/settings/planspiele/stichworte",
                        aktiv_praefixe=("/admin/settings/planspiele/stichworte",),
                    ),
                ),
            ),
            AdminEintrag(
                id="ki",
                label="KI & MCP",
                gruppe="einstellungen",
                icon="KI",
                href="/admin/ki-anfragen",
                aktiv_praefixe=("/admin/ki-anfragen", "/admin/ki-prompts"),
                tooltip="Tokenverbrauch und Fehler der KI-Anfragen anzeigen",
                tabs=(
                    AdminTab(
                        id="ki_anfragen",
                        label="KI-Anfragen",
                        href="/admin/ki-anfragen",
                        aktiv_praefixe=("/admin/ki-anfragen",),
                    ),
                    AdminTab(
                        id="ki_prompts",
                        label="KI-Prompts",
                        href="/admin/ki-prompts",
                        aktiv_praefixe=("/admin/ki-prompts",),
                        rollen=("system_admin",),
                    ),
                ),
                icon_art="text",
            ),
            AdminEintrag(
                id="org_backup",
                label="Datensicherung",
                gruppe="einstellungen",
                icon="device-floppy",
                href="/admin/org-backup",
                aktiv_praefixe=("/admin/org-backup",),
                tooltip="Alle Org-Daten als Archiv sichern (Download / Remote-Ziel)",
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
        if element.sichtbarkeit != "alle" and not voll:
            return False
        if element.sichtbarkeit == "sysadmin" and not sysadmin:
            return False
        if element.sichtbarkeit == "org_admin" and sysadmin:
            return False
        return (not element.rollen or bool(rollen.intersection(element.rollen))) and (
            not element.modulflag or bool(getattr(request.state, element.modulflag, False))
        )

    gruppen = []
    for gruppe in ADMIN_GRUPPEN:
        eintraege = []
        for eintrag in gruppe.eintraege:
            if not erlaubt(eintrag):
                continue
            tabs = tuple(tab for tab in eintrag.tabs if erlaubt(tab))
            if eintrag.tabs and not tabs:
                continue
            eintraege.append(
                replace(
                    eintrag,
                    tabs=tabs,
                    aktiv_praefixe=tuple(p for tab in tabs for p in tab.aktiv_praefixe)
                    if tabs
                    else eintrag.aktiv_praefixe,
                    href=eintrag.href if not tabs or any(tab.href == eintrag.href for tab in tabs) else tabs[0].href,
                )
            )
        if eintraege:
            gruppen.append(replace(gruppe, eintraege=tuple(eintraege), ueberschrift=voll))
    path = request.url.path
    aktiv = next((e for g in gruppen for e in g.eintraege if e.ist_aktiv(path)), None)
    # Auch ausgeblendete Modul-Links bestimmten bisher die Gruppenoeffnung.
    gruppen_eintrag = aktiv or next((e for g in ADMIN_GRUPPEN for e in g.eintraege if e.ist_aktiv(path)), None)
    fallback = next(
        (g.key for g in ADMIN_GRUPPEN if any(path.startswith(p) for p in g.aktiv_praefixe)),
        ADMIN_GRUPPEN[0].key,
    )
    aktive_gruppe = (gruppen_eintrag.aktive_gruppe or gruppen_eintrag.gruppe) if gruppen_eintrag else fallback
    if path == "/admin/hilfe":
        aktive_gruppe = "hilfe"
    tab = next((t for g in gruppen for e in g.eintraege for t in e.tabs if t.ist_aktiv(path)), None)
    return AdminNavigation(tuple(gruppen), aktive_gruppe, aktiv, tab)
