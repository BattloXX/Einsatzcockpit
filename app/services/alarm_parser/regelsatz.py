"""Versionierte Regeln als validierbare JSON-Daten."""

import re
import subprocess
import sys
from functools import lru_cache
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


@lru_cache(maxsize=512)
def validiere_muster(pattern: str, probelauf: bool = True) -> str:
    """Konservative Regex-Teilmenge: keine quantifizierten Gruppen/Backrefs.

    Unbeschraenkte Wiederholungen werden zusaetzlich eingeschraenkt. Der
    isolierte Probelauf bricht auch teure begrenzte Wiederholungen hart ab.
    """
    if len(pattern) > 200:
        raise ValueError("Muster laenger als 200 Zeichen")
    if re.search(r"\\[1-9]|\(\?P=|\(\?[=!<]|\)(?:[*+?]|\{)", pattern):
        raise ValueError("Unsichere Regex-Konstruktion")
    # Auch begrenzte Wiederholungen duerfen keinen riesigen Suchraum erzeugen.
    if re.search(r"\{\d{4,}|,\d{4,}\}", pattern):
        raise ValueError("Wiederholung zu gross")
    ohne_escapes = re.sub(r"\\.", "X", pattern)
    if len(re.findall(r"[*+]|\{\d+,\}", ohne_escapes)) > 1:
        raise ValueError("Mehrere unbeschraenkte Wiederholungen")
    try:
        regex = re.compile(pattern, re.IGNORECASE)
        if probelauf:
            # Eigener Prozess: auch ein unerwartet teures Muster darf den Validator
            # nicht blockieren. Die 50 ms gelten fuer den Regex-Lauf selbst (im Kind
            # gemessen), der Interpreterstart zaehlt nicht; hartes Limit 3 s.
            subprocess.run(
                [sys.executable, "-I", "-c",
                 "import re,sys,time; r=re.compile(sys.argv[1],re.I); t=time.perf_counter(); "
                 "[r.search(x) for x in ('a'*2047+'!', '0'*2047+'!', ' '*2048)]; "
                 "sys.exit(0 if time.perf_counter()-t <= 0.05 else 3)", pattern],
                timeout=3, check=True, capture_output=True,
            )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise ValueError("Muster ueberschreitet 50 ms oder Probelauf fehlgeschlagen") from exc
    except re.error as exc:
        raise ValueError("Ungueltiges Muster") from exc
    if regex.match(""):
        raise ValueError("Leere Treffer sind nicht erlaubt")
    return pattern


class Regelsatz(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "0"
    leitstellennummer_muster: str = r"^[a-z]\d{8}\b"
    stichwort_muster: str = r"[ft]\d{1,2}"
    sonderkennzeichen_muster: str = r"_[a-z]+"
    weitere_code_muster: str = r"r\d+"
    trennzeichen: str = Field(default=">", min_length=1, max_length=10)
    datum_muster: str = r"\(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2} Uhr\)"
    datumsformat: str = "%d.%m.%Y %H:%M Uhr"
    gemeinde_aliase: dict[str, str] = Field(default_factory=dict)
    ortsteil_aliase: dict[str, str] = Field(default_factory=dict)
    strassen_aliase: dict[str, str] = Field(default_factory=dict)
    meldebild_phrasen: list[str] = Field(default_factory=lambda: ["patientenrettung aus"])
    zusatz_phrasen: list[str] = Field(default_factory=lambda: ["rettung vor ort"])
    zusatz_muster: str = r"\bca\s{0,5}\d"
    adresszusatz_muster: str = r"\bnaehe hnr \d{1,5}[a-z]?\b"
    lagezusatz_muster: str = r"\boestlich im wald\b"
    hausnummer_muster: str = r"\d{1,5}[a-z]?/\d{1,5}|\d{1,5}[a-z]?"
    melder_muster: str = r"\bmelder\s{1,5}([\d/.-]+)"
    melderbereich_muster: str = r"\bmelderbereich\s{1,5}([\w/.-]+)"
    reihenfolge_varianten: list[list[str]] = Field(default_factory=lambda: [
        ["gemeinde", "ortsteil", "strasse"], ["strasse", "gemeinde"],
        ["ortsteil", "gemeinde", "strasse"],
    ])
    qualitaetsgewichte: dict[str, int] = Field(default_factory=lambda: {
        "leitstellennummer": 25, "stichwort": 25, "gemeinde": 20,
        "strasse": 10, "alarmzeit": 15, "meldebild": 5,
    })
    unbedeutend_muster: str = r"[\s,;:.\-]+"

    @model_validator(mode="after")
    def pruefen(self) -> Self:
        for name in type(self).model_fields:
            if name.endswith("_muster"):
                wert = getattr(self, name)
                # Eingebaute Standardmuster sind geprueft; Probelauf nur fuer fremde Muster.
                validiere_muster(wert, probelauf=wert != type(self).model_fields[name].default)
        if any(value < 0 or value > 100 for value in self.qualitaetsgewichte.values()):
            raise ValueError("Gewichte muessen zwischen 0 und 100 liegen")
        erlaubt = {"gemeinde", "ortsteil", "strasse"}
        if not self.reihenfolge_varianten or any(
            not variante or len(set(variante)) != len(variante) or set(variante) - erlaubt
            for variante in self.reihenfolge_varianten
        ):
            raise ValueError("Ungueltige Reihenfolge")
        return self


STANDARD_REGELSATZ = Regelsatz()
