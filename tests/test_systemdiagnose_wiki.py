"""Wiki-Push publiziert unveraendert: neue Querverweise muessen aufloesbar sein."""
import re
from pathlib import Path


def test_systemadmin_wiki_links_exist_without_md():
    root = Path("docs/wiki")
    names = ("Administration-Systemdiagnose", "Administration-System-Admin-Ueberblick",
             "Administration-System-Admin-MCP", "Administration-Zwei-Faktor-Authentifizierung",
             "Administration-Einstellungen", "Home", "_Sidebar")
    for name in names:
        source = (root / (name + ".md")).read_text()
        for target in re.findall(r"\]\(([^)]+)\)", source):
            if "://" in target or target.startswith(("#", "/")):
                continue
            page = target.split("#")[0]
            assert not page.endswith(".md"), (name, target)
            assert (root / (page + ".md")).is_file(), (name, target)
