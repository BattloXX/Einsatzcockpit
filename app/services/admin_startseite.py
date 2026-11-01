"""Inhalte der Verwaltungs-Startseite: Einrichtungsreihenfolge und Wiki-Verweise.

Die Wiki-Seiten liegen unter ``docs/wiki``; ``tests/test_admin_startseite.py``
prueft, dass jede hier genannte Seite existiert.
"""

from dataclasses import dataclass
from typing import Literal

WIKI_BASIS = "https://github.com/BattloXX/Einsatzcockpit/wiki"


@dataclass(frozen=True)
class EinrichtungsSchritt:
    titel: str
    beschreibung: str
    href: str
    wiki: str
    sichtbarkeit: Literal["alle", "sysadmin"] = "alle"


@dataclass(frozen=True)
class WikiLink:
    label: str
    slug: str
    icon: str


@dataclass(frozen=True)
class WikiBereich:
    titel: str
    icon: str
    links: tuple[WikiLink, ...]
    sichtbarkeit: Literal["alle", "sysadmin"] = "alle"


EINRICHTUNG: tuple[EinrichtungsSchritt, ...] = (
    EinrichtungsSchritt(
        "Organisation einrichten",
        "Stammdaten, Logo, Module und Auto-Schließen festlegen.",
        "/admin/settings",
        "Administration-Einstellungen",
    ),
    EinrichtungsSchritt(
        "Benutzer und Rollen",
        "Benutzer einladen, Rollen vergeben, Mitglieder verknüpfen.",
        "/admin/benutzer",
        "Administration-Benutzer-und-Rollen",
    ),
    EinrichtungsSchritt(
        "Stammdaten pflegen",
        "Fahrzeuge, Alarmtypen und Auftragsvorschläge anlegen.",
        "/admin/vehicles",
        "Administration-Stammdaten-pflegen",
    ),
    EinrichtungsSchritt(
        "Alarmierung anbinden",
        "Leitstelle, SMS, Teams, Push und Druck-Gateway einrichten.",
        "/admin/sms-provider",
        "Administration-SMS-Versandweg",
    ),
    EinrichtungsSchritt(
        "Infoscreens und Wetter",
        "Alarm-Infoscreen, Wetterwarnungen und Wetterstation.",
        "/infoscreen-alarm/verwaltung",
        "Administration-Alarm-Infoscreen",
    ),
    EinrichtungsSchritt(
        "Absichern",
        "Zwei-Faktor-Authentifizierung, Diagnose und Datensicherung.",
        "/admin/system-einstellungen",
        "Administration-System-Admin-Ueberblick",
        "sysadmin",
    ),
)

