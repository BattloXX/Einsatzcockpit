"""Minimaler SVG-Balkendiagramm-Generator für PDF-Berichte.

WeasyPrint rendert kein JavaScript – Chart.js (wie in der Web-Statistik) ist im
PDF nicht nutzbar. Diese Helfer erzeugen statische, gestapelte Balkendiagramme
als SVG-String, den WeasyPrint direkt zeichnet. Bewusst abhängigkeitsfrei
(keine matplotlib o. Ä.) und rein funktional.
"""
from __future__ import annotations

import html
import math
from decimal import Decimal

# Farben analog zur Web-Statistik (stats/fahrtenbuch.html).
TYP_COLORS = {
    "einsatz": "#d42225",
    "uebung": "#1877f2",
    "taetigkeit": "#0f766e",
    "sonstige": "#6b7280",
}
TYP_LABELS = {
    "einsatz": "Einsatz",
    "uebung": "Übung",
    "taetigkeit": "Tätigkeit",
    "sonstige": "Sonstige",
}

# Palette für Personen-Segmente (Maschinisten je Fahrzeug) – kräftige, gut
# unterscheidbare Farben, zyklisch verwendet.
PERSON_PALETTE = [
    "#b71c1c", "#0d47a1", "#1b5e20", "#e65100", "#4a148c", "#006064",
    "#880e4f", "#33691e", "#3e2723", "#01579b", "#f9a825", "#4e342e",
    "#ad1457", "#00695c", "#283593", "#558b2f",
]


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _num(v) -> float:
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _fmt(v) -> str:
    f = _num(v)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:.1f}"


def _nice_max(v: float) -> float:
    """Rundet den Maximalwert auf einen „schönen" Achsen-Endwert auf."""
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10 ** exp
    frac = v / base
    if frac <= 1:
        nice = 1
    elif frac <= 2:
        nice = 2
    elif frac <= 5:
        nice = 5
    else:
        nice = 10
    return nice * base


def _truncate(label: str, n: int = 16) -> str:
    return label if len(label) <= n else label[: n - 1] + "…"


def stacked_bar_svg(
    labels: list[str],
    series: list[dict],
    *,
    width: int = 1000,
    height: int = 560,
    y_title: str = "Anzahl",
) -> str:
    """Gestapeltes Balkendiagramm als SVG-String.

    ``labels``  – Kategorien auf der X-Achse (ein Balken je Eintrag).
    ``series``  – Liste von ``{"name": str, "color": "#rrggbb", "data": [..]}``;
                  jeder ``data``-Vektor hat dieselbe Länge wie ``labels``.
    """
    n = len(labels)
    totals = [sum(_num(s["data"][i]) for s in series) for i in range(n)]
    if n == 0 or max(totals, default=0) <= 0:
        return _empty_svg(width, height)

    # Layout
    m_left, m_right, m_top = 58, 24, 26
    x_label_h = 96      # Platz für gedrehte X-Beschriftung
    legend_h = 34 * (1 + (len(series) - 1) // 4)  # 4 Legenden-Einträge pro Zeile
    plot_top = m_top
    plot_bottom = height - x_label_h - legend_h
    plot_left = m_left
    plot_right = width - m_right
    plot_w = plot_right - plot_left
    plot_h = plot_bottom - plot_top

    y_max = _nice_max(max(totals))
    ticks = 5
    band = plot_w / n
    bar_w = min(band * 0.62, 90)

    def y_of(val: float) -> float:
        return plot_bottom - (val / y_max) * plot_h

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'style="width:100%;height:auto;max-height:100%" '
        f'font-family="Arial, sans-serif">'
    )

    # Y-Gitter + Beschriftung
    for t in range(ticks + 1):
        val = y_max * t / ticks
        y = y_of(val)
        parts.append(
            f'<line x1="{plot_left:.1f}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}" '
            f'stroke="#e0e0e0" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{plot_left - 8:.1f}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="13" fill="#666">{_esc(_fmt(val))}</text>'
        )
    # Y-Achsentitel
    parts.append(
        f'<text x="16" y="{plot_top + plot_h / 2:.1f}" text-anchor="middle" font-size="13" '
        f'fill="#666" transform="rotate(-90 16 {plot_top + plot_h / 2:.1f})">{_esc(y_title)}</text>'
    )

    # Balken (gestapelt) + X-Beschriftung + Summenlabel
    for i, label in enumerate(labels):
        cx = plot_left + band * i + band / 2
        bx = cx - bar_w / 2
        y_cursor: float = plot_bottom
        for s in series:
            val = _num(s["data"][i])
            if val <= 0:
                continue
            h = (val / y_max) * plot_h
            y_cursor -= h
            parts.append(
                f'<rect x="{bx:.1f}" y="{y_cursor:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                f'fill="{_esc(s["color"])}"/>'
            )
            if h >= 20:
                parts.append(
                    f'<text x="{cx:.1f}" y="{y_cursor + h / 2 + 5:.1f}" text-anchor="middle" '
                    f'font-size="13" fill="#fff" font-weight="700">{_esc(_fmt(val))}</text>'
                )
        # Summe über dem Balken
        total = totals[i]
        parts.append(
            f'<text x="{cx:.1f}" y="{y_of(total) - 7:.1f}" text-anchor="middle" '
            f'font-size="13" fill="#333" font-weight="700">{_esc(_fmt(total))}</text>'
        )
        # X-Label gedreht
        parts.append(
            f'<text x="{cx:.1f}" y="{plot_bottom + 16:.1f}" text-anchor="end" font-size="13" '
            f'fill="#333" transform="rotate(-35 {cx:.1f} {plot_bottom + 16:.1f})">'
            f'{_esc(_truncate(label))}</text>'
        )

    # X-Achse
    parts.append(
        f'<line x1="{plot_left:.1f}" y1="{plot_bottom:.1f}" x2="{plot_right:.1f}" '
        f'y2="{plot_bottom:.1f}" stroke="#999" stroke-width="1.5"/>'
    )

    # Legende
    per_row = 4
    sw = 18
    row_gap = 30
    col_w = plot_w / per_row
    legend_top = height - legend_h + 6
    for idx, s in enumerate(series):
        row = idx // per_row
        col = idx % per_row
        lx = plot_left + col * col_w
        ly = legend_top + row * row_gap
        parts.append(
            f'<rect x="{lx:.1f}" y="{ly:.1f}" width="{sw}" height="{sw}" fill="{_esc(s["color"])}"/>'
        )
        parts.append(
            f'<text x="{lx + sw + 6:.1f}" y="{ly + sw - 4:.1f}" font-size="13" fill="#333">'
            f'{_esc(_truncate(s["name"], 22))}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _empty_svg(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'style="width:100%;height:auto;max-height:100%" font-family="Arial, sans-serif">'
        f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" font-size="20" '
        f'fill="#999">Keine Daten im gewählten Zeitraum</text></svg>'
    )


