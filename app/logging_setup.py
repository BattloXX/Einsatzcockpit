"""Schlichtes Prozess-Logging vor dem Import der MCP-Server einrichten."""

import logging
import sys


def einrichten() -> None:
    """Ohne vorhandenen Stream-Handler nach stderr protokollieren."""
    root = logging.getLogger()
    if not any(isinstance(handler, logging.StreamHandler) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)


def rich_handler_entfernen() -> None:
    """Rich-Handler entfernen, ohne Rich eigens zu importieren oder Puffer anzutasten."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if any(klasse.__module__ == "rich.logging" and klasse.__name__ == "RichHandler"
               for klasse in type(handler).__mro__):
            root.removeHandler(handler)
            handler.close()
    einrichten()
