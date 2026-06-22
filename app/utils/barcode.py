"""Einfacher CODE128B-Barcode-Generator – erzeugt ein SVG ohne externe Bibliotheken."""
from __future__ import annotations

# Kodierungstabelle: Index 0-95 = ASCII 32-127, Index 96-102 = Sonderzeichen,
# Index 103=Start A, 104=Start B, 105=Start C, 106=Stop
_PATTERNS: tuple[str, ...] = (
    "212222","222122","222221","121223","121322","131222","122213","122312",
    "132212","221213","221312","231212","112232","122132","122231","113222",
    "123122","123221","223211","221132","221231","213212","223112","312131",
    "311222","321122","321221","312212","322112","322211","212123","212321",
    "232121","111323","131123","131321","112313","132113","132311","211313",
    "231113","231311","112133","112331","132131","113123","113321","133121",
    "313121","211331","231131","213113","213311","213131","311123","311321",
    "331121","312113","312311","332111","314111","221411","431111","111224",
    "111422","121124","121421","141122","141221","112214","112412","122114",
    "122411","142112","142211","241211","221114","413111","241112","134111",
    "111242","121142","121241","114212","124112","124211","411212","421112",
    "421211","212141","214121","412121","111143","111341","131141","114113",
    "114311","411113","411311","113141","114131","311141","411131","211412",
    "211214","211232","2331112",
)

_START_B = 104
_STOP    = 106


def code128b_svg(value: str) -> str:
    """Gibt einen CODE128B-Barcode als SVG-String zurueck, oder '' bei Fehler."""
    if not value:
        return ""
    # CODE128B: nur ASCII 32–127
    if not all(32 <= ord(c) <= 127 for c in value):
        return ""

    indices = [ord(c) - 32 for c in value]
    check   = (_START_B + sum(idx * (pos + 1) for pos, idx in enumerate(indices))) % 103

    symbol_ids = [_START_B] + indices + [check, _STOP]

    modules: list[int] = []
    for sid in symbol_ids:
        for ch in _PATTERNS[sid]:
            modules.append(int(ch))

    quiet     = 10
    total_mod = sum(modules) + quiet * 2

    rects: list[str] = []
    x       = quiet
    is_bar  = True
    for w in modules:
        if is_bar:
            rects.append(f'<rect x="{x}" y="0" width="{w}" height="100"/>')
        x      += w
        is_bar  = not is_bar

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_mod} 100" '
        f'preserveAspectRatio="none" '
        f'style="display:block;width:100%;height:100%;">'
        f'{"".join(rects)}'
        f"</svg>"
    )
