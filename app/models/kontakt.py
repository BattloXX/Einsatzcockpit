"""Zentrale, organisationsweite Kontakte und ihre Objekt-Zuordnungen."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.telefon import telefon_normalisiert
from app.core.tenant import TenantScoped
from app.db import Base

KONTAKT_TYP_PERSON = "person"
KONTAKT_TYP_STELLE = "stelle"


class Kontakt(TenantScoped, Base):
    """Zentraler Kontakt, der spaeter mehreren Objekten zugeordnet werden kann."""
    __tablename__ = "kontakt"
    __table_args__ = (Index("ix_kontakt_org_anzeigename", "org_id", "anzeigename"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    typ: Mapped[str] = mapped_column(String(20), nullable=False, default=KONTAKT_TYP_PERSON)
    anzeigename: Mapped[str] = mapped_column(String(150), nullable=False)
    vorname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nachname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    funktion: Mapped[str | None] = mapped_column(String(150), nullable=True)
    organisation: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    erreichbarkeit: Mapped[str | None] = mapped_column(Text, nullable=True)
    notizen: Mapped[str | None] = mapped_column(Text, nullable=True)
    bild_pfad: Mapped[str | None] = mapped_column(String(500), nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archiviert: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    aktualisiert_am: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    erstellt_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    aktualisiert_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    telefone: Mapped[list[KontaktTelefon]] = relationship(
        back_populates="kontakt", cascade="all, delete-orphan", order_by="KontaktTelefon.sort"
    )
    kategorien: Mapped[list[KontaktKategorieZuordnung]] = relationship(
        back_populates="kontakt", cascade="all, delete-orphan"
    )
    anhaenge: Mapped[list[KontaktAnhang]] = relationship(
        back_populates="kontakt", cascade="all, delete-orphan"
    )
    externe_referenzen: Mapped[list[KontaktExterneReferenz]] = relationship(
        back_populates="kontakt", cascade="all, delete-orphan"
    )


class KontaktTelefon(TenantScoped, Base):
    """Telefonnummer eines zentralen Kontakts mit persistierter Normalform."""
    __tablename__ = "kontakt_telefon"
    __table_args__ = (Index("ix_kontakt_telefon_org_kontakt", "org_id", "kontakt_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kontakt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False
    )
    nummer: Mapped[str] = mapped_column(String(100), nullable=False)
    nummer_normalisiert: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bevorzugt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sms_eignung: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    kontakt: Mapped[Kontakt] = relationship(back_populates="telefone")

    @validates("nummer")
    def _normalisiere_nummer(self, _key: str, nummer: str) -> str:
        self.nummer_normalisiert = telefon_normalisiert(nummer)
        return nummer


class KontaktImportVorschau(TenantScoped, Base):
    """Kurzlebiger, serverseitiger Stand eines Kontaktimports vor der Uebernahme."""
    __tablename__ = "kontakt_import_vorschau"
    __table_args__ = (Index("ix_kontakt_import_vorschau_org_user", "org_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    zeilen_json: Mapped[str] = mapped_column(Text, nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class KontaktKategorie(TenantScoped, Base):
    """Frei pflegbare Kategorie fuer zentrale Kontakte."""
    __tablename__ = "kontakt_kategorie"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_kontakt_kategorie_org_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class KontaktKategorieZuordnung(TenantScoped, Base):
    """Zuordnung eines Kontakts zu einer Kontaktkategorie."""
    __tablename__ = "kontakt_kategorie_zuordnung"
    __table_args__ = (UniqueConstraint("kontakt_id", "kategorie_id", name="uq_kontakt_kategorie_zuordnung"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kontakt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False
    )
    kategorie_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("kontakt_kategorie.id", ondelete="CASCADE"), nullable=False
    )

    kontakt: Mapped[Kontakt] = relationship(back_populates="kategorien")
    kategorie: Mapped[KontaktKategorie] = relationship()


class KontaktAnhang(TenantScoped, Base):
    """Metadaten eines Kontakt-Anhangs; Speicherung folgt ObjektDokument."""
    __tablename__ = "kontakt_anhang"
    __table_args__ = (Index("ix_kontakt_anhang_org_kontakt", "org_id", "kontakt_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kontakt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False
    )
    dateiname: Mapped[str] = mapped_column(String(255), nullable=False)
    medientyp: Mapped[str] = mapped_column(String(100), nullable=False)
    groesse_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    speicher_pfad: Mapped[str] = mapped_column(String(500), nullable=False)
    hochgeladen_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    hochgeladen_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    kontakt: Mapped[Kontakt] = relationship(back_populates="anhaenge")


class KontaktExterneReferenz(TenantScoped, Base):
    """Stabile Zuordnung einer externen Quelle zu einem zentralen Kontakt."""
    __tablename__ = "kontakt_externe_referenz"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "quelle", "quelle_kontext", "extern_id",
            name="uq_kontakt_externe_referenz",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kontakt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False
    )
    quelle: Mapped[str] = mapped_column(String(50), nullable=False)
    quelle_kontext: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extern_id: Mapped[str] = mapped_column(String(150), nullable=False)

    kontakt: Mapped[Kontakt] = relationship(back_populates="externe_referenzen")


class ObjektKontaktFreigabe(TenantScoped, Base):
    """Kanal-Freigabe fuer einen konkreten, denormalisierten Kontaktwert am Objekt."""
    __tablename__ = "objekt_kontakt_freigabe"
    __table_args__ = (
        UniqueConstraint("objekt_kontakt_id", "kanal", "ziel_wert", name="uq_objekt_kontakt_freigabe"),
        Index("ix_objekt_kontakt_freigabe_org_kontakt", "org_id", "objekt_kontakt_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    objekt_kontakt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt_kontakt.id", ondelete="CASCADE"), nullable=False
    )
    kanal: Mapped[str] = mapped_column(String(10), nullable=False)
    ziel_wert: Mapped[str] = mapped_column(String(200), nullable=False)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
