"""Statischer Systemkatalog fuer Planspiele."""
# ruff: noqa: E501

from dataclasses import dataclass

SYSTEM_KATALOG_VERSION = 1

ORT_TYPEN: dict[str, str] = {
    "wohngebaeude": "Wohngebäude", "wohnung": "Wohnung", "mehrfamilienhaus": "Mehrfamilienhaus",
    "wohngebaeude_gross": "Großes Wohngebäude", "gebaeude": "Gebäude", "gebaeude_gross": "Großes Gebäude",
    "garage": "Garage", "tiefgarage": "Tiefgarage", "gewerbe": "Gewerbe", "industrie": "Industrie",
    "landwirtschaft": "Landwirtschaft", "sonderobjekt": "Sonderobjekt", "objekt_bma": "Objekt mit BMA",
    "strasse": "Straße", "strasse_geeignet": "Geeignete Straße", "verkehrsflaeche": "Verkehrsfläche",
    "parkplatz": "Parkplatz", "wohngebiet": "Wohngebiet", "wohn_gruengebiet": "Wohn- und Grüngebiet",
    "gelaende": "Gelände", "hang": "Hang", "baustelle": "Baustelle", "bau_betriebsgelaende": "Bau- oder Betriebsgelände",
    "gewaessernaehe": "Gewässernähe", "gewaesser": "Gewässer", "gewaesser_boot": "Gewässer für Boote",
    "boot": "Boot", "gleisbereich": "Gleisbereich", "ortsgebiet": "Ortsgebiet", "nachbargebiet": "Nachbargebiet",
    "bestehender_einsatz": "Bestehender Einsatz", "beliebig": "Beliebig", "beliebig_geeignet": "Beliebig geeignet",
}

TEMPLATE_VARIABLEN = frozenset({
    "adresse", "strasse", "hausnr", "ort", "objekt", "gebaeudeart", "meldender", "meldezeit", "stock",
    "bereich", "ursache", "gruppe", "hindernis", "geraet", "material", "einheit", "neu", "ortsteil",
})


@dataclass(frozen=True)
class Baustein:
    code: str
    typ: str
    text: str
    trigger_type: str
    offset_min_s: int | None
    offset_max_s: int | None
    trigger_param: dict[str, str | bool] | None
    wahrscheinlichkeit: float
    ziel: str
    sichtbarkeit: str
    variablen: dict[str, tuple[str, ...]]


def _b(code: str, typ: str, text: str, trigger: str, minimum: int | None = None,
       maximum: int | None = None, param: dict[str, str | bool] | None = None,
       p: float = 1.0, variablen: dict[str, tuple[str, ...]] | None = None) -> Baustein:
    return Baustein(code, typ, text, trigger, minimum, maximum, param, p, "stelle", "teilnehmer", variablen or {})


