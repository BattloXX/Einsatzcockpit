"""Dokumentseiten-Drehung: serverseitige Bild-Rotation (_bild_response)."""
import io
import os
import tempfile
from pathlib import Path

from PIL import Image
from starlette.responses import FileResponse

from app.routers.ui_objekt_dokumente import _bild_response


def _tmp_png(w, h):
    img = Image.new("RGB", (w, h), "white")
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(f, "PNG")
    f.close()
    return Path(f.name)


def test_bild_response_ohne_rotation_ist_fileresponse():
    p = _tmp_png(40, 80)
    try:
        r = _bild_response(p, "image/png", 0)
        # rotation==0 -> unveraendert als FileResponse (kein PIL-Overhead)
        assert isinstance(r, FileResponse)
    finally:
        os.unlink(p)


def test_bild_response_90_grad_tauscht_dimensionen():
    p = _tmp_png(40, 80)  # Hochformat
    try:
        r = _bild_response(p, "image/png", 90)
        out = Image.open(io.BytesIO(r.body))
        assert out.size == (80, 40)  # 90°: Breite/Hoehe getauscht
    finally:
        os.unlink(p)


def test_bild_response_180_grad_behaelt_dimensionen():
    p = _tmp_png(40, 80)
    try:
        r = _bild_response(p, "image/png", 180)
        out = Image.open(io.BytesIO(r.body))
        assert out.size == (40, 80)
    finally:
        os.unlink(p)


def test_rotation_negativ_normalisiert():
    # -90 (links) == 270; _bild_response nimmt rotation % 360
    p = _tmp_png(40, 80)
    try:
        r = _bild_response(p, "image/png", 270)
        out = Image.open(io.BytesIO(r.body))
        assert out.size == (80, 40)
    finally:
        os.unlink(p)
