"""Validierter Anfrage- und Ergebnisvertrag der zentralen Suche."""

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.suche.quellen import html_zu_text

ERLAUBTE_TYPEN = (
    "objekt", "objekt_dokument_seite", "katplan_element", "katplan_seite",
    "gsl_stelle", "gsl_meldung", "gsl_einheit", "einsatz", "einsatz_meldung",
    "strassensperre", "wasserstelle", "kontakt",
)


class SuchAnfrage(BaseModel):
    q: str
    kontext: Literal["global", "einsatz", "gsl"] = "global"
    kontext_id: int | None = None
    typen: list[str] = Field(default_factory=list)
    plan_id: int | None = None
    filter: dict[str, Any] = Field(default_factory=dict)
    inkl_erledigt: bool = False
    ganze_org: bool = False
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lng: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    radius_m: int | None = Field(default=None, ge=1, le=5000)
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("q")
    @classmethod
    def pruefe_q(cls, wert: str) -> str:
        wert = wert.strip()
        if not 1 <= len(wert) <= 500:
            raise ValueError("Suchtext muss 1 bis 500 Zeichen enthalten")
        return wert

    @field_validator("typen")
    @classmethod
    def pruefe_typen(cls, wert: list[str]) -> list[str]:
        if any(typ not in ERLAUBTE_TYPEN for typ in wert):
            raise ValueError("Unbekannter Suchtyp")
        return list(dict.fromkeys(wert))

    @model_validator(mode="after")
    def pruefe_kontext_geo(self) -> Self:
        if self.kontext != "global" and self.kontext_id is None:
            raise ValueError("Kontext-ID fehlt")
        gesetzt = sum(w is not None for w in (self.lat, self.lng, self.radius_m))
        if gesetzt not in (0, 3):
            raise ValueError("Koordinaten und Radius nur gemeinsam")
        return self


class Treffer(BaseModel):
    quelle: str
    quell_id: str
    titel: str
    untertitel: str | None = None
    auszug: str = ""
    fundstelle: str | None = None
    status: str | None = None
    ist_aktuell: bool | None = None
    grund: list[Literal["exakt", "volltext", "live", "semantisch"]] = Field(default_factory=list)
    url: str
    lat: float | None = None
    lng: float | None = None
    distanz_m: int | None = None
    stand: datetime | None = None
    telefone: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("auszug")
    @classmethod
    def klartext(cls, wert: str) -> str:
        return html_zu_text(wert)[:300]


class SuchErgebnis(BaseModel):
    treffer: list[Treffer] = Field(default_factory=list)
    modi: list[str] = Field(default_factory=list)
    teilweise: bool = False
    index_stand: datetime | None = None
    hinweise: list[str] = Field(default_factory=list)