BAUSTEINE: dict[str, Baustein] = {
    "AUF-URSACHE": _b("AUF-URSACHE", "aufklaerung", "Ursache bestätigt: {ursache}", "assignment_completed", param={"auftragsart": "erkundung"}, variablen={"ursache": ("angebranntes Kochgut", "technischer Defekt", "Arbeiten mit offenem Feuer")} ),
    "AUF-FEHL": _b("AUF-FEHL", "aufklaerung", "Keine Gefahr feststellbar / Fehlalarm", "assignment_completed", param={"auftragsart": "erkundung"}, p=0.3),
    "AUF-MELDERGR": _b("AUF-MELDERGR", "aufklaerung", "BMA: Meldergruppe {gruppe} ausgelöst, Bereich {bereich}", "elapsed_time", 120, 120, variablen={"gruppe": ("12", "24", "37"), "bereich": ("Erdgeschoss", "Technikraum", "Lagerbereich")} ),
    "AUF-WARTUNG": _b("AUF-WARTUNG", "aufklaerung", "Betreiber meldet Wartungsarbeiten im Bereich", "phase_reached", param={"phase": "erkundung"}),
    "ESK-STIEGENHAUS": _b("ESK-STIEGENHAUS", "eskalation", "Stiegenhaus zunehmend verraucht", "elapsed_time", 180, 300),
    "ESK-PERS-BALKON": _b("ESK-PERS-BALKON", "eskalation", "Person am Balkon {stock} sichtbar", "phase_reached", param={"phase": "erkundung"}, variablen={"stock": ("1. Obergeschoss", "2. Obergeschoss", "Dachgeschoss")} ),
    "ESK-NACHBAR": _b("ESK-NACHBAR", "eskalation", "Rauch dringt in Nachbarwohnung", "elapsed_time", 600, 900),
    "ESK-AUSBREITUNG": _b("ESK-AUSBREITUNG", "eskalation", "Brand breitet sich auf {bereich} aus", "elapsed_time", 480, 720, variablen={"bereich": ("Dachbereich", "angrenzende Wohnung", "Fassade")} ),
    "ESK-WEITERE-PERS": _b("ESK-WEITERE-PERS", "eskalation", "Weitere betroffene Person gemeldet", "elapsed_time", 240, 240),
    "ESK-STICHWORT": _b("ESK-STICHWORT", "eskalation", "Lageänderung – Vorschlag Stichwort {neu} (Regie entscheidet)", "manual", variablen={"neu": ("F3", "F4", "T3", "T4")} ),
    "ESK-FAHRZEUGLAGE": _b("ESK-FAHRZEUGLAGE", "eskalation", "Fahrzeug instabil / schwierige Lage", "phase_reached", param={"phase": "in_arbeit"}),
    "ESK-PEGEL": _b("ESK-PEGEL", "eskalation", "Bachpegel steigt deutlich", "elapsed_time", 300, 900),
    "ESK-WEITERE-KELLER": _b("ESK-WEITERE-KELLER", "eskalation", "Weitere Keller in {strasse} betroffen", "elapsed_time", 600, 600),
    "ESK-GEFAHRBEREICH": _b("ESK-GEFAHRBEREICH", "eskalation", "Wind dreht – Gefahrenbereich anpassen", "elapsed_time", 600, 600),
    "ESK-HANGBEWEGUNG": _b("ESK-HANGBEWEGUNG", "eskalation", "Erneute Bewegung im Hang beobachtet", "elapsed_time", 600, 1200),
    "KOM-ZUFAHRT": _b("KOM-ZUFAHRT", "komplikation", "Zufahrt blockiert durch {hindernis}", "elapsed_time", 480, 480, variablen={"hindernis": ("parkende Fahrzeuge", "umgestürzten Baum", "Baustellenfahrzeug")} ),
    "KOM-GERAET": _b("KOM-GERAET", "komplikation", "Ausfall {geraet} gemeldet", "manual", variablen={"geraet": ("Tauchpumpe", "Atemschutzgerät", "hydraulisches Rettungsgerät")} ),
    "KOM-FUNK": _b("KOM-FUNK", "komplikation", "Funkverbindung schlecht, Rückfrage erforderlich", "elapsed_time", 300, 300),
    "KOM-WIDERSPRUCH": _b("KOM-WIDERSPRUCH", "komplikation", "Widersprüchliche Angabe zur Personenzahl", "elapsed_time", 240, 240),
    "KOM-STROM-GAS": _b("KOM-STROM-GAS", "komplikation", "Strom/Gas noch nicht abgeschaltet", "phase_reached", param={"phase": "erkundung"}),
    "KOM-LOESCHWASSER": _b("KOM-LOESCHWASSER", "komplikation", "Hydrant ohne ausreichenden Druck", "elapsed_time", 600, 600),
    "KOM-TIERE": _b("KOM-TIERE", "komplikation", "Tiere im Gebäude/Stall", "phase_reached", param={"phase": "erkundung"}),
    "KOM-GEBINDE": _b("KOM-GEBINDE", "komplikation", "Unbekannte Gebinde aufgefunden", "phase_reached", param={"phase": "erkundung"}),
    "KOM-SCHAULUSTIGE": _b("KOM-SCHAULUSTIGE", "komplikation", "Schaulustige behindern Einsatzstelle", "elapsed_time", 600, 600),
    "ENT-EINGEGRENZT": _b("ENT-EINGEGRENZT", "entspannung", "Brand eingegrenzt (nur nach Maßnahme)", "assignment_completed", param={"auftragsart": "abarbeitung"}),
    "ENT-PERS-GEFUNDEN": _b("ENT-PERS-GEFUNDEN", "entspannung", "Vermisste Person gefunden, in Sicherheit", "manual"),
    "ENT-PERS-BEFREIT": _b("ENT-PERS-BEFREIT", "entspannung", "Person befreit (nur Regie/nach gemeldeter Maßnahme)", "manual"),
    "ENT-KEINE-GEFAHR": _b("ENT-KEINE-GEFAHR", "entspannung", "Keine weitere Person gefährdet", "manual"),
    "ENT-PEGEL": _b("ENT-PEGEL", "entspannung", "Wasserstand sinkt / Niederschlag lässt nach", "elapsed_time", 1800, 1800),
    "ENT-WIND": _b("ENT-WIND", "entspannung", "Sturm flaut ab", "elapsed_time", 1800, 1800),
    "LAG-WETTER": _b("LAG-WETTER", "lageweit", "Neue Unwetterwarnung / Wetterfront", "elapsed_time", 300, 600),
    "LAG-SPERRE": _b("LAG-SPERRE", "lageweit", "{strasse} nicht passierbar (Übungs-Marker)", "elapsed_time", 1200, 1200),
    "LAG-SAMMELSTELLE": _b("LAG-SAMMELSTELLE", "lageweit", "Sammelstelle {ort} eingerichtet", "manual"),
    "LAG-ANRUFE": _b("LAG-ANRUFE", "lageweit", "Vermehrt Anrufe aus {ortsteil}", "elapsed_time", 600, 600, variablen={"ortsteil": ("Ortskern", "Nord", "Siedlung")} ),
    "FUNK-EINTREFFEN": _b("FUNK-EINTREFFEN", "funk", "{einheit}: an Einsatzstelle eingetroffen", "assignment_created", 120, 120, {"nur_simuliert": True}),
    "FUNK-MATERIAL": _b("FUNK-MATERIAL", "funk", "{einheit}: benötigt {material}", "manual", param={"nur_simuliert": True}, variablen={"material": ("Bindemittel", "Tauchpumpe", "Schaummittel")} ),
    "DUP-ANRUF": _b("DUP-ANRUF", "duplikat", "Zweite Meldung zu {adresse}, abweichende Schilderung", "elapsed_time", 120, 600, p=0.3),
}


