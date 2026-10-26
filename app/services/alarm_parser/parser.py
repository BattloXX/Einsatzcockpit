"""Reihenfolgeabhaengiger Regelparser mit explizitem Textspannen-Verbrauch."""

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.lis.lis_mapping import is_exercise_label, map_stichwort
from app.services.objekt_matching_service import BMA_REGEX, finde_bma_nummern

from .kontext import ParserKontext
from .normalize import adressvergleich, normalisiere, vergleichsform
from .regelsatz import STANDARD_REGELSATZ, Regelsatz


@dataclass
class AlarmParseResult:
    original_text: str
    leitstellennummer: str | None = None
    stichwort: str | None = None
    alarm_type_code: str | None = None
    weitere_codes: list[str] = field(default_factory=list)
    sonderkennzeichen: list[str] = field(default_factory=list)
    gemeinde: str | None = None
    ortsteil: str | None = None
    strasse: str | None = None
    hausnummer: str | None = None
    adresszusatz: str | None = None
    lagezusatz: str | None = None
    meldebild: str | None = None
    zusatzinfo: list[str] = field(default_factory=list)
    alarmzeit: datetime | None = None
    rest_text: str = ""
    status: str = "unklar"
    qualitaet: int = 0
    hinweise: list[str] = field(default_factory=list)
    parser_version: str = "0"
    ist_uebung: bool = False
    bma: list[str] = field(default_factory=list)
    melder: str | None = None
    melderbereich: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["alarmzeit"] = self.alarmzeit.isoformat() if self.alarmzeit else None
        return result


class Textspannen:
    def __init__(self, text: str):
        self.text = text
        self.belegt = [False] * len(text)
        self.gesperrt = [False] * len(text)

    def frei(self, start: int, ende: int) -> bool:
        return not any(self.belegt[start:ende]) and not any(self.gesperrt[start:ende])

    def sperre(self, start: int, ende: int) -> None:
        self.gesperrt[start:ende] = [True] * (ende - start)

    def nimm(self, start: int, ende: int) -> str:
        wert = self.rest(arbeitskopie=True)[start:ende].strip()
        for index in range(start, ende):
            if not self.gesperrt[index]:
                self.belegt[index] = True
        return wert

    def rest(self, arbeitskopie: bool = False) -> str:
        return "".join(
            " " if self.belegt[i] or (arbeitskopie and self.gesperrt[i]) else char
            for i, char in enumerate(self.text)
        )

    def phrasen(self, phrase: str, adresse: bool = False) -> list[tuple[int, int]]:
        norm = adressvergleich if adresse else vergleichsform
        ziel = norm(phrase)
        if not ziel:
            return []
        tokens = list(re.finditer(r"\S+", self.text))
        treffer = []
        for i, token in enumerate(tokens):
            for ende in tokens[i:i + len(ziel.split()) + 2]:
                start, stop = token.start(), ende.end()
                if self.frei(start, stop) and norm(self.text[start:stop]) == ziel:
                    treffer.append((start, stop))
        return treffer


