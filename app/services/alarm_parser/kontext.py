"""Katalog-Snapshot ohne Datenbank oder andere I/O."""

from dataclasses import dataclass, field


@dataclass
class GemeindeInfo:
    anzeigename: str
    ortsteile: dict[str, str] = field(default_factory=dict)
    strassen: dict[str, str] = field(default_factory=dict)
    objekte: dict[str, str] = field(default_factory=dict)


@dataclass
class ParserKontext:
    gemeinden: dict[str, GemeindeInfo] = field(default_factory=dict)
    zeitzone: str = "Europe/Vienna"


def leerer_kontext() -> ParserKontext:
    return ParserKontext()