# ── Diagramm-Aufbau aus den Bericht-Daten ──────────────────────────────────────

def _typ_series(rows: list[dict]) -> list[dict]:
    """Segmente Einsatz/Übung/Tätigkeit/Sonstige für eine Zeilenliste."""
    return [
        {"name": TYP_LABELS[t], "color": TYP_COLORS[t], "data": [r[t] for r in rows]}
        for t in ("einsatz", "uebung", "taetigkeit", "sonstige")
    ]


def build_bericht_charts(daten: dict) -> dict[str, str]:
    """Erzeugt die drei SVG-Diagramme (Fahrzeuge, Maschinisten, Maschinisten je Fahrzeug)."""
    # 1) Einsätze/Übungen je Fahrzeug
    fz = daten.get("fahrzeuge", [])
    svg_fahrzeuge = stacked_bar_svg(
        [r["label"] for r in fz], _typ_series(fz), y_title="Fahrten",
    )

    # 2) Maschinisten – alle Fahrzeuge
    ma = daten.get("maschinisten", [])
    svg_maschinisten = stacked_bar_svg(
        [r["label"] for r in ma], _typ_series(ma), y_title="Fahrten",
    )

    # 3) Maschinisten je Fahrzeug: X = Fahrzeuge, Segmente = Personen (Gesamt),
    #    gleiche Person über alle Fahrzeuge in gleicher Farbe.
    je = daten.get("je_fahrzeug", [])
    person_order: list[str] = []
    for grp in je:
        for z in grp["zeilen"]:
            if z["label"] not in person_order:
                person_order.append(z["label"])
    color_of = {p: PERSON_PALETTE[i % len(PERSON_PALETTE)] for i, p in enumerate(person_order)}
    fz_labels = [grp["label"] for grp in je]
    series3 = []
    for p in person_order:
        data = []
        for grp in je:
            wert = next((z["gesamt"] for z in grp["zeilen"] if z["label"] == p), 0)
            data.append(wert)
        series3.append({"name": p, "color": color_of[p], "data": data})
    svg_je_fahrzeug = stacked_bar_svg(fz_labels, series3, y_title="Fahrten (gesamt)")

    return {
        "fahrzeuge": svg_fahrzeuge,
        "maschinisten": svg_maschinisten,
        "je_fahrzeug": svg_je_fahrzeug,
    }
