"""HTML-Sanitisierung für Quill-Output (Einsatzjournal)."""
from __future__ import annotations

_ALLOWED_TAGS = {
    "p", "br", "strong", "em", "u", "s",
    "ul", "ol", "li",
    "a", "h3", "h4", "blockquote", "span",
}
_ALLOWED_ATTRIBUTES = {"a": {"href", "rel", "target"}}


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
        import html as _html
        cleaned = _html.escape(raw)
    return cleaned or None
