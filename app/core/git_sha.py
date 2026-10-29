"""Git-Revision ohne Subprozess und ohne Abhaengigkeit von Settings."""

import re
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{7,40}")


def valid_sha(value: str) -> bool:
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def lese_git_sha(projektwurzel: Path) -> str:
    try:
        git = projektwurzel / ".git"
        if git.is_file():
            verweis = git.read_text().strip()
            if not verweis.startswith("gitdir: "):
                return ""
            git = (projektwurzel / verweis[8:]).resolve()
        head = (git / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            if not ref.startswith("refs/") or ".." in ref.split("/"):
                return ""
            # Worktrees teilen Branch-Referenzen mit dem Haupt-Repository.
            gemeinsam = git
            if (git / "commondir").is_file():
                gemeinsam = (git / (git / "commondir").read_text().strip()).resolve()
            lose = gemeinsam / ref
            if lose.is_file():
                head = lose.read_text().strip()
            else:
                head = ""
                for zeile in (gemeinsam / "packed-refs").read_text().splitlines():
                    teile = zeile.split()
                    if len(teile) == 2 and teile[1] == ref:
                        head = teile[0]
                        break
        return head if len(head) == 40 and valid_sha(head) else ""
    except (OSError, UnicodeError, ValueError):
        # Archive und Docker-Images enthalten oft kein lesbares Git-Verzeichnis.
        # Die Anwendung muss trotzdem starten; eine unbekannte Revision bleibt leer.
        return ""
