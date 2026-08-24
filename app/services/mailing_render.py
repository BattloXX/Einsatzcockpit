"""Sicheres Rendering benutzerverfasster Mailing-Templates.

Bewusst wird nicht die globale Template-Umgebung aus ``app.core.templating``
verwendet: Mailing-Inhalte sind Benutzereingaben. Die Sandbox, StrictUndefined
und eine Primitive-Whitelist verhindern Objektzugriff/SSTI.
"""

import html as html_lib
import re

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

ALLOWED_CONTEXT = {"vorname", "nachname", "email", "org_name", "empfaenger_name"}
_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=True)


def append_unsubscribe_footer(html: str, text: str | None, unsub_url: str) -> tuple[str, str]:
    """Ergaenzt den verpflichtenden sichtbaren Abmeldehinweis."""
    escaped_url = html_lib.escape(unsub_url, quote=True)
    footer_html = (
        '<p style="margin-top:24px;color:#6b7280;font-size:12px">'
        f'Sie möchten keine weiteren E-Mails erhalten? <a href="{escaped_url}">Jetzt abmelden</a>.'
        "</p>"
    )
    if re.search(r"</body\s*>", html, re.I):
        html = re.sub(r"</body\s*>", footer_html + "</body>", html, count=1, flags=re.I)
    else:
        html += footer_html
    footer_text = f"Sie möchten keine weiteren E-Mails erhalten? Jetzt abmelden: {unsub_url}"
    return html, f"{text.rstrip()}\n\n{footer_text}" if text else footer_text


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