@dataclass(frozen=True)
class SystemSzenario:
    code: str
    stichwort: str
    titel: str
    alarmtext_template: str
    kategorie: str
    ort_typen: tuple[str, ...]
    schwierigkeit: str
    bausteine: tuple[str, ...]
    zufall_erlaubt: bool = True
    requires_existing_object: bool = False
    requires_verified_bma: bool = False
    geografie_bedingung: dict[str, str] | None = None
    gsl_modus: str | None = None
    text_overrides: dict[str, str] | None = None
    lernziele: str = "Lage erkunden und Maßnahmen koordinieren."


# Anonyme Melderbezeichnungen fuer {meldender}; nie reale Namen.
MELDENDE: tuple[str, ...] = (
    "Anrufer", "Anruferin", "Nachbar", "Nachbarin", "Passant", "Passantin", "Bewohner", "Bewohnerin",
    "Hausverwaltung", "Betriebsleiter", "Lenker eines vorbeifahrenden PKW", "Polizei",
)

# Leitstellennahe Alarmtexte je Szenario. Platzhalter: {adresse}, {objekt}, {meldender}.
_ALARMTEXTE: dict[str, str] = {
    "B01": "Brandgeruch im Stiegenhaus, Ursache unklar, kein Feuer sichtbar. {adresse}. Meldung: {meldender}.",
    "B02": "Küchenbrand laut Bewohner selbst gelöscht, Wohnung verraucht, Kontrolle erbeten. {adresse}. Meldung: {meldender}.",
    "B03": "Rauchentwicklung nach Schweiß-/Flexarbeiten im Betrieb, Ursache unklar. {adresse}. Meldung: {meldender}.",
    "B04": "Müllcontainer brennt, steht direkt neben Gebäudewand. {adresse}. Meldung: {meldender}.",
    "B05": "Hecke brennt an der Grundstücksgrenze, Flammen gut sichtbar. {adresse}. Meldung: {meldender}.",
    "B06": "PKW brennt, freistehend, keine Personen im Fahrzeug laut Melder. {adresse}. Meldung: {meldender}.",
    "B07": "Kaminbrand, Funkenflug aus dem Kamin, Bewohner im Haus. {adresse}. Meldung: {meldender}.",
    "B08": "Rauchwarnmelder piepst in Wohnung, niemand öffnet, Lage unklar. {adresse}. Meldung: {meldender}.",
    "B09": "Zimmerbrand im Mehrfamilienhaus, Rauch aus Fenster, Personen eventuell noch in der Wohnung. {adresse}. Meldung: {meldender}.",
    "B10": "Küchenbrand, Stiegenhaus verraucht, Bewohner können Wohnungen nicht verlassen. {adresse}. Meldung: {meldender}.",
    "B11": "Balkonbrand, Flammen greifen auf die Fassade über. {adresse}. Meldung: {meldender}.",
    "B12": "Fahrzeug brennt in Garage/Carport, direkt am Wohnhaus angebaut. {adresse}. Meldung: {meldender}.",
    "B13": "LKW brennt auf dem Betriebsgelände, starke Rauchentwicklung. {adresse}. Meldung: {meldender}.",
    "B14": "Heustock dampft und raucht, Temperatur stark erhöht. {adresse}. Meldung: {meldender}.",
    "B15": "Dachstuhlbrand, Flammen aus dem Dach, mehrere Wohnungen betroffen. {adresse}. Meldung: {meldender}.",
    "B16": "Brand im Industriegebäude, dichter schwarzer Rauch, Mitarbeiter evakuieren. {adresse}. Meldung: {meldender}.",
    "B17": "Wirtschaftsgebäude in Vollbrand, Tiere im angrenzenden Stall. {adresse}. Meldung: {meldender}.",
    "B18": "Brand in der Tiefgarage, Rauch dringt in die Stiegenhäuser. {adresse}. Meldung: {meldender}.",
    "B19": "Brand in {objekt}, viele Personen im Gebäude, Räumung läuft. {adresse}. Meldung: {meldender}.",
    "B20": "Brandmeldeanlage ausgelöst, {objekt}. {adresse}. Automatische Alarmweiterleitung.",
    "B21": "Brandmeldeanlage erneut ausgelöst, {objekt}, zuvor technische Arbeiten im Haus. {adresse}. Automatische Alarmweiterleitung.",
    "B22": "Unklare Rauchmeldung, Sachverhalt abklären. {adresse}. Meldung: {meldender}.",
    "B23": "Nachbarfeuerwehr fordert Unterstützung bei Gebäudebrand an. Bereitstellung bei {adresse}.",
    "B24": "Sondereinsatzmittel für laufenden Brandeinsatz angefordert. Einsatzort {adresse}.",
    "B25": "Boot brennt im Hafenbereich, Personen eventuell an Bord. {adresse}. Meldung: {meldender}.",
    "T01": "Wasser im Keller nach Starkregen, steigt weiter. {adresse}. Meldung: {meldender}.",
    "T02": "Kanalrückstau, Wasser drückt aus dem Bodenablauf in den Keller. {adresse}. Meldung: {meldender}.",
    "T03": "Wasser läuft in die Tiefgarageneinfahrt, Fahrzeuge in der Garage. {adresse}. Meldung: {meldender}.",
    "T04": "Abgebrochene Äste blockieren den Gehweg. {adresse}. Meldung: {meldender}.",
    "T05": "Ölspur auf der Fahrbahn, ca. 100 m. {adresse}. Meldung: {meldender}.",
    "T06": "Tier in misslicher Lage, kommt nicht selbst heraus. {adresse}. Meldung: {meldender}.",
    "T07": "Tragehilfe angefordert, Zugang über Stiege nicht möglich. {adresse}. Meldung: Rettungsdienst.",
    "T08": "Dachziegel lösen sich, fallen auf den Gehsteig. {adresse}. Meldung: {meldender}.",
    "T09": "Baum liegt quer über der Fahrbahn, Straße blockiert. {adresse}. Meldung: {meldender}.",
    "T10": "Baum auf geparktes Fahrzeug gestürzt, unklar ob Personen im Fahrzeug. {adresse}. Meldung: {meldender}.",
    "T11": "Fahrzeug von der Straße abgekommen, muss geborgen werden, keine eingeklemmten Personen. {adresse}. Meldung: {meldender}.",
    "T12": "Sturmschaden, Blechteile und Planen lösen sich vom Gebäude. {adresse}. Meldung: {meldender}.",
    "T13": "Hangrutschung, Erdmaterial auf Straße, Hang weiter in Bewegung. {adresse}. Meldung: {meldender}.",
    "T14": "Bach verklaust, Wasser tritt über das Ufer, Häuser in der Nähe. {adresse}. Meldung: {meldender}.",
    "T15": "Verkehrsunfall, eine Person im Fahrzeug eingeklemmt. {adresse}. Meldung: {meldender}.",
    "T16": "Person durch umgestürztes Bauteil eingeschlossen. {adresse}. Meldung: {meldender}.",
    "T17": "Person nach Erdrutsch verschüttet, Kollegen graben bereits. {adresse}. Meldung: {meldender}.",
    "T18": "Busunfall, zahlreiche Verletzte. {adresse}. Meldung: {meldender}.",
    "T19": "Teileinsturz eines Gebäudes, mehrere Personen vermisst. {adresse}. Meldung: {meldender}.",
    "T20": "Bahnunfall, mehrere Betroffene. {adresse}. Meldung: {meldender}.",
    "T21": "Unbekannter Stoff ausgetreten, stechender Geruch, Messung und Beratung erforderlich. {adresse}. Meldung: {meldender}.",
    "T22": "Verfärbung/Ölfilm auf dem Gewässer, Herkunft unbekannt. {adresse}. Meldung: {meldender}.",
    "T23": "Chemikalienaustritt im Betrieb, Gefahrenbereich muss festgelegt werden. {adresse}. Meldung: {meldender}.",
    "T24": "Gefahrstofffreisetzung, mehrere Personen klagen über Atemwegsreizung. {adresse}. Meldung: {meldender}.",
    "T25": "Nachbarfeuerwehr fordert technische Unterstützung an. Einsatzort {adresse}.",
    "T26": "Person im Wasser vermisst, Suche auf dem See erforderlich. {adresse}. Meldung: {meldender}.",
    "G01": "Starkregen: zahlreiche Meldungen über überflutete Keller im Ortsgebiet.",
    "G02": "Sturmfront: zahlreiche Meldungen über umgestürzte Bäume und Gebäudeschäden.",
    "G03": "Unwetter mit Starkregen und Sturmböen: zahlreiche Schadensmeldungen im Ortsgebiet.",
    "G04": "Bachverklausung mit Überflutung, zahlreiche Folgeschäden im Ortsgebiet.",
    "G05": "Lang anhaltendes Unwetter, Schadensmeldungen kommen in mehreren Wellen.",
    "F30": "Proberuf. Keine Einsatzlage.",
}


