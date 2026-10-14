"""Read-only Referenz der LFV-Stichworte V03."""
# ruff: noqa: E501

from dataclasses import dataclass

QUELLE = "LFV Vorarlberg, V03 Beschreibung f und t Stichworte 02_2021.xlsx"
REVISION = "V03 02/2021"


@dataclass(frozen=True)
class LfvStichwort:
    code: str
    bezeichnung: str
    kategorie: str
    beschreibung: str


STICHWORTE: tuple[LfvStichwort, ...] = (
    LfvStichwort("f1", "Geringes Ereignis Brand", "brand", "Kein Feuer, Geruchswahrnehmung, besorgter / unsicherer Melder, Kontrolle, belüften nach gelöschtem Brand, Nachlöscharbeiten, …"),
    LfvStichwort("f2", "Kleines Ereignis Brand", "brand", "Brand außerhalb von Gebäuden, freistehendem PKW, Mülleimer, Container, Freifläche/Schilf/Hecke/Baum, Kaminbrand, Rauchwarnmelder oder nicht registrierte BMA, …"),
    LfvStichwort("f3", "Mittleres Ereignis Brand", "brand", "Zimmerbrand, Wohnungsbrand oder Brand direkt an Gebäuden, Balkon, Fassade, Carport (Gebäudeanbau), Fahrzeug in Garage/Carport/Schuppen, LKW, Heustocküberhitzung, …"),
    LfvStichwort("f4", "Großes Ereignis Brand", "brand", "Haus steht in Vollbrand, Dachstuhlbrand, Industriegebäude, landwirtschaftliches Gebäude, Kraftwerk, Brand in Tiefgarage, in anderen sensiblen/großen Objekten (z. B. viele Personen im Gebäude), …"),
    LfvStichwort("f5", "Nachbarschaftshilfe Brand", "brand", "Feuerwehr fordert Nachbarschaftshilfe an. Im Auftrag der zuständigen Feuerwehr alarmiert die RFL die Feuerwehr(en), welche im Alarmplan hinterlegt sind."),
    LfvStichwort("f10", "Abklärung", "brand", "Abklärung einer Sachlage bzw. eines möglichen Einsatzes mit entsprechend kompetenter Stelle. Sachlage ist nicht einem anderen Einsatzstichwort zuordenbar oder bedarf der vorherigen Abklärung."),
    LfvStichwort("f11", "Sondereinsatzmittel", "brand", "Zur Bewältigung eines Ereignisses sind Sondereinsatzmittel erforderlich. Alle nicht als Sondereinsatzmittel definierten Geräte/Fahrzeuge/Materialien werden bei Anforderung per f11_FW-Gemeinde alarmiert."),
    LfvStichwort("f14", "BMA", "brand", "Eine Brandmeldeanlage, welche als registrierter Notrufteilnehmer über eine automatische Alarmweiterleitung verfügt."),
    LfvStichwort("f21", "Bootseinsatz Brand", "brand", "Brandereignis auf dem Bodensee."),
    LfvStichwort("f30", "Proberuf", "brand", "Proberuf."),
    LfvStichwort("t1", "Geringes Ereignis Technik", "technik", "Tierrettung, Wasser im Gebäude, Wasserschaden, Hindernisse und Sachschäden sichern/beseitigen/reinigen, Ölbindemittel aufbringen, Ausleuchtung, Tragehilfe, Zugangsschaffung mit Leiter, …"),
    LfvStichwort("t2", "Kleines Ereignis Technik", "technik", "Befreiung/Bergung ohne hydraulisches Rettungsgerät u. a. nach VU, Sichtschutz, große Hindernisse und Sachschäden, Hangrutschung/Mure mit Gefahr, Fahrzeuge/Maschinen/Geräte sichern, bergen, …"),
    LfvStichwort("t3", "Mittleres Ereignis Technik", "technik", "Stützpunkt mit hydraulischem Rettungsgerät, Personen retten, befreien nach Unfall eingeschlossener Person, verschüttete Person, Personenrettung bei Pfählungsverletzung, …"),
    LfvStichwort("t4", "Großes Ereignis Technik", "technik", "Unfall Bus-/Bahnverkehr oder viele beteiligte Fahrzeuge, MANV (Massenanfall von Verletzten), Einsturz Gebäude."),
    LfvStichwort("t5", "Nachbarschaftshilfe Technik", "technik", "Feuerwehr fordert Nachbarschaftshilfe an. Im Auftrag der zuständigen Feuerwehr alarmiert die RFL die Feuerwehr(en), welche im Alarmplan hinterlegt sind."),
    LfvStichwort("t6", "Gefahrgut klein", "technik", "Messgerät und/oder Beratung wird benötigt, Messeinsatz (Erkundung, Lagebeurteilung), Beratung bei Freisetzung chemischer Stoffe, Beratung bei Gewässerverunreinigung, …"),
    LfvStichwort("t7", "Gefahrgut groß", "technik", "Ereignis mit Freisetzung von Gefahrstoffen (chemisch, biologisch, radioaktiv), Strahlenschutz."),
    LfvStichwort("t9", "Großlage", "technik", "Ereignis mit vielen Schadenstellen. Hindernisse und Sachschäden sichern/beseitigen/reinigen, Sturmschäden, Wasserschäden, Verklausung, Hangrutschung/Mure, …"),
    LfvStichwort("t21", "Bootseinsatz Technik", "technik", "Personenrettung, Sucheinsatz, Arbeitseinsatz, Bergungen, … Unterstichwort Ölwehr: Öl-/Betriebsmittel-Austritt, Verschmutzung."),
)


def finde(code: str) -> LfvStichwort | None:
    code_lower = code.lower()
    return next((stichwort for stichwort in STICHWORTE if stichwort.code == code_lower), None)
