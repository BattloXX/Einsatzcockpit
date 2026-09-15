"""Gemeinsame Konfiguration für serverseitige OpenStreetMap-Kachelzugriffe."""

OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
# Die OSM-Tile-Nutzungsrichtlinie verlangt bei serverseitigen Abrufen einen
# identifizierbaren User-Agent.  Beide Verbraucher (statische Karten und der
# Browser-Tile-Proxy) verwenden bewusst dieselbe Kennung.
OSM_TILE_USER_AGENT = "Einsatzcockpit/1.0 (+https://einsatzcockpit.com)"
OSM_TILE_REFERER = "https://einsatzcockpit.com/"
