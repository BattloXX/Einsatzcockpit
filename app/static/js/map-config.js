(function (window) {
  "use strict";

  var config = window.EinsatzcockpitMapConfig = window.EinsatzcockpitMapConfig || {};
  config.osmTileUrl = "/karten/osm-tile/{z}/{x}/{y}.png";
  config.osmAttribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>';
  config.osmMaxZoom = 19;

  config.osmTileOptions = function (options) {
    var defaults = {
      attribution: config.osmAttribution,
      maxZoom: config.osmMaxZoom
    };
    return Object.assign(defaults, options || {});
  };

  config.addOsmTileLayer = function (map, options) {
    if (!window.L) return null;

    var layer = window.L.tileLayer(config.osmTileUrl, config.osmTileOptions(options));
    var consecutiveErrors = 0;
    var notice = null;

    function showUnavailableNotice() {
      if (notice) return;
      notice = window.L.DomUtil.create("div", "map-tile-error-notice");
      notice.setAttribute("role", "status");
      notice.textContent = "Kartenhintergrund derzeit nicht verfügbar";
      Object.assign(notice.style, {
        position: "absolute",
        top: "10px",
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: "1000",
        padding: "6px 10px",
        borderRadius: "4px",
        background: "rgba(17, 24, 39, 0.88)",
        color: "#fff",
        fontSize: "12px",
        pointerEvents: "none"
      });
      map.getContainer().appendChild(notice);
    }

    layer.on("tileerror", function (event) {
      // Keine kaputten Browser-Fehlerbilder stehen lassen; Leaflet unternimmt
      // ohne errorTileUrl keine eigene Endlos-Wiederholung.
      if (event.tile) event.tile.style.visibility = "hidden";
      consecutiveErrors += 1;
      if (consecutiveErrors >= 3) showUnavailableNotice();
    });
    layer.on("tileload", function () {
      consecutiveErrors = 0;
    });
    map.on("unload", function () {
      if (notice) notice.remove();
    });

    return layer.addTo(map);
  };
})(window);