def _parse(result: AlarmParseResult, kontext: ParserKontext, regeln: Regelsatz) -> None:
    text = normalisiere(result.original_text)
    sp = Textspannen(text)
    result.parser_version = regeln.version
    result.ist_uebung = is_exercise_label(text)
    result.bma = finde_bma_nummern(text)
    for feld in ("melder", "melderbereich"):
        struktur_treffer = list(re.finditer(getattr(regeln, f"{feld}_muster"), text, re.IGNORECASE))
        if len(struktur_treffer) == 1:
            setattr(result, feld, struktur_treffer[0].group(1))
        elif struktur_treffer:
            result.hinweise.extend([f"{feld}_mehrdeutig", "mehrdeutig"])
            for struktur_match in struktur_treffer:
                sp.sperre(*struktur_match.span())

    # Zeit zuerst abtrennen, auch wenn sie am Ende des Meldebildes steht.
    zeit_treffer = list(re.finditer(regeln.datum_muster, text, re.IGNORECASE))
    if len(zeit_treffer) > 1:
        result.hinweise.extend(["alarmzeit_mehrdeutig", "mehrdeutig"])
        for zeit_match in zeit_treffer:
            sp.sperre(*zeit_match.span())
    for datum_match in zeit_treffer if len(zeit_treffer) == 1 else []:
        try:
            lokal = datetime.strptime(datum_match.group()[1:-1], regeln.datumsformat).replace(
                tzinfo=ZoneInfo(kontext.zeitzone),
            )
            utc = lokal.astimezone(UTC)
            # Zeitumstellung: weder nicht existente noch doppelte Ortszeiten raten.
            if utc.astimezone(lokal.tzinfo).replace(tzinfo=None) != lokal.replace(tzinfo=None):
                raise ValueError("Nicht existente Ortszeit")
            if lokal.utcoffset() != lokal.replace(fold=1).utcoffset():
                raise ValueError("Mehrdeutige Ortszeit")
            result.alarmzeit = utc.replace(tzinfo=None)
            sp.nimm(*datum_match.span())
        except (ValueError, KeyError):
            result.hinweise.append("alarmzeit_ungueltig")
            sp.sperre(*datum_match.span())

    match = re.match(regeln.leitstellennummer_muster, text, re.IGNORECASE)
    pos = 0
    if match:
        result.leitstellennummer = sp.nimm(*match.span())
        pos = match.end()
    # Codes nur im Kopf erkennen, niemals Ziffern aus Adresse/Meldebild entnehmen.
    kopf: list[tuple[str, str, int, int]] = []
    for token in re.finditer(r"[^\s,]+", text[pos:]):
        start, ende = pos + token.start(), pos + token.end()
        value = token.group()
        code_feld = next((name for name, muster in (
            ("stichwort", regeln.stichwort_muster),
            ("weitere_codes", regeln.weitere_code_muster),
            ("sonderkennzeichen", regeln.sonderkennzeichen_muster),
        ) if re.fullmatch(muster, value, re.IGNORECASE)), None)
        if code_feld is None:
            break
        kopf.append((code_feld, value, start, ende))
    mehrere_stichworte = sum(feld == "stichwort" for feld, _, _, _ in kopf) > 1
    if mehrere_stichworte:
        result.hinweise.extend(["stichwort_mehrdeutig", "mehrdeutig"])
    for code_feld, value, start, ende in kopf:
        if code_feld == "stichwort" and mehrere_stichworte:
            sp.sperre(start, ende)
            continue
        sp.nimm(start, ende)
        if code_feld == "stichwort":
            result.stichwort = value
            result.alarm_type_code = map_stichwort(value)
        else:
            getattr(result, code_feld).append(value)

    # Explizite Segmente sowie bekannte Freitext-Anfaenge.
    trennstellen = [m.start() for m in re.finditer(re.escape(regeln.trennzeichen), sp.rest(arbeitskopie=True))]
    if trennstellen:
        grenzen = trennstellen + [len(text)]
        for i, start in enumerate(trennstellen):
            ende = grenzen[i + 1]
            wert = sp.rest(arbeitskopie=True)[start + len(regeln.trennzeichen):ende].strip()
            sp.nimm(start, ende)
            if i == 0:
                result.meldebild = wert or None
            elif wert:
                result.zusatzinfo.append(wert)
    else:
        starts = [span[0] for phrase in regeln.meldebild_phrasen for span in sp.phrasen(phrase)]
        if starts:
            start = min(starts)
            result.meldebild = sp.rest(arbeitskopie=True)[start:].strip()
            sp.nimm(start, len(text))
    if result.meldebild:
        bild = Textspannen(result.meldebild)
        starts = [span[0] for phrase in regeln.zusatz_phrasen for span in bild.phrasen(phrase)]
        starts += [m.start() for m in re.finditer(regeln.zusatz_muster, bild.text, re.IGNORECASE)]
        if starts:
            start = min(starts)
            result.zusatzinfo.insert(0, bild.text[start:].strip())
            result.meldebild = bild.text[:start].strip() or None

    def katalog(feld: str, werte: dict[str, str], aliase: dict[str, str]) -> tuple[int, int] | None:
        kandidaten: dict[tuple[int, int], set[str]] = {}
        for key, anzeige in werte.items():
            namen = [key, anzeige] + [alias for alias, ziel in aliase.items() if vergleichsform(ziel) in (
                vergleichsform(key), vergleichsform(anzeige),
            )]
            for name in namen:
                for span in sp.phrasen(name, adresse=feld == "strasse"):
                    kandidaten.setdefault(span, set()).add(anzeige)
        if not kandidaten:
            return None
        # Verschachtelte Namensvarianten: der laengste Katalogname gewinnt.
        kandidaten = {span: namen for span, namen in kandidaten.items() if not any(
            other != span and other[0] <= span[0] and other[1] >= span[1] for other in kandidaten
        )}
        if len(kandidaten) != 1 or len(next(iter(kandidaten.values()))) != 1:
            result.hinweise.extend([f"{feld}_mehrdeutig", "mehrdeutig"])
            for span in kandidaten:
                sp.sperre(*span)
            return None
        span, anzeigenamen = next(iter(kandidaten.items()))
        setattr(result, feld, next(iter(anzeigenamen)))
        sp.nimm(*span)
        return span

    gemeinde_span = katalog("gemeinde", {key: info.anzeigename for key, info in kontext.gemeinden.items()},
                            regeln.gemeinde_aliase)
    infos = [info for info in kontext.gemeinden.values() if info.anzeigename == result.gemeinde]
    ort_span = str_span = None
    if len(infos) == 1:
        info = infos[0]
        ort_span = katalog("ortsteil", info.ortsteile, regeln.ortsteil_aliase)
        str_span = katalog("strasse", info.strassen | info.objekte, regeln.strassen_aliase)
    elif not result.gemeinde and "gemeinde_mehrdeutig" not in result.hinweise:
        # Eindeutigkeit gilt ueber Gemeinden hinweg, auch bei gleicher Anzeige.
        passende_infos = []
        for info in kontext.gemeinden.values():
            werte = info.strassen | info.objekte
            namen = list(werte) + list(werte.values()) + [
                alias for alias, ziel in regeln.strassen_aliase.items()
                if adressvergleich(ziel) in {adressvergleich(name) for name in werte}
            ]
            spans = {span for name in namen for span in sp.phrasen(name, adresse=True)}
            if spans:
                passende_infos.append((info, spans))
        if len(passende_infos) == 1:
            info = passende_infos[0][0]
            str_span = katalog("strasse", info.strassen | info.objekte, regeln.strassen_aliase)
        elif passende_infos:
            result.hinweise.extend(["strasse_mehrdeutig", "mehrdeutig"])
            for _, spans in passende_infos:
                for span in spans:
                    sp.sperre(*span)

    # Strukturangaben ausserhalb des Freitextes ebenfalls eindeutig verbrauchen.
    for muster in (BMA_REGEX.pattern, regeln.melder_muster, regeln.melderbereich_muster):
        for struktur_match in re.finditer(muster, text, re.IGNORECASE):
            if sp.frei(*struktur_match.span()):
                sp.nimm(*struktur_match.span())

    reihenfolge = [name for _, name in sorted(
        (ort_spanne[0], name) for name, ort_spanne in (
            ("gemeinde", gemeinde_span), ("ortsteil", ort_span), ("strasse", str_span),
        ) if ort_spanne
    )]
    if reihenfolge and not any(
        [name for name in variante if name in reihenfolge] == reihenfolge
        for variante in regeln.reihenfolge_varianten
    ):
        # Unzulaessige Reihenfolgen bleiben vollstaendig pruefbar im Rest.
        for name, ruecknahme in (("gemeinde", gemeinde_span), ("ortsteil", ort_span), ("strasse", str_span)):
            if ruecknahme:
                sp.belegt[ruecknahme[0]:ruecknahme[1]] = [False] * (ruecknahme[1] - ruecknahme[0])
                setattr(result, name, None)
        str_span = None
        result.hinweise.append("reihenfolge_unbekannt")

    for feld in ("adresszusatz", "lagezusatz"):
        tokens = list(re.finditer(r"\S+", sp.rest(arbeitskopie=True)))
        treffer = []
        for i, token in enumerate(tokens):
            for ende_token in tokens[i:i + 8]:
                start, stop = token.start(), ende_token.end()
                if sp.frei(start, stop) and re.fullmatch(
                    getattr(regeln, f"{feld}_muster"), vergleichsform(text[start:stop]), re.IGNORECASE,
                ):
                    treffer.append((start, stop))
        if len(treffer) == 1:
            start, stop = treffer[0]
            if feld == "lagezusatz" and not result.ortsteil:
                grenze = max((i + 1 for i in range(start) if sp.belegt[i] or sp.gesperrt[i]), default=0)
                rest_vorher = sp.rest(arbeitskopie=True)[grenze:start]
                prefix = re.search(r"\S.*", rest_vorher)
                if prefix:
                    start = grenze + prefix.start()
            setattr(result, feld, sp.nimm(start, stop))
        elif treffer:
            result.hinweise.append("mehrdeutig")

    if str_span:
        match = re.match(r"\s*(\S+)", sp.rest(arbeitskopie=True)[str_span[1]:])
        if match and re.fullmatch(regeln.hausnummer_muster, match.group(1), re.IGNORECASE):
            start = str_span[1] + match.start(1)
            result.hausnummer = sp.nimm(start, str_span[1] + match.end(1))
    if (result.gemeinde or result.strasse) and not result.hausnummer:
        result.hinweise.append("hausnummer_fehlt")

    rest = sp.rest()
    result.rest_text = " ".join(
        token for token in rest.split() if not re.fullmatch(regeln.unbedeutend_muster, token)
    )
    result.qualitaet = min(100, sum(
        weight for name, weight in regeln.qualitaetsgewichte.items() if getattr(result, name, None)
    ))
    if result.leitstellennummer and result.stichwort:
        result.status = "ok" if (
            (result.gemeinde or result.strasse) and result.alarmzeit and not result.rest_text
            and "mehrdeutig" not in result.hinweise
        ) else "teilweise"


def parse_alarm(raw_text: str, kontext: ParserKontext, regelsatz: Regelsatz = STANDARD_REGELSATZ) -> AlarmParseResult:
    result = AlarmParseResult(original_text=raw_text)
    try:
        _parse(result, kontext, regelsatz)
    except Exception:
        logging.getLogger("einsatzleiter.alarm").exception("Unerwarteter Fehler im Alarmparser")
        # Bei unerwarteten Eingaben keine halbfertige Zuordnung ausliefern.
        result = AlarmParseResult(
            original_text=raw_text, rest_text=normalisiere(raw_text),
            hinweise=["parser_fehler"], parser_version=regelsatz.version,
        )
    return result
