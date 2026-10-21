"""Suchqualitaet messen; Suchtexte und Titel erscheinen nur in der Review-CSV."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from collections import defaultdict
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import TextIO
from unittest.mock import patch

from pydantic import BaseModel, Field, ValidationError, field_validator

# Direktaufruf aus dem Repository, ohne Installation als Python-Modul.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.permissions import can_view_kontakt_directory  # noqa: E402
from app.core.tenant import set_tenant_context  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.mcp.tools.kontakt import kontakte_modul_aktiv  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.kontakt_access_service import KontaktZugriff  # noqa: E402
from app.services.suche import abfrage, embedding, vektor  # noqa: E402
from app.services.suche.vertrag import ERLAUBTE_TYPEN, SuchAnfrage, Treffer  # noqa: E402

GRUPPEN = ("objekt", "hochwasserplan", "gasplan", "sperre", "wasserstelle", "einsatz", "kontakt")
MODI = ("volltext", "hybrid", "semantisch")


class Frage(BaseModel):
    nr: int = Field(strict=True, gt=0)
    gruppe: str
    erwartete_quelle: str
    frage: str = Field(min_length=1, max_length=500)
    soll_titel: str | list[str] | None = None

    @field_validator("gruppe")
    @classmethod
    def pruefe_gruppe(cls, wert: str) -> str:
        if wert not in GRUPPEN:
            raise ValueError("Unbekannte Gruppe")
        return wert

    @field_validator("erwartete_quelle")
    @classmethod
    def pruefe_quelle(cls, wert: str) -> str:
        if wert not in ERLAUBTE_TYPEN:
            raise ValueError("Unbekannte Quelle")
        return wert

    @field_validator("soll_titel")
    @classmethod
    def pruefe_titel(cls, wert: str | list[str] | None) -> str | list[str] | None:
        if wert is not None:
            teile = [wert] if isinstance(wert, str) else wert
            if not teile or any(not teil.strip() for teil in teile):
                raise ValueError("Leere Soll-Titel sind nicht erlaubt")
        return wert


def lade_fragen(pfad: Path) -> list[Frage]:
    fragen = []
    nummern: set[int] = set()
    for zeile, text in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
        try:
            frage = Frage.model_validate(json.loads(text))
        except (ValueError, ValidationError):
            raise ValueError(f"Ungueltige Fragendatei in Zeile {zeile} (Details unterdrueckt)") from None
        if frage.nr in nummern:
            raise ValueError(f"Doppelte Fragennummer in Zeile {zeile}")
        nummern.add(frage.nr)
        fragen.append(frage)
    if not fragen:
        raise ValueError("Die Fragendatei ist leer")
    return fragen


@dataclass(frozen=True)
class Bewertung:
    nr: int
    gruppe: str
    streng: bool
    rang: int | None
    trefferzahl: int
    dauer_ms: float
    teilweise: bool = False

    @property
    def reziproker_rang(self) -> float:
        return 1 / self.rang if self.rang is not None else 0.0


def bewerte(frage: Frage, treffer: Sequence[Treffer], limit: int,
            dauer_ms: float = 0.0, teilweise: bool = False) -> Bewertung:
    """Ersten passenden Rang innerhalb Top-N ermitteln, ohne DB-Zugriff."""
    teile = [frage.soll_titel] if isinstance(frage.soll_titel, str) else frage.soll_titel
    quellen = {frage.erwartete_quelle}
    if frage.erwartete_quelle == "objekt":
        quellen.add("objekt_dokument_seite")
    if frage.erwartete_quelle == "katplan_element":
        quellen.add("katplan_seite")
    rang = next((r for r, t in enumerate(treffer[:limit], 1)
                 if (any(teil.casefold() in t.titel.casefold() for teil in teile)
                     if teile else t.quelle in quellen)), None)
    return Bewertung(frage.nr, frage.gruppe, teile is not None, rang,
                     len(treffer[:limit]), dauer_ms, teilweise)


@dataclass(frozen=True)
class Aggregat:
    fragen: int
    recall: float
    mrr: float
    streng: float
    schwach: float
    p50_ms: float
    p95_ms: float
    teilweise: int


def aggregiere(bewertungen: Sequence[Bewertung]) -> dict[str, Aggregat]:
    """Makromittel je Gruppe und ueber alle Fragen (Nearest-Rank-Quantile)."""
    gruppen: dict[str, list[Bewertung]] = defaultdict(list)
    for wert in bewertungen:
        gruppen[wert.gruppe].append(wert)
    if bewertungen:
        gruppen["gesamt"] = list(bewertungen)
    ergebnis = {}
    for gruppe, werte in gruppen.items():
        anzahl = len(werte)
        zeiten = sorted(w.dauer_ms for w in werte)
        streng = sum(w.streng for w in werte) / anzahl
        ergebnis[gruppe] = Aggregat(
            anzahl, sum(w.rang is not None for w in werte) / anzahl,
            sum(w.reziproker_rang for w in werte) / anzahl, streng, 1 - streng,
            zeiten[math.ceil(anzahl * .5) - 1], zeiten[math.ceil(anzahl * .95) - 1],
            sum(w.teilweise for w in werte),
        )
    return ergebnis


def schreibe_ausgabe(messungen: dict[str, list[Bewertung]], limit: int, ausgabe: TextIO) -> None:
    csv_ausgabe = csv.writer(ausgabe, delimiter=";", lineterminator="\n")
    csv_ausgabe.writerow(["Modus", "Gruppe", "Fragen", f"Recall@{limit}", "MRR",
                          "Anteil_streng", "Anteil_schwach", "p50_ms", "p95_ms", "Teilweise"])
    for modus, werte in messungen.items():
        for gruppe, a in aggregiere(werte).items():
            csv_ausgabe.writerow([modus, gruppe, a.fragen, f"{a.recall:.4f}", f"{a.mrr:.4f}",
                                  f"{a.streng:.4f}", f"{a.schwach:.4f}",
                                  f"{a.p50_ms:.2f}", f"{a.p95_ms:.2f}", a.teilweise])
        csv_ausgabe.writerow([modus, "ohne_Treffer", ",".join(str(w.nr) for w in werte if not w.trefferzahl)])


def schreibe_review(ausgabe: TextIO, daten: Sequence[tuple[str, Frage, Sequence[Treffer]]]) -> None:
    writer = csv.writer(ausgabe, delimiter=";", lineterminator="\n")
    writer.writerow(["nr", "gruppe", "modus", "frage", "erwartete_quelle", "soll_titel",
                     "rang", "quelle", "titel", "fundstelle", "grund", "distanz"])
    for modus, frage, treffer in daten:
        basis = [frage.nr, frage.gruppe, modus, frage.frage, frage.erwartete_quelle,
                 json.dumps(frage.soll_titel, ensure_ascii=False) if frage.soll_titel is not None else ""]
        if not treffer:
            writer.writerow([*basis, "", "", "", "", "", ""])
        for rang, t in enumerate(treffer[:5], 1):
            # Der aktuelle Treffervertrag transportiert keine Vektordistanz.
            writer.writerow([*basis, rang, t.quelle, t.titel, t.fundstelle,
                             ",".join(t.grund), getattr(t, "distanz", "")])


class ModusFehler(Exception):
    """Abbruchgrund ohne Suchtexte oder Exceptiondetails."""


def evaluiere_modus(db: Session, user: User, fragen: Sequence[Frage], modus: str, limit: int,
                   ) -> tuple[list[Bewertung], list[tuple[str, Frage, Sequence[Treffer]]]]:
    bewertungen = []
    review: list[tuple[str, Frage, Sequence[Treffer]]] = []
    with patch.object(settings, "SEARCH_SEMANTIC_ENABLED", modus != "volltext"):
        if modus != "volltext":
            if not vektor.vektor_faehig(db):
                raise ModusFehler("DB ist nicht vektorfaehig (MariaDB >=11.7 erforderlich)")
            try:
                embedding.EmbeddingProvider().embed_anfrage("Suchtest")
            except embedding.EmbeddingFehler:
                raise ModusFehler("Embedding-Provider nicht erreichbar oder Antwort ungueltig") from None
        # Gleiche Verzeichnisrechte wie UI/MCP im globalen Kontext.
        kontakt_zugriff: KontaktZugriff = ("voll" if user.org_id is not None
                                         and kontakte_modul_aktiv(user.org_id, db)
                                         and can_view_kontakt_directory(user) else "keiner")
        for frage in fragen:
            start = perf_counter()
            ergebnis = abfrage.suche(db, user, SuchAnfrage(q=frage.frage, limit=limit),
                                    kontakt_zugriff=kontakt_zugriff)
            dauer_ms = (perf_counter() - start) * 1000
            if modus != "volltext" and (
                "semantisch" not in ergebnis.modi or "Semantik nicht verfügbar" in ergebnis.hinweise
            ):
                raise ModusFehler(f"Frage {frage.nr}: Semantikstufe nicht verfuegbar; Org-Index/Provider pruefen")
            treffer = [t for t in ergebnis.treffer if "semantisch" in t.grund] if modus == "semantisch" else (
                ergebnis.treffer
            )
            bewertungen.append(bewerte(frage, treffer, limit, dauer_ms, ergebnis.teilweise))
            review.append((modus, frage, treffer[:5]))
    return bewertungen, review


def main() -> int:
    parser = argparse.ArgumentParser(description="Suchqualitaet ohne Suchtexte in der Standardausgabe messen.")
    parser.add_argument("--org", type=int, required=True)
    parser.add_argument("--user", type=int, required=True)
    parser.add_argument("--fragen", type=Path, default=Path("scripts/data/suche_fragen.jsonl"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--modi", default="volltext,hybrid")
    parser.add_argument("--review-csv", type=Path)
    parser.add_argument("--max-distanz", type=float)
    args = parser.parse_args()
    modi = list(dict.fromkeys(args.modi.split(",")))
    if not modi or any(m not in MODI for m in modi):
        parser.error("--modi erlaubt volltext,hybrid,semantisch (kommagetrennt)")
    if not 1 <= args.limit <= 50:
        parser.error("--limit muss zwischen 1 und 50 liegen")
    if args.max_distanz is not None and not 0 <= args.max_distanz <= 2:
        parser.error("--max-distanz muss zwischen 0 und 2 liegen")
    if args.review_csv and args.review_csv.resolve() == args.fragen.resolve():
        parser.error("Review-CSV darf die Fragendatei nicht ueberschreiben")
    try:
        fragen = lade_fragen(args.fragen)
    except OSError:
        parser.error("Fragendatei nicht lesbar")
    except ValueError as exc:
        parser.error(str(exc))
    logging.disable(logging.CRITICAL)
    messungen: dict[str, list[Bewertung]] = {}
    review: list[tuple[str, Frage, Sequence[Treffer]]] = []
    fehlgeschlagen = False
    try:
        with SessionLocal() as db, ExitStack() as stack:
            set_tenant_context(db, None)
            db.get_bind().engine.echo = False
            if args.max_distanz is not None:
                stack.enter_context(patch.object(settings, "SEARCH_SEMANTIC_MAX_DISTANZ", args.max_distanz))
            user = db.scalar(select(User).where(User.id == args.user, User.org_id == args.org, User.active.is_(True)))
            if user is None:
                parser.error("Kein aktiver Benutzer mit dieser ID in der Organisation")
            for modus in modi:
                try:
                    werte, zeilen = evaluiere_modus(db, user, fragen, modus, args.limit)
                except ModusFehler as exc:
                    sys.stderr.write(f"{modus}: abgebrochen: {exc}\n")
                    fehlgeschlagen = True
                    continue
                except Exception:
                    sys.stderr.write(f"{modus}: abgebrochen: Suchlauf fehlgeschlagen (Details unterdrueckt)\n")
                    fehlgeschlagen = True
                    continue
                messungen[modus] = werte
                review.extend(zeilen)
        schreibe_ausgabe(messungen, args.limit, sys.stdout)
        if args.review_csv:
            with args.review_csv.open("w", encoding="utf-8", newline="") as datei:
                schreibe_review(datei, review)
    except Exception:
        sys.stderr.write("Auswertung fehlgeschlagen (DB-/Dateizugriff; Details unterdrueckt)\n")
        return 1
    return int(fehlgeschlagen)


if __name__ == "__main__":
    sys.exit(main())
