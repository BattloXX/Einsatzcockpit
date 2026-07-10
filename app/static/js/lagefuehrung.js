/* Lageführung-Lagekarte: Leaflet + Geoman-Editor mit Auto-Layern (Einsatzort,
 * Fahrzeuge, Objekt), manuellen Zeichnungen, taktischen Zeichen, Meldungsmarkern,
 * Distanzwerkzeugen, Multi-User-Presence/Soft-Locks und Rechtevergabe (Phase 1+2).
 *
 * initLagefuehrungKarte({ elementId, incidentId, incidentLat, incidentLng,
 *                          csrfToken, editierbar, objektEnabled,
 *                          userId, userName, isFuehrer, grantedUserIds })
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

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function divIcon(html, anchor) {
    return L.divIcon({ html: html, className: "lft-divicon", iconSize: null, iconAnchor: anchor });
  }

  function vehicleIcon(color, zeichenKey) {
    if (zeichenKey) {
      return divIcon(
        '<div class="lft-vehicle-tz" style="border-color:' + color + '"><img src="' + tzIconUrl(zeichenKey) + '" alt=""></div>',
        [16, 16]
      );
    }
    return divIcon('<div class="lft-vehicle-icon" style="border-color:' + color + '"></div>', [12, 12]);
  }

  function tzIconUrl(zeichenKey) {
    return "/static/tz/symbole/" + zeichenKey + ".svg";
  }

  // Haversine-Distanz in Metern zwischen zwei Leaflet LatLng.
  function haversineMeters(a, b) {
    var R = 6371000;
    var toRad = function (d) { return d * Math.PI / 180; };
    var dLat = toRad(b.lat - a.lat);
    var dLng = toRad(b.lng - a.lng);
    var lat1 = toRad(a.lat), lat2 = toRad(b.lat);
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return R * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
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

    // ── Sidebar-Tabs (Layer / Taktik) ────────────────────────────────────────
    document.querySelectorAll("[data-lft-tab]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll("[data-lft-tab]").forEach(function (b) { b.classList.remove("lft-tab--aktiv"); });
        btn.classList.add("lft-tab--aktiv");
        var tab = btn.getAttribute("data-lft-tab");
        document.querySelectorAll("[data-lft-panel]").forEach(function (panel) {
          panel.hidden = panel.getAttribute("data-lft-panel") !== tab;
        });
      });
    });

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
            vehicleMarkers[v.id].setIcon(vehicleIcon(v.color, v.zeichen_key));
            vehicleMarkers[v.id].setTooltipContent(label);
          } else {
            vehicleMarkers[v.id] = L.marker([v.lat, v.lng], { icon: vehicleIcon(v.color, v.zeichen_key) })
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
            .bindPopup('<strong>' + escapeHtml(o.name || "Objekt") + '</strong><br><a href="' + o.url + '" target="_blank" rel="noopener">Objektdaten öffnen</a>');
        });
      }).catch(function () {});
    }

    // ── Manuelle Features: Zeichnungen, taktische Zeichen, Meldungen, Distanz ───
    var featureLayers = {}; // feature.id -> Leaflet-Layer
    var lockedFeatures = {}; // feature.id -> {user_id, name}

    function featureStyle(f) {
      if (f.typ === "distanz") {
        return { color: (f.props && f.props.color) || "#6b7280", weight: 2, dashArray: "6 4", fillOpacity: 0.04 };
      }
      return { color: (f.props && f.props.color) || "#e53e3e", weight: 3 };
    }

    function applyLockVisual(layer, feature) {
      var lock = lockedFeatures[feature.id];
      var el = layer.getElement && layer.getElement();
      if (lock && lock.user_id !== opts.userId) {
        layer.bindTooltip("wird bearbeitet von " + escapeHtml(lock.name), { permanent: false });
        if (el) { el.classList.add("lft-locked"); }
      } else if (el) {
        el.classList.remove("lft-locked");
      }
    }

    function sendWs(payload) {
      if (ws && ws.readyState === 1) { ws.send(JSON.stringify(payload)); }
    }

    var editingFeatureId = null;
    function beginEditing(featureId) {
      if (editingFeatureId === featureId) { return; }
      if (editingFeatureId != null) { endEditing(editingFeatureId); }
      editingFeatureId = featureId;
      sendWs({ type: "lagefuehrung.feature.editing", feature_id: featureId });
    }
    function endEditing(featureId) {
      if (editingFeatureId !== featureId) { return; }
      editingFeatureId = null;
      sendWs({ type: "lagefuehrung.feature.released", feature_id: featureId });
    }

    function bindEditSync(layer, feature) {
      layer.on("pm:dragstart pm:markerdragstart", function () { beginEditing(feature.id); });
      layer.on("pm:update pm:dragend pm:markerdragend", function (ev) {
        endEditing(feature.id);
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

    function tzFeatureIcon(f) {
      var rot = f.rotation || 0;
      var scale = f.scale || 1;
      var html = '<img src="' + tzIconUrl(f.zeichen_key) + '" alt="" ' +
        'style="width:32px;height:32px;transform:rotate(' + rot + 'deg) scale(' + scale + ');transform-origin:center center;">';
      return divIcon(html, [16, 16]);
    }

    function bindTzPopup(layer, f) {
      var el = document.createElement("div");
      el.className = "lft-tz-popup";
      el.innerHTML =
        '<div class="lft-tz-popup__title">' + escapeHtml(f.label || "") + '</div>' +
        '<div class="lft-tz-popup__row"><span>Drehen</span>' +
        '<button type="button" data-rot="-15">↺</button>' +
        '<button type="button" data-rot="15">↻</button></div>' +
        '<div class="lft-tz-popup__row"><span>Größe</span>' +
        '<button type="button" data-scale="0.75">S</button>' +
        '<button type="button" data-scale="1">M</button>' +
        '<button type="button" data-scale="1.5">L</button></div>';
      layer.bindPopup(el);
      layer.on("popupopen", function () { beginEditing(f.id); });
      layer.on("popupclose", function () { endEditing(f.id); });
      el.addEventListener("click", function (ev) {
        var btn = ev.target.closest("button");
        if (!btn) { return; }
        var current = layer.lft_feature;
        var patch = { version: current.version };
        if (btn.dataset.rot) { patch.rotation = ((current.rotation || 0) + parseInt(btn.dataset.rot, 10) + 360) % 360; }
        if (btn.dataset.scale) { patch.scale = parseFloat(btn.dataset.scale); }
        fetchJson(apiBase + "/features/" + current.id, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": opts.csrfToken },
          body: JSON.stringify(patch)
        }).then(function (updated) {
          renderFeature(updated);
        }).catch(function () { ladeFeatures(); });
      });
    }

    function renderFeature(f) {
      if (featureLayers[f.id]) { layerZeichnung.removeLayer(featureLayers[f.id]); }
      if (!f.geometry) { return; }
      var group = L.geoJSON(f.geometry, {
        style: featureStyle(f),
        pointToLayer: function (geoJsonPoint, latlng) {
          if (f.typ === "taktisches_zeichen" && f.zeichen_key) {
            return L.marker(latlng, { icon: tzFeatureIcon(f) });
          }
          if (f.typ === "meldung") {
            return L.marker(latlng, { icon: divIcon('<div class="lft-meldung-icon">📢</div>', [14, 28]) });
          }
          if (f.typ === "distanz" && f.props && f.props.kind === "kreis") {
            return L.circle(latlng, { radius: f.props.distanz_m || 0, color: "#6b7280", weight: 2, dashArray: "6 4", fillOpacity: 0.04 });
          }
          return L.marker(latlng, { icon: markerFeatureIcon(f) });
        }
      });
      var layer = group.getLayers()[0];
      if (!layer) { return; }
      layer.lft_feature = f;

      if (f.typ === "taktisches_zeichen") {
        bindTzPopup(layer, f);
      } else if (f.typ === "meldung") {
        layer.bindPopup('<strong>📢 Meldung</strong><br>' + escapeHtml(f.label || ""));
      } else if (f.typ === "distanz") {
        var text = (f.props && f.props.distanz_m != null) ? (f.props.distanz_m + " m") : "";
        if (text) { layer.bindTooltip(text, { permanent: true, direction: "center" }); }
      } else if (f.label) {
        layer.bindTooltip(escapeHtml(f.label), { permanent: true, direction: "top" });
      }

      layer.addTo(layerZeichnung);
      if (opts.editierbar) { bindEditSync(layer, f); }
      applyLockVisual(layer, f);
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

    function createFeature(payload) {
      fetchJson(apiBase + "/features", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": opts.csrfToken },
        body: JSON.stringify(payload)
      }).then(function (f) {
        renderFeature(f);
        ladeChronologie();
      }).catch(function () {
        alert("Element konnte nicht gespeichert werden.");
      });
    }

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
        createFeature({ typ: typ, geometry: geojson, layer_gruppe: "zeichnung" });
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

    // ── Taktische Zeichen: Palette + Platzierungsmodus ───────────────────────
    var pendingPlacement = null; // {kind, data, points:[]}

    function armPlacement(kind, data) {
      pendingPlacement = { kind: kind, data: data || {}, points: [] };
      karte.getContainer().style.cursor = "crosshair";
    }
    function disarmPlacement() {
      pendingPlacement = null;
      karte.getContainer().style.cursor = "";
    }

    function openMeldungForm(latlng) {
      var marker = L.marker(latlng, { icon: divIcon('<div class="lft-meldung-icon">📢</div>', [14, 28]) }).addTo(karte);
      var el = document.createElement("div");
      el.className = "lft-meldung-form";
      el.innerHTML =
        '<textarea rows="3" class="form-input" placeholder="Meldungstext"></textarea>' +
        '<div class="lft-meldung-form__actions">' +
        '<button type="button" class="btn btn--sm btn--primary" data-action="save">Speichern</button>' +
        '<button type="button" class="btn btn--sm btn--ghost" data-action="cancel">Abbrechen</button></div>';
      marker.bindPopup(el, { closeOnClick: false }).openPopup();
      el.querySelector('[data-action="cancel"]').addEventListener("click", function () {
        karte.removeLayer(marker);
      });
      el.querySelector('[data-action="save"]').addEventListener("click", function () {
        var text = el.querySelector("textarea").value.trim();
        karte.removeLayer(marker);
        if (!text) { return; }
        createFeature({
          typ: "meldung",
          geometry: { type: "Point", coordinates: [latlng.lng, latlng.lat] },
          label: text,
          layer_gruppe: "zeichnung"
        });
      });
    }

    function handlePlacementClick(latlng) {
      var p = pendingPlacement;
      if (p.kind === "tz") {
        createFeature({
          typ: "taktisches_zeichen",
          zeichen_key: p.data.id,
          label: p.data.name,
          geometry: { type: "Point", coordinates: [latlng.lng, latlng.lat] },
          layer_gruppe: "zeichnung"
        });
        disarmPlacement();
      } else if (p.kind === "meldung") {
        disarmPlacement();
        openMeldungForm(latlng);
      } else if (p.kind === "distanzlinie") {
        p.points.push(latlng);
        if (p.points.length === 2) {
          var distanzLinie = Math.round(haversineMeters(p.points[0], p.points[1]));
          createFeature({
            typ: "distanz",
            geometry: {
              type: "LineString",
              coordinates: [[p.points[0].lng, p.points[0].lat], [p.points[1].lng, p.points[1].lat]]
            },
            props: { kind: "linie", distanz_m: distanzLinie },
            layer_gruppe: "zeichnung"
          });
          disarmPlacement();
        }
      } else if (p.kind === "distanzkreis") {
        p.points.push(latlng);
        if (p.points.length === 2) {
          var radius = Math.round(haversineMeters(p.points[0], p.points[1]));
          createFeature({
            typ: "distanz",
            geometry: { type: "Point", coordinates: [p.points[0].lng, p.points[0].lat] },
            props: { kind: "kreis", distanz_m: radius },
            layer_gruppe: "zeichnung"
          });
          disarmPlacement();
        }
      }
    }

    karte.on("click", function (e) {
      if (!pendingPlacement) { return; }
      handlePlacementClick(e.latlng);
    });

    if (opts.editierbar) {
      var btnMeldung = document.getElementById("lft-tool-meldung");
      if (btnMeldung) { btnMeldung.addEventListener("click", function () { armPlacement("meldung"); }); }
      var btnLinie = document.getElementById("lft-tool-distanzlinie");
      if (btnLinie) { btnLinie.addEventListener("click", function () { armPlacement("distanzlinie"); }); }
      var btnKreis = document.getElementById("lft-tool-distanzkreis");
      if (btnKreis) { btnKreis.addEventListener("click", function () { armPlacement("distanzkreis"); }); }

      var tzPickerEl = document.getElementById("lft-tz-picker");
      if (tzPickerEl) {
        fetch("/static/tz/tz-manifest.json").then(function (r) { return r.json(); }).then(function (m) {
          renderTzPicker(tzPickerEl, m.symbole || []);
        }).catch(function () {});
      }
    }

    function renderTzPicker(el, symbole) {
      el.innerHTML = '<input type="text" class="form-input lft-tz-picker__search" placeholder="Suchen …">';
      var byKat = {}, gruppen = [];
      symbole.forEach(function (s) {
        var k = s.kat || "Symbole";
        if (!byKat[k]) { byKat[k] = []; gruppen.push(k); }
        byKat[k].push(s);
      });
      gruppen.forEach(function (kat) {
        var box = document.createElement("div");
        box.className = "lft-tz-picker__gruppe";
        var h = document.createElement("div");
        h.className = "lft-tz-picker__h";
        h.textContent = kat;
        box.appendChild(h);
        var grid = document.createElement("div");
        grid.className = "lft-tz-picker__grid";
        byKat[kat].forEach(function (s) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "lft-tz-picker__item";
          btn.title = s.name;
          btn.setAttribute("data-such", (s.such || "") + " " + s.name.toLowerCase());
          btn.innerHTML = '<img src="' + s.datei + '" alt=""><span>' + escapeHtml(s.name) + "</span>";
          btn.addEventListener("click", function () { armPlacement("tz", s); });
          grid.appendChild(btn);
        });
        box.appendChild(grid);
        el.appendChild(box);
      });
      var such = el.querySelector(".lft-tz-picker__search");
      such.addEventListener("input", function () {
        var q = such.value.toLowerCase().trim();
        el.querySelectorAll(".lft-tz-picker__item").forEach(function (b) {
          var txt = b.getAttribute("data-such") || "";
          b.style.display = (!q || txt.indexOf(q) !== -1) ? "" : "none";
        });
      });
    }

    // ── Präsenz + Rechtevergabe ───────────────────────────────────────────────
    var onlineUsers = {}; // user_id -> name
    var grantedUsers = {}; // user_id -> true
    (opts.grantedUserIds || []).forEach(function (uid) { grantedUsers[uid] = true; });

    function renderPresence() {
      var el = document.getElementById("lft-presence");
      if (!el) { return; }
      el.innerHTML = "";
      Object.keys(onlineUsers).forEach(function (uidStr) {
        var uid = Number(uidStr);
        var chip = document.createElement("span");
        chip.className = "lft-presence__chip" + (uid === opts.userId ? " lft-presence__chip--self" : "");
        chip.textContent = onlineUsers[uidStr];
        if (opts.isFuehrer && uid !== opts.userId) {
          var granted = !!grantedUsers[uidStr];
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "lft-presence__grant";
          btn.textContent = granted ? "Editor ✕" : "→ Editor";
          btn.title = granted ? "Editor-Recht entziehen" : "Zum Editor machen";
          btn.addEventListener("click", function () {
            fetchJson(apiBase + "/berechtigung/" + uid, {
              method: granted ? "DELETE" : "POST",
              headers: { "X-CSRF-Token": opts.csrfToken }
            }).catch(function () {});
          });
          chip.appendChild(btn);
        }
        el.appendChild(chip);
      });
    }
    renderPresence();

    // ── Live-Updates über den bestehenden Einsatz-WebSocket-Kanal ────────────
    var ws = null;
    try {
      var wsProto = location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(wsProto + "//" + location.host + "/ws/incident/" + opts.incidentId);
      ws.onopen = function () {
        sendWs({ type: "lagefuehrung.presence.join" });
      };
      ws.onmessage = function (ev) {
        var data;
        try { data = JSON.parse(ev.data); } catch (e) { return; }
        if (!data || typeof data.type !== "string") { return; }

        if (data.type.indexOf("lagefuehrung.feature.") === 0 &&
          ["lagefuehrung.feature.created", "lagefuehrung.feature.updated", "lagefuehrung.feature.deleted"].indexOf(data.type) !== -1) {
          ladeFeatures();
          ladeChronologie();
        } else if (data.type === "lagefuehrung.fuehrer_changed") {
          var bannerEl = document.getElementById("lft-banner-text");
          if (bannerEl) { bannerEl.innerHTML = "Lageführung: <strong>" + escapeHtml(data.name || "?") + "</strong>"; }
        } else if (data.type === "lagefuehrung.presence.changed") {
          onlineUsers = {};
          (data.users || []).forEach(function (u) { onlineUsers[u.user_id] = u.name; });
          renderPresence();
        } else if (data.type === "lagefuehrung.feature.locked") {
          lockedFeatures[data.feature_id] = { user_id: data.user_id, name: data.name };
          var layer = featureLayers[data.feature_id];
          if (layer) { applyLockVisual(layer, layer.lft_feature); }
        } else if (data.type === "lagefuehrung.feature.unlocked") {
          delete lockedFeatures[data.feature_id];
          var layer2 = featureLayers[data.feature_id];
          if (layer2) { applyLockVisual(layer2, layer2.lft_feature); }
        } else if (data.type === "lagefuehrung.berechtigung.changed") {
          if (data.granted) { grantedUsers[data.user_id] = true; } else { delete grantedUsers[data.user_id]; }
          renderPresence();
          if (data.user_id === opts.userId) {
            // eigener Editor-Status geändert — Seite neu laden, damit Werkzeuge erscheinen/verschwinden
            location.reload();
          }
        }
      };
      setInterval(function () {
        if (ws.readyState === 1) {
          ws.send("ping");
          sendWs({ type: "lagefuehrung.presence.heartbeat" });
        }
      }, 10000);
    } catch (e) { /* WS optional — Polling-Layer funktionieren trotzdem */ }
  };
})();