def _s(code: str, stichwort: str, titel: str, ort: str | tuple[str, ...], schwer: str, bausteine: str,
       *, zufall: bool = True, bma: bool = False, geo: dict[str, str] | None = None,
       gsl: str | None = None, overrides: dict[str, str] | None = None) -> SystemSzenario:
    ort_typen = (ort,) if isinstance(ort, str) else ort
    kategorie = "gsl" if gsl else ("brand" if stichwort.startswith("F") else "technik")
    alarmtext = _ALARMTEXTE[code]
    return SystemSzenario(code, stichwort, titel, alarmtext, kategorie, ort_typen, schwer,
                          tuple(bausteine.split(", ")) if bausteine else (), zufall, bma, bma, geo, gsl, overrides)


SZENARIEN: tuple[SystemSzenario, ...] = (
    _s("B01", "F1", "Unklarer Brandgeruch im Wohngebäude", "wohngebaeude", "leicht", "AUF-URSACHE, AUF-FEHL, KOM-WIDERSPRUCH"),
    _s("B02", "F1", "Gelöschter Küchenbrand, Kontrolle erforderlich", "wohnung", "leicht", "AUF-URSACHE, KOM-STROM-GAS, ENT-KEINE-GEFAHR"),
    _s("B03", "F1", "Rauch nach technischen Arbeiten, Ursache unklar", "gewerbe", "leicht", "AUF-WARTUNG, AUF-URSACHE, ESK-AUSBREITUNG"),
    _s("B04", "F2", "Müllcontainer brennt neben Gebäude", ("strasse", "gewerbe"), "leicht", "ESK-AUSBREITUNG, KOM-SCHAULUSTIGE, ENT-EINGEGRENZT"),
    _s("B05", "F2", "Heckenbrand nahe Grundstücksgrenze", "wohngebiet", "leicht", "ESK-AUSBREITUNG, KOM-LOESCHWASSER, ENT-EINGEGRENZT"),
    _s("B06", "F2", "Freistehender PKW brennt", ("parkplatz", "strasse"), "leicht", "KOM-ZUFAHRT, KOM-SCHAULUSTIGE, ENT-EINGEGRENZT"),
    _s("B07", "F2", "Kaminbrand mit Funkenflug", "wohngebaeude", "mittel", "ESK-AUSBREITUNG, KOM-STROM-GAS, AUF-URSACHE"),
    _s("B08", "F2", "Rauchwarnmelder ausgelöst, Lage unklar", "wohnung", "leicht", "AUF-FEHL, ESK-WEITERE-PERS, AUF-URSACHE"),
    _s("B09", "F3", "Zimmerbrand in Mehrfamilienhaus", "wohngebaeude", "mittel", "ESK-STIEGENHAUS, ESK-PERS-BALKON, ESK-NACHBAR, ENT-PERS-GEFUNDEN"),
    _s("B10", "F3", "Küchenbrand mit verrauchten Fluchtwegen", "wohngebaeude", "mittel", "ESK-STIEGENHAUS, ESK-WEITERE-PERS, ENT-EINGEGRENZT"),
    _s("B11", "F3", "Balkon-/Fassadenbrand, Ausbreitung droht", "mehrfamilienhaus", "mittel", "ESK-AUSBREITUNG, ESK-PERS-BALKON, KOM-LOESCHWASSER"),
    _s("B12", "F3", "Fahrzeugbrand in Garage oder Carport am Haus", ("gebaeude", "garage"), "mittel", "ESK-AUSBREITUNG, KOM-STROM-GAS, ENT-EINGEGRENZT"),
    _s("B13", "F3", "LKW brennt auf Betriebsgelände", "gewerbe", "mittel", "KOM-GEBINDE, ESK-AUSBREITUNG, KOM-ZUFAHRT"),
    _s("B14", "F3", "Heustocküberhitzung mit Rauchentwicklung", "landwirtschaft", "mittel", "KOM-TIERE, ESK-AUSBREITUNG, KOM-LOESCHWASSER"),
    _s("B15", "F4", "Dachstuhlbrand, mehrere Wohnungen betroffen", "wohngebaeude_gross", "schwer", "ESK-AUSBREITUNG, ESK-WEITERE-PERS, KOM-LOESCHWASSER, ESK-STICHWORT"),
    _s("B16", "F4", "Industriegebäude, starke Rauchentwicklung", "industrie", "schwer", "KOM-GEBINDE, ESK-GEFAHRBEREICH, KOM-ZUFAHRT, ESK-AUSBREITUNG"),
    _s("B17", "F4", "Landwirtschaftliches Wirtschaftsgebäude in Vollbrand", "landwirtschaft", "schwer", "KOM-TIERE, KOM-LOESCHWASSER, ESK-AUSBREITUNG"),
    _s("B18", "F4", "Brand in Tiefgarage mit Rauchverschleppung", "tiefgarage", "schwer", "ESK-STIEGENHAUS, ESK-WEITERE-PERS, KOM-FUNK"),
    _s("B19", "F4", "Brand in Objekt mit vielen Personen", "sonderobjekt", "schwer", "ESK-WEITERE-PERS, KOM-WIDERSPRUCH, LAG-SAMMELSTELLE, ENT-PERS-GEFUNDEN"),
    _s("B20", "F14", "Automatische Brandmeldeanlage, Ursache unklar", "objekt_bma", "leicht", "AUF-MELDERGR, AUF-WARTUNG, AUF-FEHL, ESK-STICHWORT", bma=True),
    _s("B21", "F14", "BMA nach technischen Arbeiten, erneute Auslösung", "objekt_bma", "mittel", "AUF-MELDERGR, AUF-WARTUNG, ESK-AUSBREITUNG", bma=True),
    _s("B22", "F10", "Unklare Rauchmeldung, Abklärung des Sachverhalts", "beliebig_geeignet", "leicht", "AUF-FEHL, AUF-URSACHE, DUP-ANRUF"),
    _s("B23", "F5", "Nachbarschaftshilfe bei Gebäudebrand", "nachbargebiet", "mittel", "ESK-AUSBREITUNG, KOM-FUNK, KOM-ZUFAHRT", zufall=False),
    _s("B24", "F11", "Anforderung Sondergerät bei bestehender Brandlage", "bestehender_einsatz", "mittel", "KOM-GERAET, FUNK-MATERIAL, KOM-ZUFAHRT", zufall=False),
    _s("B25", "F21", "Brand auf Boot / Hafenbereich", "boot", "schwer", "ESK-AUSBREITUNG, ESK-WEITERE-PERS, KOM-FUNK", geo={"gewaesser": "bodensee"}),
    _s("T01", "T1", "Wasser im Keller nach Starkregen", "wohngebaeude", "leicht", "KOM-STROM-GAS, ESK-WEITERE-KELLER, ENT-PEGEL"),
    _s("T02", "T1", "Kanalrückstau in Keller", "wohngebaeude", "leicht", "ESK-WEITERE-KELLER, KOM-STROM-GAS, ENT-PEGEL"),
    _s("T03", "T1", "Wasser dringt in Tiefgarageneinfahrt ein", "tiefgarage", "mittel", "ESK-PEGEL, KOM-ZUFAHRT, ENT-PEGEL"),
    _s("T04", "T1", "Umgestürzte Äste blockieren Gehweg", "strasse", "leicht", "ESK-AUSBREITUNG, DUP-ANRUF, ENT-WIND", overrides={"ESK-AUSBREITUNG": "Weitere Äste drohen abzubrechen"}),
    _s("T05", "T1", "Kleinere Ölspur / Bindemittel erforderlich", "verkehrsflaeche", "leicht", "KOM-ZUFAHRT, AUF-URSACHE, DUP-ANRUF"),
    _s("T06", "T1", "Tierrettung aus misslicher Lage", "wohn_gruengebiet", "leicht", "KOM-SCHAULUSTIGE, AUF-URSACHE, ENT-KEINE-GEFAHR"),
    _s("T07", "T1", "Tragehilfe / Zugang über Leiter", "gebaeude", "leicht", "KOM-ZUFAHRT, KOM-WIDERSPRUCH, FUNK-MATERIAL"),
    _s("T08", "T1", "Lose Dachziegel sichern, akute Gefährdung", "gebaeude", "mittel", "LAG-WETTER, KOM-SCHAULUSTIGE, ENT-WIND"),
    _s("T09", "T2", "Baum blockiert Fahrbahn", "strasse", "mittel", "KOM-ZUFAHRT, LAG-SPERRE, DUP-ANRUF"),
    _s("T10", "T2", "Baum auf geparktem Fahrzeug", ("parkplatz", "strasse"), "mittel", "ESK-WEITERE-PERS, KOM-STROM-GAS, ENT-KEINE-GEFAHR"),
    _s("T11", "T2", "Fahrzeugbergung ohne hydraulisches Rettungsgerät", ("strasse", "gelaende"), "mittel", "ESK-FAHRZEUGLAGE, KOM-ZUFAHRT, FUNK-MATERIAL"),
    _s("T12", "T2", "Größerer Sachschaden nach Sturmböen", ("gewerbe", "wohngebiet"), "mittel", "LAG-WETTER, ESK-AUSBREITUNG, ENT-WIND", overrides={"ESK-AUSBREITUNG": "Weitere Gebäudeteile drohen sich zu lösen"}),
    _s("T13", "T2", "Hangrutschung mit möglicher weiterer Bewegung", ("hang", "strasse"), "schwer", "ESK-HANGBEWEGUNG, LAG-SPERRE, ESK-GEFAHRBEREICH"),
    _s("T14", "T2", "Verklausung an Bach mit Überflutungsgefahr", "gewaessernaehe", "schwer", "ESK-PEGEL, ESK-WEITERE-KELLER, ENT-PEGEL"),
    _s("T15", "T3", "Verkehrsunfall, Person eingeklemmt", "strasse", "schwer", "ESK-WEITERE-PERS, ESK-FAHRZEUGLAGE, KOM-ZUFAHRT, ENT-PERS-BEFREIT"),
    _s("T16", "T3", "Person durch Bauteil eingeschlossen", "bau_betriebsgelaende", "schwer", "KOM-GERAET, KOM-WIDERSPRUCH, ENT-PERS-BEFREIT"),
    _s("T17", "T3", "Verschüttete Person nach Einsturz von Erdmaterial", ("baustelle", "hang"), "schwer", "ESK-HANGBEWEGUNG, FUNK-MATERIAL, ENT-PERS-BEFREIT"),
    _s("T18", "T4", "Busunfall mit vielen Verletzten", "strasse_geeignet", "schwer", "ESK-WEITERE-PERS, LAG-SAMMELSTELLE, KOM-ZUFAHRT, KOM-WIDERSPRUCH"),
    _s("T19", "T4", "Teileinsturz Gebäude, mehrere Personen vermisst", "gebaeude_gross", "schwer", "ESK-HANGBEWEGUNG, KOM-WIDERSPRUCH, LAG-SAMMELSTELLE, ENT-PERS-GEFUNDEN", overrides={"ESK-HANGBEWEGUNG": "Weitere Bauteile bewegen sich sichtbar"}),
    _s("T20", "T4", "Bahnunfall mit mehreren Betroffenen", "gleisbereich", "schwer", "ESK-WEITERE-PERS, KOM-STROM-GAS, LAG-SAMMELSTELLE"),
    _s("T21", "T6", "Unbekannter Stoff, Messung und Beratung erforderlich", "gewerbe", "mittel", "KOM-GEBINDE, ESK-GEFAHRBEREICH, AUF-URSACHE"),
    _s("T22", "T6", "Gewässerverunreinigung, fachliche Erkundung", "gewaesser", "mittel", "AUF-URSACHE, ESK-AUSBREITUNG, KOM-ZUFAHRT", overrides={"ESK-AUSBREITUNG": "Verunreinigung breitet sich bachabwärts aus"}),
    _s("T23", "T7", "Chemikalienaustritt mit Gefahrenbereich", ("industrie", "gewerbe"), "schwer", "ESK-GEFAHRBEREICH, ESK-WEITERE-PERS, KOM-GEBINDE, ESK-STICHWORT"),
    _s("T24", "T7", "Gefahrstofffreisetzung mit mehreren Betroffenen", ("industrie", "gewerbe"), "schwer", "ESK-WEITERE-PERS, ESK-GEFAHRBEREICH, LAG-SAMMELSTELLE"),
    _s("T25", "T5", "Technische Nachbarschaftshilfe angefordert", "nachbargebiet", "mittel", "KOM-FUNK, FUNK-MATERIAL, KOM-ZUFAHRT", zufall=False),
    _s("T26", "T21", "Bergung/Personensuche auf Wasser", "gewaesser_boot", "schwer", "KOM-FUNK, KOM-WIDERSPRUCH, ENT-PERS-GEFUNDEN", geo={"gewaesser": "bodensee"}),
    _s("G01", "T9", "Starkregen verursacht zahlreiche überflutete Keller", "ortsgebiet", "mittel", "ESK-PEGEL, ESK-WEITERE-KELLER, LAG-SPERRE, ENT-PEGEL", gsl="hochwasser"),
    _s("G02", "T9", "Sturmfront, viele Bäume und Gebäudeschäden", "ortsgebiet", "mittel", "LAG-WETTER, LAG-SPERRE, KOM-ZUFAHRT, ENT-WIND", gsl="sturm"),
    _s("G03", "T9", "Gleichzeitiger Starkregen und Sturm", "ortsgebiet", "schwer", "LAG-WETTER, ESK-PEGEL, LAG-SPERRE, ENT-WIND", gsl="kombination"),
    _s("G04", "T9", "Bachverklausung plus viele Folgeschäden", ("gewaesser", "ortsgebiet"), "schwer", "ESK-PEGEL, LAG-SPERRE, LAG-ANRUFE, ENT-PEGEL", gsl="hochwasser"),
    _s("G05", "T9", "Langandauerndes Unwetter mit mehreren Einsatzwellen", "ortsgebiet", "schwer", "LAG-WETTER, LAG-ANRUFE, LAG-SAMMELSTELLE, ENT-PEGEL", gsl="kombination"),
    _s("F30", "F30", "Proberuf / Testalarm", "beliebig", "leicht", "", zufall=False),
)