WIKI_BEREICHE: tuple[WikiBereich, ...] = (
    WikiBereich(
        "Erste Schritte",
        "rocket",
        (
            WikiLink("Erste Schritte", "Anwender-Erste-Schritte", "book"),
            WikiLink("Einsatz starten", "Anwender-Einsatz-starten", "urgent"),
            WikiLink("Einsatzboard", "Anwender-Kanban-Board-bedienen", "folders"),
            WikiLink("Mobile Nutzung / PWA", "Anwender-Mobile-Nutzung-PWA", "device-mobile"),
            WikiLink("Native Android-App", "Anwender-Native-Android-App", "device-mobile"),
            WikiLink("Feedback und Support", "Feedback-und-Support", "message"),
        ),
    ),
    WikiBereich(
        "Grundeinrichtung",
        "settings",
        (
            WikiLink("Verwaltungsmenü", "Administration-Navigation", "layout-dashboard"),
            WikiLink("Einstellungen", "Administration-Einstellungen", "settings"),
            WikiLink("Benutzer und Rollen", "Administration-Benutzer-und-Rollen", "users"),
            WikiLink("Stammdaten pflegen", "Administration-Stammdaten-pflegen", "truck"),
            WikiLink("Mannschaftsregister", "Anwender-Mannschaftsregister", "users-group"),
            WikiLink("Single Sign-On", "Administration-Single-Sign-On", "key"),
            WikiLink("Organisationen verwalten", "Administration-Organisations-verwalten", "building"),
        ),
    ),
    WikiBereich(
        "Alarmierung und Leitstelle",
        "antenna-bars-5",
        (
            WikiLink("LIS/IPR-Anbindung", "Administration-LIS-Anbindung", "plug-connected"),
            WikiLink("DIBOS/Elvis", "Administration-DIBOS-Elvis-Anbindung", "activity"),
            WikiLink("Print und Alarm Gateway", "Administration-Print-Alarm-Gateway", "printer"),
            WikiLink("SMS-Einsatzinfo", "Administration-SMS-Einsatzinfo", "message-share"),
            WikiLink("SMS-Versandweg", "Administration-SMS-Versandweg", "send"),
            WikiLink("Teams-Alarmierung", "Administration-Teams-Alarmierung", "brand-teams"),
            WikiLink("Mail-Versand", "Administration-Mail-Versand", "mail"),
            WikiLink("Push mit FCM", "Administration-Push-FCM", "bell"),
            WikiLink("Alarm-Infoscreen", "Administration-Alarm-Infoscreen", "device-desktop"),
        ),
    ),
    WikiBereich(
        "Module einrichten",
        "apps",
        (
            WikiLink("Einsatz- und Katastrophenpläne", "Administration-Katastrophenplaene", "book"),
            WikiLink("Nachschlagewerke", "Administration-Nachschlagewerke", "book"),
            WikiLink("Kontakte", "Administration-Kontaktverwaltung", "address-book"),
            WikiLink("Straßensperren", "Administration-Strassensperren", "road"),
            WikiLink("Objekte", "Administration-Objektverwaltung", "building"),
            WikiLink("BSP-Overlay", "Administration-BSP-Overlay", "map"),
            WikiLink("Fahrtenbuch", "Administration-Fahrtenbuch", "truck"),
            WikiLink("Förderstrecke", "Administration-Foerderstrecken-Planer", "droplet"),
            WikiLink("Geräteverleih", "Administration-Geraeteverleih", "package"),
            WikiLink("Planspiele", "Administration-Planspiele", "target"),
            WikiLink("Mailing", "Administration-Mailing-Modul", "mail"),
            WikiLink("Drohne / UAS", "Administration-Drohne-UAS", "drone"),
            WikiLink("Atemschutz-Geräteprüfung", "Administration-Atemschutz-Geraetepruefung", "lungs"),
            WikiLink("Statistik", "Administration-Statistik-Dashboard", "chart-bar"),
        ),
    ),
    WikiBereich(
        "Großschadenslage",
        "home-x",
        (
            WikiLink("Großschadenslage", "Anwender-Grosschadenslage", "home-x"),
            WikiLink("GSL-Einstellungen", "Administration-GSL-Einstellungen", "settings"),
            WikiLink("GSL-Ressourcenverwaltung", "Anwender-GSL-Ressourcenverwaltung", "users"),
            WikiLink("GSL-Einheitenansicht", "Anwender-GSL-Einheitenansicht", "device-desktop"),
            WikiLink("Übergreifende Meldungen", "Anwender-Uebergreifende-Meldungen", "speakerphone"),
        ),
    ),
    WikiBereich(
        "Schnittstellen und KI",
        "plug",
        (
            WikiLink("API-Keys", "Administration-API-Keys-verwalten", "key"),
            WikiLink("Einsatz-Feed", "Administration-Einsatz-Feed", "antenna"),
            WikiLink("Nachrichten-API", "Administration-Nachrichten-API", "send"),
            WikiLink("MCP-Server", "Administration-MCP-Server", "plug"),
            WikiLink("MCP-Assistent", "Anwender-MCP-Assistent", "sparkles"),
            WikiLink("WordPress-Berichte", "Administration-WordPress-Berichte", "brand-wordpress"),
            WikiLink("Zentrale Suche", "Administration-Suche", "search"),
        ),
    ),
    WikiBereich(
        "Sicherheit und Betrieb",
        "shield-lock",
        (
            WikiLink("System-Admin-Überblick", "Administration-System-Admin-Ueberblick", "shield-lock"),
            WikiLink("Systemdiagnose", "Administration-Systemdiagnose", "traffic-lights"),
            WikiLink("System-Admin-MCP", "Administration-System-Admin-MCP", "plug"),
            WikiLink("Zwei-Faktor-Authentifizierung", "Administration-Zwei-Faktor-Authentifizierung", "shield"),
            WikiLink("Systemstatus und Überwachung", "Administration-Dienstueberwachung", "activity"),
            WikiLink("Audit-Log und Zeitreise", "Administration-Audit-Log-und-Zeitreise", "history"),
            WikiLink("Datensicherung der Organisation", "Administration-Org-Datensicherung", "archive"),
            WikiLink("Backup und Disaster-Recovery", "Betrieb-Backup-und-Disaster-Recovery", "archive"),
            WikiLink("Updates", "Installation-Updates", "tools"),
            WikiLink("Troubleshooting", "Installation-Troubleshooting", "tools"),
        ),
        "sysadmin",
    ),
)


def wiki_url(slug: str) -> str:
    return f"{WIKI_BASIS}/{slug}"


def einrichtung_fuer(sysadmin: bool) -> tuple[EinrichtungsSchritt, ...]:
    return tuple(s for s in EINRICHTUNG if sysadmin or s.sichtbarkeit == "alle")


def wiki_bereiche_fuer(sysadmin: bool) -> tuple[WikiBereich, ...]:
    return tuple(b for b in WIKI_BEREICHE if sysadmin or b.sichtbarkeit == "alle")
