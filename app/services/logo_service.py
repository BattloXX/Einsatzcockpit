"""Kleine Organisationslogos ohne EXIF-Metadaten."""

from io import BytesIO

from PIL import Image, ImageOps


def logo_verkleinern(daten: bytes, endung: str) -> bytes:
    """Seitenverhaeltnis, Dateiformat und PNG-Transparenz bleiben erhalten."""
    if endung.lower() == ".svg":
        return daten
    with Image.open(BytesIO(daten)) as quelle:
        formatname = quelle.format
        # Bereits kleine, bereinigte Logos nicht erneut verlustbehaftet kodieren.
        if max(quelle.size) <= 512 and not quelle.getexif() and not (
            set(quelle.info) - {"jfif", "jfif_version", "jfif_unit", "jfif_density", "transparency"}
        ):
            return daten
        bild = ImageOps.exif_transpose(quelle)
        if "transparency" in bild.info:
            bild = bild.convert("RGBA")
        bild.thumbnail((512, 512), Image.Resampling.LANCZOS)
        bild.info.clear()
        ausgabe = BytesIO()
        bild.save(ausgabe, format=formatname, optimize=True)
        return ausgabe.getvalue()
