"""Datensparsame technische Templates; freie Woerter werden nie ausgegeben."""

import re

from app.admin_mcp.masking import mask_text

# Nur feste technische Begriffe duerfen die Datenschutzgrenze passieren.
WORDS = frozenset(
    (
        "error warning critical info debug failed failure timeout connection refused "
        "reset retry attempt job worker gateway redis database db request response "
        "status http https get post put delete exception valueerror runtimeerror "
        "keyerror typeerror oserror permissionerror not found unavailable sending sent "
        "push sms print token expired invalid missing reconnect disconnected connected "
        "check diagnose systemdiagnose benachrichtigung fehlgeschlagen lauf fehler "
        "verbindung fehlgeschlagen versuch auftrag nicht verfuegbar"
    ).split()
)


def normalize_message(message: str) -> str:
    message = message[:64000]
    message = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[,.]\d+)?\s*", "", message)
    for pattern, replacement in (
        (r"https?://[^\s\"']+", "<url>"),
        (r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<email>"),
        (r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "<id>"),
        (r"(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|(?<!\w)(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.%]+", "<ip>"),
        (r"\"[^\"\n]*\"|'[^'\n]*'", "<str>"),
        (r"(?:[A-Za-z]:)?/[^\s,;]+", "<path>"),
        (r"\b(?:0x[0-9a-fA-F]+|[0-9a-fA-F]{8,})\b", "<id>"),
        (r"\b\d+(?:\.\d+)*\b", "<n>"),
    ):
        message = re.sub(pattern, replacement, message)
    tokens = re.findall(r"<(?:url|email|id|ip|str|path|n)>|[\w.-]+|[=:,;()\[\]]", message)
    template = " ".join(
        t if t.startswith("<") else t.lower() if t.lower() in WORDS else "<str>" if re.search(r"\w", t) else t
        for t in tokens
    )
    return mask_text(template)
