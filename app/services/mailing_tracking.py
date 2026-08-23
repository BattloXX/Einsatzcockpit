"""Per-recipient open and click tracking markup."""

import html as html_lib
import re
from urllib.parse import quote

from app.core.security import sign_mailing_track_token

_LINK = re.compile(r'(<a\b[^>]*?\bhref\s*=\s*)(["\'])(https?://.*?)(\2)', re.I | re.S)


def rewrite_links(
    html: str, queue_item_id: int, org_id: int, *, track_opens: bool = True, track_clicks: bool = True
) -> str:
    token = sign_mailing_track_token(queue_item_id, org_id)
    if track_clicks:
        html = _LINK.sub(
            lambda m: (
                f"{m.group(1)}{m.group(2)}/mailing/c/{token}"
                f"?u={quote(html_lib.unescape(m.group(3)), safe='')}{m.group(4)}"
            ),
            html,
        )
    if track_opens:
        pixel = f'<img src="/mailing/t/{token}.png" width="1" height="1" alt="" style="display:none">'
        html = (
            re.sub(r"</body\s*>", pixel + "</body>", html, count=1, flags=re.I)
            if re.search(r"</body\s*>", html, re.I)
            else html + pixel
        )
    return html