@dataclass(frozen=True)
class Welle:
    name: str
    dauer_s: int
    faktor: float
    stellen_codes: dict[str, int]


GSL_WELLEN: dict[str, tuple[Welle, ...]] = {
    "hochwasser": (
        Welle("Anstieg", 900, 0.8, {"T01": 18, "T02": 16, "T03": 14, "T05": 12, "T06": 10, "T07": 10, "T11": 8, "T14": 12}),
        Welle("Spitze", 1800, 1.5, {"T01": 15, "T02": 14, "T03": 13, "T05": 10, "T09": 10, "T11": 8, "T13": 12, "T14": 18}),
        Welle("Konzentration", 1200, 1.0, {"T01": 15, "T02": 14, "T03": 12, "T05": 10, "T09": 10, "T11": 9, "T14": 16, "T17": 14}),
        Welle("Entspannung", 900, 0.5, {"T01": 18, "T02": 16, "T03": 12, "T05": 14, "T06": 12, "T07": 10, "T11": 8, "T14": 10}),
    ),
    "sturm": (
        Welle("Böen", 900, 0.9, {"T01": 10, "T04": 14, "T05": 10, "T08": 14, "T09": 16, "T10": 12, "T11": 10, "T15": 14}),
        Welle("Spitze", 1500, 1.6, {"T01": 9, "T04": 13, "T05": 9, "T08": 13, "T09": 16, "T10": 11, "T11": 10, "T12": 11, "T15": 8}),
        Welle("Nachphase", 1200, 0.6, {"T01": 10, "T04": 15, "T05": 11, "T08": 14, "T09": 16, "T10": 11, "T11": 10, "T12": 13}),
    ),
    "kombination": (
        Welle("Starkregen", 900, 0.9, {"T01": 15, "T02": 14, "T03": 12, "T04": 10, "T05": 10, "T09": 10, "T11": 8, "T14": 12, "T15": 9}),
        Welle("Böen", 900, 1.2, {"T01": 10, "T03": 9, "T04": 14, "T05": 10, "T08": 13, "T09": 15, "T11": 10, "T12": 10, "T15": 9}),
        Welle("Mischphase", 1800, 1.7, {"T01": 12, "T02": 11, "T03": 10, "T04": 12, "T08": 10, "T09": 13, "T12": 10, "T14": 12, "T15": 10}),
        Welle("Nachphase", 1200, 0.6, {"T01": 12, "T02": 10, "T04": 13, "T05": 11, "T08": 11, "T09": 14, "T11": 10, "T14": 10, "T17": 9}),
    ),
}

