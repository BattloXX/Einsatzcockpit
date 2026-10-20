"""Gemeinsame Inline-SVG-Registry für Jinja und PDF."""

import logging
import re
from functools import cache
from pathlib import Path

from markupsafe import Markup, escape

from app.config import settings

GROESSEN = {"xs": 16, "sm": 20, "md": 24, "lg": 32}
FALLBACK = "help-square"
_STATUS = frozenset({"rot", "orange", "gelb", "gruen", "blau", "grau", "schwarz", "weiss"})
_warned: set[str] = set()


@cache
def _registry() -> dict[str, str]:
    icons = {}
    directory = Path(__file__).resolve().parents[1] / "static/icons/tabler"
    for path in sorted(directory.glob("*.svg")):
        match = re.search(r"<svg\b[^>]*>(.*?)</svg>", path.read_text(encoding="utf-8"), re.S)
        if match is None:
            raise ValueError(f"Ungültiges Icon: {path}")
        inner = re.sub(r'<path stroke="none" d="M0 0h24v24H0z" fill="none"\s*/>', "", match[1])
        inner = re.sub(r">\s+<", "><", inner).strip()
        if not inner:
            raise ValueError(f"Leeres Icon: {path}")
        icons[path.stem] = inner
    if FALLBACK not in icons:
        raise ValueError(f"Fallback-Icon fehlt: {FALLBACK}")
    return icons


def icon_namen() -> frozenset[str]:
    return frozenset(_registry())


def render_icon(name: str, size: str = "md", cls: str = "", label: str | None = None) -> Markup:
    if size not in GROESSEN:
        raise ValueError(f"Unbekannte Icon-Größe: {size}")
    icons = _registry()
    if name not in icons:
        if settings.ICONS_STRICT:
            raise KeyError(name)
        if name not in _warned:
            logging.getLogger("einsatzleiter.icons").warning("Unbekanntes Icon: %s", name)
            _warned.add(name)
        name = FALLBACK
    classes = escape(" ".join(f"ecp-icon ecp-icon--{size} {cls}".split()))
    aria = 'aria-hidden="true"' if label is None else f'role="img" aria-label="{escape(label)}"'
    title = "" if label is None else f"<title>{escape(label)}</title>"
    pixels = GROESSEN[size]
    return Markup(
        f'<svg class="{classes}" width="{pixels}" height="{pixels}" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round" {aria} focusable="false">{title}{icons[name]}</svg>'
    )


def status_dot(status: str, label: str, cls: str = "") -> Markup:
    if status not in _STATUS:
        raise ValueError(f"Unbekannter Status: {status}")
    classes = escape(" ".join(f"status-dot status-dot--{status} {cls}".split()))
    return Markup(
        f'<span class="{classes}" role="img" aria-label="{escape(label)}" '
        f'title="{escape(label)}"></span>'
    )
