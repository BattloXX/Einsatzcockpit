"""HTML-Sanitisierung für Quill-Output (Einsatzjournal)."""
from __future__ import annotations

_ALLOWED_TAGS = {
    "p", "br", "strong", "em", "u", "s",
    "ul", "ol", "li",
    "a", "h3", "h4", "blockquote", "span",
}
_ALLOWED_ATTRIBUTES = {"a": {"href", "rel", "target"}}


def _stdlib_sanitize(raw: str) -> str:
    """Minimal allowlist sanitizer using only stdlib html.parser."""
    import html as _html
    from html.parser import HTMLParser

    class _Parser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self._out: list[str] = []
            self._void = {"br", "img", "hr", "input"}

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag not in _ALLOWED_TAGS:
                return
            allowed_attrs = _ALLOWED_ATTRIBUTES.get(tag, set())
            parts = [tag]
            for k, v in attrs:
                if k in allowed_attrs and v is not None:
                    parts.append(f'{k}="{_html.escape(v, quote=True)}"')
            self._out.append(f'<{" ".join(parts)}>')

        def handle_endtag(self, tag: str) -> None:
            if tag in _ALLOWED_TAGS and tag not in self._void:
                self._out.append(f"</{tag}>")

        def handle_data(self, data: str) -> None:
            self._out.append(_html.escape(data))

        def handle_entityref(self, name: str) -> None:
            self._out.append(f"&{name};")

        def handle_charref(self, name: str) -> None:
            self._out.append(f"&#{name};")

    parser = _Parser()
    parser.feed(raw)
    return "".join(parser._out)


def sanitize_html(raw: str | None) -> str | None:
    """Bereinigt Quill-HTML auf eine sichere Allowlist. Gibt None zurück wenn leer."""
    if not raw or not raw.strip():
        return None
    try:
        import nh3
        cleaned = nh3.clean(
            raw,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            link_rel=None,
        )
    except ImportError:
        cleaned = _stdlib_sanitize(raw)
    return cleaned or None