GSL_SCHWERE_GEWICHTE = {"T1": 1.0, "T2": 0.8, "T3": 0.25, "T4": 0.05}

GSL_ALARMTEXT_VARIANTEN: dict[str, tuple[str, ...]] = {
    "T01": ("Starkregen setzt Keller unter Wasser, Pegel steigt. {adresse}. Meldung: {meldender}.", "Wassereintritt im Keller nach Regen, Auspumpen erforderlich. {adresse}. Meldung: {meldender}.", "Keller überflutet, Wasser dringt weiter nach. {adresse}. Meldung: {meldender}."),
    "T02": ("Rückstau aus Kanalisation im Keller. {adresse}. Meldung: {meldender}.", "Abwasser tritt im Keller aus Bodenablauf aus. {adresse}. Meldung: {meldender}.", "Kanalwasser drückt in Kellerräume, Lage nimmt zu. {adresse}. Meldung: {meldender}."),
    "T03": ("Tiefgarageneinfahrt läuft voll, Fahrzeuge gefährdet. {adresse}. Meldung: {meldender}.", "Wasser strömt in Garage, Zufahrt nicht mehr passierbar. {adresse}. Meldung: {meldender}.", "Starkregen überflutet Tiefgaragenzufahrt. {adresse}. Meldung: {meldender}."),
    "T04": ("Äste nach Windbruch auf Gehsteig, Durchgang blockiert. {adresse}. Meldung: {meldender}.", "Lose Baumteile liegen auf dem Gehweg. {adresse}. Meldung: {meldender}.", "Wind hat Äste abgerissen, Gefahr für Passanten. {adresse}. Meldung: {meldender}."),
    "T05": ("Betriebsmittel auf Fahrbahn, Bindemittel notwendig. {adresse}. Meldung: {meldender}.", "Verunreinigung durch Öl auf Straße festgestellt. {adresse}. Meldung: {meldender}.", "Rutschige Ölspur im Fahrbahnbereich. {adresse}. Meldung: {meldender}."),
    "T06": ("Tier steckt fest und braucht Hilfe. {adresse}. Meldung: {meldender}.", "Tierrettung angefordert, Tier kann sich nicht befreien. {adresse}. Meldung: {meldender}.", "Verletztes oder eingeklemmtes Tier gemeldet. {adresse}. Meldung: {meldender}."),
    "T07": ("Rettungsdienst benötigt Tragehilfe über Stiege. {adresse}. Meldung: Rettungsdienst.", "Patiententransport über engen Zugang, Feuerwehrhilfe erbeten. {adresse}. Meldung: Rettungsdienst.", "Tragehilfe wegen fehlender Liftmöglichkeit angefordert. {adresse}. Meldung: Rettungsdienst."),
    "T08": ("Dachziegel werden vom Wind gelöst, Bereich sichern. {adresse}. Meldung: {meldender}.", "Teile vom Dach fallen auf den Gehsteig. {adresse}. Meldung: {meldender}.", "Sturmschaden am Dach, lose Ziegel gefährden Passanten. {adresse}. Meldung: {meldender}."),
    "T09": ("Umgestürzter Baum sperrt Fahrbahn vollständig. {adresse}. Meldung: {meldender}.", "Baum liegt über Straße, kein Durchkommen. {adresse}. Meldung: {meldender}.", "Fahrbahn durch Windwurf blockiert. {adresse}. Meldung: {meldender}."),
    "T10": ("Baum auf abgestelltem Fahrzeug, Personenlage unklar. {adresse}. Meldung: {meldender}.", "Windwurf hat Pkw getroffen. {adresse}. Meldung: {meldender}.", "Astwerk und Baum liegen auf einem Fahrzeug. {adresse}. Meldung: {meldender}."),
    "T11": ("Pkw muss nach Abkommen von Fahrbahn geborgen werden. {adresse}. Meldung: {meldender}.", "Fahrzeugbergung ohne eingeklemmte Personen angefordert. {adresse}. Meldung: {meldender}.", "Auto abseits der Straße, technische Hilfe benötigt. {adresse}. Meldung: {meldender}."),
    "T12": ("Sturmböen lösen Gebäudeteile, Sicherung erforderlich. {adresse}. Meldung: {meldender}.", "Blechteile schlagen im Wind am Gebäude. {adresse}. Meldung: {meldender}.", "Planen und Verkleidung drohen sich zu lösen. {adresse}. Meldung: {meldender}."),
    "T13": ("Erdreich rutscht auf Straße, Hang weiterhin instabil. {adresse}. Meldung: {meldender}.", "Murenmaterial blockiert Fahrbahn. {adresse}. Meldung: {meldender}.", "Hangbewegung mit Material auf Verkehrsfläche. {adresse}. Meldung: {meldender}."),
    "T14": ("Bach ist verklaust, Wasser bedroht nahe Gebäude. {adresse}. Meldung: {meldender}.", "Wasser tritt bei Bachlauf über die Ufer. {adresse}. Meldung: {meldender}.", "Verklausung verursacht Überflutung im Nahbereich. {adresse}. Meldung: {meldender}."),
    "T15": ("Verkehrsunfall mit eingeklemmter Person. {adresse}. Meldung: {meldender}.", "Person nach Unfall im Fahrzeug eingeschlossen. {adresse}. Meldung: {meldender}.", "Pkw-Unfall, technische Rettung erforderlich. {adresse}. Meldung: {meldender}."),
    "T16": ("Person unter umgestürztem Bauteil eingeschlossen. {adresse}. Meldung: {meldender}.", "Bauteil hat Person eingeklemmt, Rettung läuft an. {adresse}. Meldung: {meldender}.", "Technische Rettung einer eingeschlossenen Person. {adresse}. Meldung: {meldender}."),
    "T17": ("Person nach Erdrutsch teilweise verschüttet. {adresse}. Meldung: {meldender}.", "Verschüttete Person, Kollegen leisten Erste Hilfe. {adresse}. Meldung: {meldender}.", "Erdmaterial hat Person eingeschlossen. {adresse}. Meldung: {meldender}."),
}
