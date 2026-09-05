"""Regressionsschutz gegen wirkungslose BEM-Modifier in Jinja-Templates."""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "app" / "templates"
CSS_ROOT = REPO_ROOT / "app" / "static" / "css"

EXTENDS_RE = re.compile(r"{%\s*extends\s+['\"]([^'\"]+)['\"]")
INCLUDE_RE = re.compile(r"{%\s*(?:include|from|import)\s+['\"]([^'\"]+)['\"]")
STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
STYLESHEET_RE = re.compile(
    r"<link\b[^>]*\brel=['\"]stylesheet['\"][^>]*\bhref=['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
CLASS_ATTR_RE = re.compile(
    r"\bclass\s*=\s*(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL
)
MODIFIER_RE = re.compile(r"^[a-z][a-z0-9-]*--[a-z0-9-]+$")
CSS_CLASS_RE = re.compile(r"\.([a-z][a-z0-9-]*--[a-z0-9-]+)")
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _template_modifier(text: str) -> set[str]:
    modifier = set()
    for _quote, value in CLASS_ATTR_RE.findall(text):
        for token in value.split():
            if "{{" in token or "{%" in token or "|" in token:
                continue
            if MODIFIER_RE.fullmatch(token):
                modifier.add(token)
    return modifier


def _css_modifier(text: str) -> set[str]:
    return set(CSS_CLASS_RE.findall(CSS_COMMENT_RE.sub("", text)))


def _lokale_stylesheets(template_texts: list[str]) -> set[Path]:
    stylesheets = {CSS_ROOT / "app.css", CSS_ROOT / "tooltips.css"}
    for text in template_texts:
        for href in STYLESHEET_RE.findall(text):
            path = href.split("?", maxsplit=1)[0]
            marker = "/static/css/"
            if marker in path:
                stylesheets.add(CSS_ROOT / path.split(marker, maxsplit=1)[1])
    return {path for path in stylesheets if path.is_file()}


def _wurzeln(templates: dict[str, str], referenziert: set[str]) -> list[str]:
    """Tatsaechlich gerenderte Seiten: von keinem anderen Template eingebunden.

    Partials werden nicht einzeln geprueft, sondern im Kontext jeder Seite, die sie
    einbindet — nur dort steht fest, welche Styles zur Laufzeit erreichbar sind.
    """
    return sorted(name for name in templates if name not in referenziert)


def test_bem_modifier_sind_in_erreichbarem_css_definiert() -> None:
    templates = {
        str(path.relative_to(TEMPLATE_ROOT)): path.read_text(encoding="utf-8-sig")
        for path in TEMPLATE_ROOT.rglob("*.html")
    }
    # Abwaerts-Kanten: ein Template sieht die Styles seines Basistemplates
    # (``extends``) und aller Partials, die es selbst einbindet (``include``).
    # Bewusst NICHT umgekehrt: sonst gelten seitenlokale Styles fremder Seiten
    # ueber gemeinsame Partials als global erreichbar und die Pruefung ist wertlos.
    kanten = {
        name: {ref for ref in EXTENDS_RE.findall(text) + INCLUDE_RE.findall(text) if ref in templates}
        for name, text in templates.items()
    }
    referenziert = {ref for refs in kanten.values() for ref in refs}

    def abhaengigkeiten(name: str, gesehen: set[str] | None = None) -> set[str]:
        gesehen = set() if gesehen is None else gesehen
        if name in gesehen:
            return gesehen
        gesehen.add(name)
        for ref in kanten[name]:
            abhaengigkeiten(ref, gesehen)
        return gesehen

    fehler: set[tuple[str, str]] = set()
    for wurzel in _wurzeln(templates, referenziert):
        erreichbar = abhaengigkeiten(wurzel)
        texte = [templates[name] for name in erreichbar]
        css_text = "\n".join(
            path.read_text(encoding="utf-8") for path in _lokale_stylesheets(texte)
        )
        css_text += "\n" + "\n".join(style for text in texte for style in STYLE_RE.findall(text))
        definiert = _css_modifier(css_text)
        for name in sorted(erreichbar):
            for modifier in _template_modifier(templates[name]) - definiert:
                fehler.add((f"{wurzel} -> {name}" if name != wurzel else wurzel, modifier))

    assert not fehler, "Undefinierte CSS-Modifier:\n" + "\n".join(
        f"{template}: {modifier}" for template, modifier in sorted(fehler)
    )


# ── Probenplanung: sichtbare Formularfelder muessen das Formular-Design benutzen ──
#
# Der Modifier-Test oben greift nur bei falsch geschriebenen BEM-Modifiern. Ein
# komplett fehlendes class-Attribut ist fuer ihn unsichtbar -- genau dadurch sind die
# Formulare der Probenplanung ueber elf Phasen hinweg mit weissen Browser-Standard-
# feldern ausgeliefert worden (Bugfix 2026-09-05). Diese Pruefung deckt den Fall ab.

PROBENPLANUNG_ROOT = TEMPLATE_ROOT / "probenplanung"
FELD_RE = re.compile(r"<(input|select|textarea)\b[^>]*>", re.IGNORECASE)
# Diese Eingabearten tragen bewusst kein Feld-Design: unsichtbar, eigene Auszeichnung
# ueber .form-group--checkbox, oder es sind Schaltflaechen.
OHNE_FELDDESIGN_RE = re.compile(
    r"""type\s*=\s*["'](hidden|checkbox|radio|submit|button|image)["']""", re.IGNORECASE
)
FELDKLASSEN = ("form-input", "form-select", "form-control")


def test_probenplanung_felder_tragen_formularklassen():
    fehlend = []
    for pfad in sorted(PROBENPLANUNG_ROOT.glob("*.html")):
        for treffer in FELD_RE.finditer(pfad.read_text(encoding="utf-8")):
            tag = treffer.group(0)
            if OHNE_FELDDESIGN_RE.search(tag) or any(k in tag for k in FELDKLASSEN):
                continue
            fehlend.append(f"{pfad.name}: {tag[:120]}")
    assert not fehlend, (
        "Sichtbare Eingabefelder ohne Formularklasse -- sie werden mit weissem "
        "Browser-Standard statt im dunklen App-Design gerendert:\n" + "\n".join(fehlend)
    )


def test_probenplanung_beschriftungen_stehen_in_formulargruppen():
    """`<label>Text<feld></label>` legt die Beschriftung neben statt ueber das Feld
    und laesst die Felder unterschiedlich weit einruecken. Zielform ist
    `<div class="form-group"><label for="x">Text</label><feld id="x" ...></div>`."""
    nackt = re.compile(r"<label>[^<]*<(input|select|textarea)\b", re.IGNORECASE)
    fehlend = [
        f"{pfad.name}: {treffer.group(0)}"
        for pfad in sorted(PROBENPLANUNG_ROOT.glob("*.html"))
        for treffer in nackt.finditer(pfad.read_text(encoding="utf-8"))
    ]
    assert not fehlend, "Beschriftung nicht als .form-group ausgezeichnet:\n" + "\n".join(fehlend)


def test_checkliste_sticky_header_hat_keine_ausgleichslose_bleed_margin():
    """`.main-content` hat `padding: 0` (app/static/css/tailwind.input.css) -- eine
    negative Margin auf `.probe-checklist-sticky` faengt daher NICHTS ab, sondern
    laesst den Sticky-Header bei <=760px um den Margin-Betrag ueber den Viewport
    hinausragen (Bugfix 2026-09-05: gemessener horizontaler Ueberlauf 8px bei 390px
    Breite). Falls der Header spaeter erneut mit den Card-Kanten buendig gemacht
    werden soll, braucht es zuerst eine tatsaechliche Padding-Gegenstelle -- kein
    Copy-Paste einer negativen Margin ohne sie."""
    css = (PROBENPLANUNG_ROOT / "_checkliste.html").read_text(encoding="utf-8")
    treffer = re.findall(r"\.probe-checklist-sticky\{([^}]*)\}", css)
    assert treffer, "Regel .probe-checklist-sticky nicht gefunden"
    for regel in treffer:
        assert "margin" not in regel or "-.5rem" not in regel and "-0.5rem" not in regel, (
            f".probe-checklist-sticky traegt wieder eine negative Margin ohne Padding-Gegenstelle: {regel}"
        )
