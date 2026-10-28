"""fontTools darf pro PDF-Druck keine WARNING-Zeilen ("NOT subset; dropped") erzeugen."""
import logging

import app.main  # noqa: F401  (setzt die Logger-Schwellen beim Import)


def test_fonttools_warnungen_werden_unterdrueckt(caplog):
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("fontTools.subset").warning("meta NOT subset; don't know how to subset; dropped")
        logging.getLogger("fontTools.ttLib.ttFont").info("Rauschen")
        logging.getLogger("fontTools.subset").error("echter Fehler")
    assert [r.getMessage() for r in caplog.records if r.name.startswith("fontTools")] == ["echter Fehler"]
