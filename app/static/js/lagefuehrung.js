/* Lageführung-Lagekarte: Leaflet + Geoman-Editor mit Auto-Layern (Einsatzort,
 * Fahrzeuge, Objekt) und manuellen Zeichnungen (Phase 1 / MVP).
 *
 * initLagefuehrungKarte({ elementId, incidentId, incidentLat, incidentLng,
 *                          csrfToken, editierbar, objektEnabled })
 */
(function () {
  "use strict";

  // Leaflets Default-Icon verweist relativ zur CSS-Datei auf images/marker-icon.png,
  // das Projekt liefert die Bilder aber unter /static/img/leaflet/ aus (Muster
  // map-picker.js/wasserstellen_admin.js) — ohne diesen Fix zeigen alle Punkte ohne
  // eigenes divIcon (z. B. per Geoman gezeichnete Marker) ein kaputtes Bild-Symbol.
  delete L.Icon.Default.prototype._getIconUrl;
  L.Icon.Default.mergeOptions({
    iconUrl: "/static/img/leaflet/marker-icon.png",
    iconRetinaUrl: "/static/img/leaflet/marker-icon-2x.png",
    shadowUrl: "/static/img/leaflet/marker-shadow.png"
  });

  function fetchJson(url, opts) {
    return fetch(url, opts).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          var err = new Error(body.detail || ("HTTP " + r.status));
          err.status = r.status;
          throw err;
        });
      }
      if (r.status === 204) { return null; }
      return r.json();
    });
  }

  function divIcon(html, anchor) {
    return L.divIcon({ html: html, className: "lft-divicon", iconSize: null, iconAnchor: anchor });
  }

  function vehicleIcon(color) {
    return divIcon('<div class="lft-vehicle-icon" style="border-color:' + color + '"></div>', [12, 12]);
  }

  window.initLagefuehrungKarte = function (opts) {
    var apiBase = "/einsatz/" + opts.incidentId + "/lagefuehrung";
    var karte = L.map(opts.elementId, { zoomControl: true });

    var osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap"
    });
    var ortho = L.tileLayer(
      "https://mapsneu.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg",
      { maxZoom: 20, attribution: "Datenquelle: basemap.at" }
    );
    osm.addTo(karte);

    if (opts.incidentLat != null && opts.incidentLng != null) {
      karte.setView([opts.incidentLat, opts.incidentLng], 16);
    } else {
      karte.setView([47.35, 9.75], 13);
    }

    var baselayerSelect = document.getElementById("lft-baselayer-select");
    if (baselayerSelect) {
      baselayerSelect.addEventListener("change", function () {
        if (this.value === "ortho") { karte.removeLayer(osm); ortho.addTo(karte); }
        else { karte.removeLayer(ortho); osm.addTo(karte); }
      });
    }

    // ── Layer-Gruppen ────────────────────────────────────────────────────────
    var layerEinsatzort = L.layerGroup().addTo(karte);
    var layerFahrzeuge = L.layerGroup().addTo(karte);
    var layerObjekt = L.layerGroup().addTo(karte);
    var layerZeichnung = L.layerGroup().addTo(karte);

    function bindToggle(checkboxId, group) {
      var cb = document.getElementById(checkboxId);
      if (!cb) { return; }
      cb.addEventListener("change", function () {
        if (this.checked) { karte.addLayer(group); } else { karte.removeLayer(group); }
      });
    }
    bindToggle("lft-layer-einsatzort", layerEinsatzort);
    bindToggle("lft-layer-fahrzeuge", layerFahrzeuge);
    bindToggle("lft-layer-objekt", layerObjekt);
    bindToggle("lft-layer-zeichnung", layerZeichnung);

    // ── Auto-Layer: Einsatzort ───────────────────────────────────────────────
    if (opts.incidentLat != null && opts.incidentLng != null) {
      L.marker([opts.incidentLat, opts.incidentLng], { icon: divIcon('<div class="lft-einsatzort-icon">📍</div>', [14, 28]) })
        .addTo(layerEinsatzort)
        .bindTooltip("Einsatzort");
    }

    // ── Auto-Layer: Fahrzeuge (Polling, LIS aktualisiert die DB im Hintergrund) ─
    var vehicleMarkers = {};
    function ladeFahrzeuge() {
      fetchJson(apiBase + "/vehicles.json").then(function (liste) {
        var seen = {};
        (liste || []).forEach(function (v) {
          if (v.lat == null || v.lng == null) { return; }
          seen[v.id] = true;
          var label = (v.label || "Fahrzeug") + " · " + v.unit_status;
          if (vehicleMarkers[v.id]) {
            vehicleMarkers[v.id].setLatLng([v.lat, v.lng]);
            vehicleMarkers[v.id].setIcon(vehicleIcon(v.color));
            vehicleMarkers[v.id].setTooltipContent(label);
          } else {
            vehicleMarkers[v.id] = L.marker([v.lat, v.lng], { icon: vehicleIcon(v.color) })
              .addTo(layerFahrzeuge)
              .bindTooltip(label);
          }
        });
        Object.keys(vehicleMarkers).forEach(function (id) {
          if (!seen[id]) { layerFahrzeuge.removeLayer(vehicleMarkers[id]); delete vehicleMarkers[id]; }
        });
      }).catch(function () { /* still show what we have */ });
    }
    ladeFahrzeuge();
    setInterval(ladeFahrzeuge, 15000);

    // ── Auto-Layer: Objekt ───────────────────────────────────────────────────
    if (opts.objektEnabled) {
      fetchJson(apiBase + "/objekte.json").then(function (liste) {
        (liste || []).forEach(function (o) {
          L.marker([o.lat, o.lng], { icon: divIcon('<div class="lft-objekt-icon">🏢</div>', [14, 28]) })
            .addTo(layerObjekt)
            .bindPopup('<strong>' + (o.name || "Objekt") + '</strong><br><a href="' + o.url + '" target="_blank" rel="noopener">Objektdaten öffnen</a>');
        });
      }).catch(function () {});
    }

    // ── Manuelle Zeichnungen (bestehende Werkzeuge via Geoman) ──────────────────
    var featureLayers = {}; // feature.id -> Leaflet-Layer

    function featureStyle(f) {
      return { color: (f.props && f.props.color) || "#e53e3e", weight: 3 };
    }

    function bindEditSync(layer, feature) {
      layer.on("pm:update pm:dragend pm:markerdragend", function (ev) {
        var gj = ev.layer.toGeoJSON();
        var geo = gj.geometry || (gj.features && gj.features[0] && gj.features[0].geometry);
        if (!geo) { return; }
        fetchJson(apiBase + "/features/" + feature.id, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": opts.csrfToken },
          body: JSON.stringify({ geometry: geo, version: layer.lft_feature.version })
        }).then(function (updated) {
          layer.lft_feature = updated;
          ladeChronologie();
        }).catch(function () { ladeFeatures(); });
      });
    }

    function markerFeatureIcon(f) {
      var color = (f.props && f.props.color) || "#e53e3e";
      return divIcon('<div class="lft-vehicle-icon" style="border-color:' + color + '"></div>', [12, 12]);
    }

    function renderFeature(f) {
      if (featureLayers[f.id]) { layerZeichnung.removeLayer(featureLayers[f.id]); }
      if (!f.geometry) { return; }
      var group = L.geoJSON(f.geometry, {
        style: featureStyle(f),
        pointToLayer: function (geoJsonPoint, latlng) {
          return L.marker(latlng, { icon: markerFeatureIcon(f) });
        }
      });
      var layer = group.getLayers()[0];
      if (!layer) { return; }
      layer.lft_feature = f;
      if (f.label) { layer.bindTooltip(f.label, { permanent: true, direction: "top" }); }
      layer.addTo(layerZeichnung);
      if (opts.editierbar) { bindEditSync(layer, f); }
      featureLayers[f.id] = layer;
    }

    function ladeFeatures() {
      fetchJson(apiBase + "/features.json").then(function (liste) {
        var seen = {};
        (liste || []).forEach(function (f) { seen[f.id] = true; renderFeature(f); });
        Object.keys(featureLayers).forEach(function (id) {
          if (!seen[id]) { layerZeichnung.removeLayer(featureLayers[id]); delete featureLayers[id]; }
        });
      }).catch(function () {});
    }
    ladeFeatures();

    function ladeChronologie() {
      var el = document.getElementById("lft-chronologie-liste");
      if (!el) { return; }
      fetchJson(apiBase + "/events.json").then(function (liste) {
        el.innerHTML = "";
        (liste || []).forEach(function (e) {
          var li = document.createElement("li");
          // e.ts kommt als UTC mit Z-Suffix vom Server (CLAUDE.md-Regel) — hier lokal
          // formatieren, sonst zeigt die Chronologie die UTC- statt der Ortszeit an.
          var zeit = new Date(e.ts).toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit" });
          li.textContent = zeit + " · " + e.event_typ;
          el.appendChild(li);
        });
      }).catch(function () {});
    }
    ladeChronologie();

    // ── Geoman-Zeichenwerkzeuge (nur wenn editierbar) ────────────────────────
    if (opts.editierbar && karte.pm) {
      karte.pm.addControls({
        position: "topleft",
        drawMarker: true, drawPolyline: true, drawRectangle: false,
        drawPolygon: true, drawCircle: false, drawCircleMarker: false,
        drawText: false, editMode: true, dragMode: true, cutPolygon: false,
        removalMode: true, rotateMode: false
      });

      karte.on("pm:create", function (e) {
        var layer = e.layer;
        var geojson = layer.toGeoJSON().geometry;
        var typ = e.shape === "Marker" ? "marker" : "zeichnung";
        layerZeichnung.removeLayer(layer);
        fetchJson(apiBase + "/features", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": opts.csrfToken },
          body: JSON.stringify({ typ: typ, geometry: geojson, layer_gruppe: "zeichnung" })
        }).then(function (f) {
          renderFeature(f);
          ladeChronologie();
        }).catch(function () {
          alert("Zeichnung konnte nicht gespeichert werden.");
        });
      });

      karte.on("pm:remove", function (e) {
        var layer = e.layer;
        var f = layer.lft_feature;
        if (!f) { return; }
        fetchJson(apiBase + "/features/" + f.id + "?version=" + f.version, {
          method: "DELETE",
          headers: { "X-CSRF-Token": opts.csrfToken }
        }).then(function () {
          delete featureLayers[f.id];
          ladeChronologie();
        }).catch(function () { ladeFeatures(); });
      });
    }

    // ── Live-Updates über den bestehenden Einsatz-WebSocket-Kanal ────────────
    try {
      var wsProto = location.protocol === "https:" ? "wss:" : "ws:";
      var ws = new WebSocket(wsProto + "//" + location.host + "/ws/incident/" + opts.incidentId);
      ws.onmessage = function (ev) {
        var data;
        try { data = JSON.parse(ev.data); } catch (e) { return; }
        if (!data || typeof data.type !== "string") { return; }
        if (data.type.indexOf("lagefuehrung.feature.") === 0) {
          ladeFeatures();
          ladeChronologie();
        } else if (data.type === "lagefuehrung.fuehrer_changed") {
          var el = document.getElementById("lft-banner-text");
          if (el) { el.innerHTML = "Lageführung: <strong>" + (data.name || "?") + "</strong>"; }
        }
      };
      setInterval(function () { if (ws.readyState === 1) { ws.send("ping"); } }, 25000);
    } catch (e) { /* WS optional — Polling-Layer funktionieren trotzdem */ }
  };
})();
