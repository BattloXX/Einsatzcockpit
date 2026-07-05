"""QR-Code-Helfer: Data-URI-PNGs zum Einbetten in HTML/PDF.

Extrahiert aus ui_settings._generate_qr_datauri (dortige Funktion bleibt fuer
Bestandscode unveraendert); Farbvarianten fuer Dark-UI und Druck (weiss).
"""
from __future__ import annotations

import base64
import io
import logging

logger = logging.getLogger("einsatzleiter.qr")


def generate_qr_datauri(url: str, *, druck: bool = False) -> str | None:
    """Erzeugt einen QR-Code als data:image/png-URI. None bei Fehlern.

    druck=True: schwarz auf weiss (PDF/Objektblatt); sonst Dark-Theme-Farben.
    """
    try:
        import qrcode  # noqa: PLC0415
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=5, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        if druck:
            img = qr.make_image(fill_color="#000000", back_color="#ffffff")
        else:
            img = qr.make_image(fill_color="#dae2fd", back_color="#0b1326")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        logger.exception("QR-Erzeugung fehlgeschlagen")
        return None
