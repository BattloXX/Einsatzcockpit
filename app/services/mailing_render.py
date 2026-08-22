"""Sicheres Rendering benutzerverfasster Mailing-Templates.

Bewusst wird nicht die globale Template-Umgebung aus ``app.core.templating``
verwendet: Mailing-Inhalte sind Benutzereingaben. Die Sandbox, StrictUndefined
und eine Primitive-Whitelist verhindern Objektzugriff/SSTI.
"""

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

ALLOWED_CONTEXT = {"vorname", "nachname", "email", "org_name", "empfaenger_name"}
_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=True)


def render_mailing(source: str, context: dict) -> str:
    safe = {
        k: v for k, v in context.items() if k in ALLOWED_CONTEXT and isinstance(v, (str, int, float, bool, type(None)))
    }
    return _env.from_string(source or "").render(safe)


def render_template(subject: str, body_html: str, body_text: str | None, context: dict) -> tuple[str, str, str | None]:
    return (
        render_mailing(subject, context),
        render_mailing(body_html, context),
        (render_mailing(body_text, context) if body_text else None),
    )
