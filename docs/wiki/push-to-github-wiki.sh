#!/usr/bin/env bash
# Dieses Skript pusht alle Wiki-Seiten aus docs/wiki/ ins GitHub-Wiki.
# Voraussetzung: GitHub-Wiki muss bereits initialisiert sein.
# Das geht über die GitHub-Webseite: Repository → Wiki → "Create the first page"
# Danach kann dieses Skript ausgeführt werden.

set -e

REPO="https://github.com/BattloXX/Einsatzcockpit.wiki.git"
WIKI_DIR="$(dirname "$0")"
TMP_DIR=$(mktemp -d)

echo "Klone Wiki-Repository..."
git clone "$REPO" "$TMP_DIR"

echo "Kopiere Wiki-Seiten..."
# Alle .md Dateien außer diesem Skript kopieren:
find "$WIKI_DIR" -name "*.md" -exec cp {} "$TMP_DIR/" \;

# Eingebettete Bilder mit ihrer relativen Verzeichnisstruktur kopieren:
if [ -d "$WIKI_DIR/images" ]; then
  mkdir -p "$TMP_DIR/images"
  cp -R "$WIKI_DIR/images/." "$TMP_DIR/images/"
fi

cd "$TMP_DIR"
git config user.email "johannes@battlogg.org"
git config user.name "Johannes Battlogg"
git add .
git commit -m "docs: vollständige Wiki-Dokumentation (DE)" || echo "Keine Änderungen"
git push origin master

echo "Aufräumen..."
rm -rf "$TMP_DIR"

echo "Wiki erfolgreich aktualisiert!"
echo "Aufrufbar unter: https://github.com/BattloXX/Einsatzcockpit/wiki"
