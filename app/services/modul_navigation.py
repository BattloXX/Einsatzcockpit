"""Gemeinsame Modul-Navigation mit Sichtbarkeit und aktiven Pfaden."""

from dataclasses import dataclass

from app.core.permissions import (
    STRASSENSPERREN_LESE_ROLLEN,
    can_view_katplan_request,
    can_view_kontakt_directory_request,
    has_role,
)


@dataclass(frozen=True)
class ModulEintrag:
    key: str
    label: str
    href: str
    icon: str
    aktiv_praefixe: tuple[str, ...]

    def ist_aktiv(self, path: str) -> bool:
        return any(path.startswith(praefix) for praefix in self.aktiv_praefixe)


@dataclass(frozen=True)
class ModulGruppe:
    key: str
    titel: str
    eintraege: tuple[ModulEintrag, ...]


def modul_gruppen(request, user) -> list[ModulGruppe]:
    if user is None:
        return []

    gruppen = [
        ("einsatz", "Im Einsatz", (
            (
                ModulEintrag("katplan", "Katastrophenpläne", "/katastrophenplaene", "katplan",
                             ("/katastrophenplaene",)),
                can_view_katplan_request(request),
            ),
            (
                ModulEintrag("nachschlagewerke", "Nachschlagewerke", "/nachschlagewerke/", "nachschlagewerke",
                             ("/nachschlagewerke",)),
                getattr(request.state, "nachschlagewerke_enabled", False),
            ),
            (
                ModulEintrag("kontakte", "Kontakte", "/kontakte", "kontakte", ("/kontakte",)),
                getattr(request.state, "kontakte_enabled", False) and can_view_kontakt_directory_request(request),
            ),
            (
                ModulEintrag("strassensperren", "Straßensperren", "/strassensperren", "strassensperren",
                             ("/strassensperren",)),
                getattr(request.state, "strassensperren_enabled", False)
                and has_role(user, *STRASSENSPERREN_LESE_ROLLEN),
            ),
            (
                ModulEintrag("foerderstrecke", "Förderstrecke", "/foerderstrecke/", "foerderstrecke",
                             ("/foerderstrecke",)),
                getattr(request.state, "foerderstrecke_enabled", False),
            ),
        )),
        ("begleitend", "Einsatzbegleitend", (
            (
                ModulEintrag("fahrtenbuch", "Fahrtenbuch", "/verwaltung/fahrten", "fahrtenbuch",
                             ("/verwaltung/fahrten", "/fahrtenbuch")),
                getattr(request.state, "fahrtenbuch_modul_aktiv", False)
                and has_role(user, "fahrtenbuch_admin", "incident_leader", "recorder"),
            ),
            (
                ModulEintrag("atemschutz_pruefung", "Atemschutz-Geräteprüfung", "/atemschutz-pruefung", "atemschutz",
                             ("/atemschutz-pruefung",)),
                getattr(request.state, "atemschutz_pruefung_modul_aktiv", False),
            ),
            (
                ModulEintrag("uas", "Drohne / UAS", "/uas/einsaetze", "uas", ("/uas",)),
                getattr(request.state, "uas_module_enabled", False),
            ),
        )),
        ("verwaltung", "Verwaltung & Planung", (
            (
                ModulEintrag("objekte", "Objekte", "/objekte/", "objekte", ("/objekte",)),
                getattr(request.state, "objekt_enabled", False),
            ),
            (
                ModulEintrag("probenplan", "Probenplan", "/probenplanung", "probenplan", ("/probenplanung",)),
                getattr(request.state, "probenplanung_enabled", False),
            ),
            (
                ModulEintrag("teilnahme", "Teilnahme", "/termine", "teilnahme", ("/termine", "/teilnahme")),
                not getattr(request.state, "probenplanung_enabled", False),
            ),
            (
                ModulEintrag("planspiele", "Planspiele", "/planspiele", "planspiele", ("/planspiele",)),
                getattr(request.state, "planspiel_enabled", False)
                and has_role(user, "uebungsleiter", "uebungsbeobachter"),
            ),
            (
                ModulEintrag("mailing", "Mailing", "/mailing/", "mailing", ("/mailing",)),
                getattr(request.state, "mailing_module_enabled", False)
                and has_role(user, "mailing_admin", "mailing_sender"),
            ),
        )),
        ("auswertung", "Auswertung", (
            (
                ModulEintrag("statistik", "Statistik", "/statistik", "statistik", ("/statistik",)),
                True,
            ),
        )),
    ]

    sichtbar = []
    for key, titel, kandidaten in gruppen:
        eintraege = tuple(eintrag for eintrag, erlaubt in kandidaten if erlaubt)
        if eintraege:
            sichtbar.append(ModulGruppe(key, titel, eintraege))
    return sichtbar


def modul_aktiv(request, user) -> bool:
    return any(
        eintrag.ist_aktiv(request.url.path)
        for gruppe in modul_gruppen(request, user)
        for eintrag in gruppe.eintraege
    )
