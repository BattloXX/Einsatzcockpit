"""Normalisierung und festes Fachlexikon fuer den Suchindex."""

import re
import unicodedata


def normalisiere(text: str) -> str:
    return " ".join(text.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").split())


SYNONYME: tuple[frozenset[str], ...] = (
    frozenset({"bma", "brandmeldeanlage"}),
    frozenset({"bmz", "brandmeldezentrale"}),
    frozenset({"fsd", "feuerwehrschluesseldepot", "schluesseldepot"}),
    frozenset({"fbf", "feuerwehrbedienfeld"}),
    frozenset({"fat", "feuerwehranzeigetableau"}),
    frozenset({"rwa", "rauchwaermeabzug", "rauch- und waermeabzug"}),
    frozenset({"hydrant", "ueberflurhydrant", "unterflurhydrant"}),
    frozenset({"lnw", "loeschwasser", "loeschwasserentnahme"}),
    frozenset({"gsl", "grossschadenslage"}),
    frozenset({"tlf", "tankloeschfahrzeug"}),
    frozenset({"kdo", "kommando"}),
    frozenset({"pv", "photovoltaik", "pv-anlage"}),
    frozenset({"gas", "erdgas"}),
    frozenset({"stiege", "treppenhaus"}),
    frozenset({"hochwasser", "überflutung", "überschwemmung", "hq"}),
    frozenset({"blackout", "stromausfall"}),
    frozenset({"evakuierung", "räumung"}),
    frozenset({"sammelplatz", "treffpunkt"}),
    frozenset({"einsatzleiter", "el", "einsatzleitung"}),
    frozenset({"katastrophenschutz", "kats", "kat-schutz", "katschutz"}),
    frozenset({"fdl", "fahrdienstleiter"}),
    frozenset({"oberleitung", "fahrleitung"}),
)


def expandiere(woerter: list[str]) -> list[str]:
    """Originalwoerter zuerst, danach Synonyme in reproduzierbarer Reihenfolge."""
    ergebnis = list(dict.fromkeys(normalisiere(w) for w in woerter))
    for wort in list(ergebnis):
        for gruppe in SYNONYME:
            normalisierte_gruppe = {normalisiere(w) for w in gruppe}
            if wort in normalisierte_gruppe:
                for synonym in sorted(normalisierte_gruppe):
                    if synonym not in ergebnis:
                        ergebnis.append(synonym)
    return ergebnis


_TELEFON = re.compile(r"(?<![\w.+])(?:\+|00)?(?:\(\d+\)|\d)[\d \t/()\-]*\d(?!\w|\.\d)")


def entferne_telefonnummern(text: str) -> str:
    """Nur Zahlenfolgen mit mindestens sieben Ziffern als Telefon entfernen."""
    def ersetze(treffer: re.Match[str]) -> str:
        nummer = treffer.group()
        return "[Tel.]" if sum(c.isdigit() for c in nummer) >= 7 else nummer

    return _TELEFON.sub(ersetze, text)


def suchwoerter(q: str) -> list[str]:
    text = re.sub(r'[+\-<>()~*"@]', "", normalisiere(q))
    woerter = []
    for wort in text.split():
        while wort and unicodedata.category(wort[0]).startswith(("P", "S")):
            wort = wort[1:]
        while wort and unicodedata.category(wort[-1]).startswith(("P", "S")):
            wort = wort[:-1]
        if wort:
            woerter.append(wort)
    return woerter[:12]
