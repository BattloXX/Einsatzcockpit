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

  // Ohne eigens zugeordnetes taktisches Zeichen (Fahrzeugverwaltung, Board- oder
  // manuell hinzugefügte Fahrzeuge) zeigt jedes Fahrzeug das generische
  // "feuerwehrfahrzeug"-Symbol. Statusfarbe bleibt als Ring um das Symbol sichtbar.
  var VEHICLE_ICON_FALLBACK = "feuerwehrfahrzeug";
  function vehicleIcon(color, zeichenKey) {
    var key = zeichenKey || VEHICLE_ICON_FALLBACK;
    return divIcon(
      '<div class="lft-vehicle-tz" style="border-color:' + color + '"><img src="' + tzIconUrl(key) + '" alt=""></div>',
      [16, 16]
    );
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

    // ── Mobile: Werkzeuge (Layer/Taktik-Palette) als Bottom-Sheet ────────────────
    // Konzept Kap. 5.2: "Karte vollflächig, Palette als Bottom-Sheet." — auf
    // Desktop/Tablet bleibt die feste Sidebar (Editieren ist dort optimiert), erst
    // ab der 760px-Mobilbreite greift die CSS-Umschaltung auf position:fixed;
    // dieser Button blendet das Sheet dort ein/aus (per Default eingeklappt, damit
    // die Karte wie im Konzept beschrieben vollflächig sichtbar bleibt).
    var sidebarToggle = document.getElementById("lft-sidebar-toggle");
    var sidebar = document.getElementById("lft-sidebar");
    if (sidebarToggle && sidebar) {
      sidebarToggle.addEventListener("click", function () {
        var offen = sidebar.classList.toggle("lft-sidebar--offen");
        sidebarToggle.setAttribute("aria-expanded", offen ? "true" : "false");
        sidebarToggle.textContent = offen ? "✕ Schließen" : "🧰 Werkzeuge";
      });
    }

    // ── Layer-Gruppen ────────────────────────────────────────────────────────
    var layerEinsatzort = L.layerGroup().addTo(karte);
    var layerFahrzeuge = L.layerGroup().addTo(karte);
    var layerObjekt = L.layerGroup().addTo(karte);
    var layerWasserstellen = L.layerGroup().addTo(karte);
    var layerZeichnung = L.layerGroup().addTo(karte);
    // Punkt-Marker der hinterlegten Objekt-Kartenobjekte (Zufahrten/Sammelplaetze/...),
    // fuer den Beschriftungen-Toggle separat gehalten (s. u.).
    var objektKartenLayers = [];

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
    bindToggle("lft-layer-wasserstellen", layerWasserstellen);
    bindToggle("lft-layer-zeichnung", layerZeichnung);

    // "Beschriftungen" ist kein eigener L.layerGroup (die Labels sind permanente Tooltips,
    // direkt an die jeweiligen Feature-Layer gebunden — Zeichnungen/Zeichen/Distanzen), daher
    // eigene Sichtbarkeits-Steuerung statt addLayer/removeLayer: beim Rendern wird der
    // Tooltip immer gebunden, aber sofort geschlossen/geöffnet je nach aktuellem Zustand.
    var beschriftungenSichtbar = true;
    function applyLabelVisibility(layer) {
      var tt = layer.getTooltip && layer.getTooltip();
      if (!tt || !tt.options.permanent) { return; }
      if (beschriftungenSichtbar) { layer.openTooltip(); } else { layer.closeTooltip(); }
    }
    var cbBeschriftung = document.getElementById("lft-layer-beschriftung");
    if (cbBeschriftung) {
      cbBeschriftung.addEventListener("change", function () {
        beschriftungenSichtbar = this.checked;
        Object.keys(featureLayers).forEach(function (id) { applyLabelVisibility(featureLayers[id]); });
        layerReplay.eachLayer(applyLabelVisibility);
        objektKartenLayers.forEach(applyLabelVisibility);
      });
    }

    // ── Auto-Layer: Einsatzort ───────────────────────────────────────────────
    if (opts.incidentLat != null && opts.incidentLng != null) {
      L.marker([opts.incidentLat, opts.incidentLng], { icon: divIcon('<div class="lft-einsatzort-icon">📍</div>', [14, 28]) })
        .addTo(layerEinsatzort)
        .bindTooltip("Einsatzort");
    }

    // ── Auto-Layer: Fahrzeuge (Polling, LIS aktualisiert die DB im Hintergrund) ─
    // Board-Fahrzeuge ohne (aktuelle) GPS-Position werden nicht auf der Karte
    // geplottet, erscheinen aber in der Fahrzeuge-Sidebar mit "Platzieren"-Button
    // (manuelle Position, Muster GSL vehicle_manual_pin).
    var vehicleMarkers = {};
    function renderFahrzeugeListe(liste) {
      var el = document.getElementById("lft-fahrzeuge-liste");
      if (!el) { return; }
      el.innerHTML = "";
      (liste || []).forEach(function (v) {
        var li = document.createElement("li");
        li.className = "lft-fahrzeuge__item";
        var text = document.createElement("span");
        text.textContent = (v.label || "Fahrzeug") + " · " + v.unit_status;
        li.appendChild(text);
        // Immer platzierbar/verschiebbar — auch wenn bereits eine (ggf. veraltete oder aus
        // einem anderen Einsatz übernommene) Position vorliegt, da GPS-Positionen je
        // Fahrzeug org-weit zuletzt-gemeldet korrelieren und nicht zwingend zu diesem
        // Einsatz passen. Der Lageführer muss die Position jederzeit manuell setzen können.
        if (v.lat != null && v.lng != null) {
          var ok = document.createElement("span");
          ok.className = "lft-fahrzeuge__auf-karte";
          ok.textContent = "auf Karte";
          li.appendChild(ok);
        }
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn--ghost btn--xs";
        btn.textContent = (v.lat == null || v.lng == null) ? "📍 Platzieren" : "📍 Verschieben";
        btn.addEventListener("click", function () { armPlacement("fahrzeug-pin", v); });
        li.appendChild(btn);
        el.appendChild(li);
      });
    }
    function ladeFahrzeuge() {
      fetchJson(apiBase + "/vehicles.json").then(function (liste) {
        renderFahrzeugeListe(liste);
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
    // Zeigt nicht nur das Objekt-Symbol, sondern die am Objekt gepflegten Infos
    // (Gefahren, Informationen, Anfahrtsweg, Kontakte) im Popup — Gefahren zusätzlich
    // als kleines Warnzeichen direkt am Symbol, damit sie auch ohne Klick auffallen.
    function objektIconHtml(o) {
      var warn = (o.gefahren && o.gefahren.length) ? '<span class="lft-objekt-icon__warn">⚠</span>' : "";
      return '<div class="lft-objekt-icon">🏢' + warn + '</div>';
    }
    function objektPopupHtml(o) {
      var html = '<div class="lft-objekt-popup">';
      html += '<div class="lft-objekt-popup__title">' + escapeHtml(o.name || "Objekt") +
        (o.vulgoname ? ' <span class="lft-objekt-popup__vulgo">(' + escapeHtml(o.vulgoname) + ')</span>' : "") + '</div>';
      if (o.adresse || o.bma_nummer) {
        html += '<div class="lft-objekt-popup__info">' +
          (o.adresse ? escapeHtml(o.adresse) : "") +
          (o.adresse && o.bma_nummer ? " · " : "") +
          (o.bma_nummer ? "🔥 BMA " + escapeHtml(o.bma_nummer) : "") + "</div>";
      }
      if (o.gefahren && o.gefahren.length) {
        html += '<div class="lft-objekt-popup__gefahren">';
        o.gefahren.forEach(function (g) {
          html += '<div class="lft-objekt-popup__gefahr">' + (g.piktogramm || "⚠️") + " " + escapeHtml(g.name) +
            (g.un_nummer ? " · UN " + escapeHtml(g.un_nummer) : "") +
            (g.stoffname ? " · " + escapeHtml(g.stoffname) : "") + "</div>";
        });
        html += "</div>";
      }
      if (o.informationen) {
        html += '<div class="lft-objekt-popup__info">' + escapeHtml(o.informationen) + "</div>";
      }
      if (o.anfahrtsweg) {
        html += '<div class="lft-objekt-popup__info"><strong>Anfahrt:</strong> ' + escapeHtml(o.anfahrtsweg) + "</div>";
      }
      if (o.kontakte && o.kontakte.length) {
        html += '<div class="lft-objekt-popup__kontakte">';
        o.kontakte.forEach(function (k) {
          html += "<div>" + escapeHtml(k.name) +
            (k.telefone && k.telefone.length ? " · " + escapeHtml(k.telefone.join(", ")) : "") + "</div>";
        });
        html += "</div>";
      }
      html += '<a href="' + o.url + '" target="_blank" rel="noopener">Objektdaten öffnen</a></div>';
      return html;
    }
    // Hinterlegte Geometrien der Objekt-Lagekarte (Zufahrten, Sammelplaetze, ...) —
    // gleiche Kurzsymbole/Stile wie objekt_karte.js, damit Einheiten das Symbol aus
    // der Objektverwaltung wiedererkennen. Punkte = Marker mit Kuerzel, Linien/
    // Flaechen (z. B. Zufahrten) = gestrichelte Geometrie mit Hover-Label.
    var KARTENOBJEKT_TEXT = {
      fsd: "FSD", schluesselbox: "BOX", bsp: "BSP", bmz: "BMZ", fbf: "FBF", dlk_stellplatz: "DLK",
      objektfunk: "FUNK", sammelplatz: "SP", feuerloescher: "FL", hauptzugang: "➜", nebenzugang: "➜",
      stiege: "ST", aufzug: "AZ", gefahr_ex: "EX", gefahr_gas: "GAS", gefahr_chemie: "CHE",
      gefahr_strom: "kV", gefahr_pv: "PV", hydrant_ueberflur: "H", hydrant_unterflur: "UH"
    };
    var KARTENOBJEKT_STIL = {
      fsd: "box", schluesselbox: "box", bsp: "box", bmz: "box", fbf: "box", dlk_stellplatz: "box",
      objektfunk: "box", aufzug: "box", sammelplatz: "gruen", stiege: "gruen", feuerloescher: "rot",
      hauptzugang: "pfeil", nebenzugang: "pfeil", gefahr_ex: "dreieck", gefahr_gas: "dreieck",
      gefahr_chemie: "dreieck", gefahr_strom: "dreieck", gefahr_pv: "dreieck",
      hydrant_ueberflur: "hydrant", hydrant_unterflur: "hydrant"
    };
    function kartenobjektIconHtml(k) {
      var text = KARTENOBJEKT_TEXT[k.typ] || (k.typ || "?").slice(0, 3).toUpperCase();
      var stil = KARTENOBJEKT_STIL[k.typ] || "box";
      return '<div class="lft-kobj-icon lft-kobj-icon--' + stil + '">' + escapeHtml(text) + '</div>';
    }
    function renderKartenobjekt(k) {
      if (k.geometry) {
        var geoLayer = L.geoJSON(k.geometry, { style: { color: "#2563eb", weight: 3, dashArray: "6 4", fillOpacity: 0.08 } });
        if (k.label) { geoLayer.bindTooltip(k.label, { sticky: true }); }
        geoLayer.addTo(layerObjekt);
        return;
      }
      if (k.lat == null || k.lng == null) { return; }
      var marker = L.marker([k.lat, k.lng], { icon: divIcon(kartenobjektIconHtml(k), [13, 13]) });
      var label = k.label || k.typ_label;
      if (label) { marker.bindTooltip(label, { permanent: true, direction: "top", className: "lft-kobj-label" }); }
      marker.addTo(layerObjekt);
      applyLabelVisibility(marker);
      objektKartenLayers.push(marker);
    }
    if (opts.objektEnabled) {
      fetchJson(apiBase + "/objekte.json").then(function (liste) {
        (liste || []).forEach(function (o) {
          L.marker([o.lat, o.lng], { icon: divIcon(objektIconHtml(o), [14, 28]) })
            .addTo(layerObjekt)
            .bindPopup(objektPopupHtml(o));
          (o.kartenobjekte || []).forEach(renderKartenobjekt);
        });
      }).catch(function () {});
    }

    // ── Auto-Layer: Wasserstellen (Löschwasser-Stammdaten, statisch je Ladung) ──
    var WASSERSTELLE_ICON_TEXT = { ueberflur: "H", unterflur: "UH", loeschwasser: "≈" };
    function wasserstelleIcon(kat) {
      var t = WASSERSTELLE_ICON_TEXT[kat] || "H";
      return L.divIcon({
        html: '<div class="hydrant-icon hydrant-icon--' + (kat || "hydrant") + '">' + t + "</div>",
        className: "hydrant-divicon", iconSize: null, iconAnchor: [11, 11]
      });
    }
    function ladeWasserstellen() {
      fetchJson(apiBase + "/wasserstellen.json").then(function (liste) {
        (liste || []).forEach(function (w) {
          var kat = w.icon_kat || w.typ || "hydrant";
          var label = (w.typ_label || "Wasserstelle") + (w.ref ? " · " + w.ref : "");
          L.marker([w.lat, w.lng], { icon: wasserstelleIcon(kat) })
            .addTo(layerWasserstellen)
            .bindTooltip(escapeHtml(label));
        });
      }).catch(function () {});
    }
    ladeWasserstellen();

    // ── Manuelle Features: Zeichnungen, taktische Zeichen, Meldungen, Distanz ───
    var featureLayers = {}; // feature.id -> Leaflet-Layer
    var lockedFeatures = {}; // feature.id -> {user_id, name}

    function featureStyle(f) {
      if (f.typ === "distanz") {
        return { color: (f.props && f.props.color) || "#6b7280", weight: 2, dashArray: "6 4", fillOpacity: 0.04 };
      }
      if (f.props && f.props.flaeche_key) {
        // Fläche aus der Flächen-Palette (tz-manifest.json flaechen[]) — Vereinfachung ohne
        // echtes SVG-Schraffur-Pattern (Muster: Farbnäherung statt exaktem Muster, wie bei der
        // PDF-Kartenapproximation in lagefuehrung_pdf_service.py): Füllfarbe = Schraffurfarbe
        // mit niedriger Deckkraft, Randfarbe = stroke, gestrichelt wenn dash=true.
        return {
          color: f.props.color || "#e53e3e",
          weight: 3,
          dashArray: f.props.dash ? "6 4" : null,
          fillColor: f.props.hatchColor || f.props.color || "#e53e3e",
          fillOpacity: f.props.hatch ? 0.22 : 0.1,
        };
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

    var FEATURE_TITEL = { text: "Text", meldung: "Meldung", marker: "Marker", zeichnung: "Zeichnung" };
    function featureTitel(f) {
      if (f.typ === "taktisches_zeichen") { return f.label || "Symbol"; }
      if (f.props && f.props.flaeche_key) { return "Fläche"; }
      return FEATURE_TITEL[f.typ] || f.typ;
    }

    // Generischer Bearbeiten-Popup für JEDEN Feature-Typ (vorher nur taktische Zeichen mit
    // Drehen/Größe, aber ohne Beschriftungsfeld — Zeichnungen/Marker/Meldungen hatten gar
    // keinen oder nur einen reinen Lese-Popup). Beschriftung ist jetzt bei allen Typen
    // editierbar; Drehen/Größe bleibt auf taktische Zeichen beschränkt (einzige Typen mit
    // rotation/scale-Feldern).
    function bindFeaturePopup(layer, f) {
      var zeigeDrehGroesse = f.typ === "taktisches_zeichen";
      var el = document.createElement("div");
      el.className = "lft-tz-popup";
      el.innerHTML =
        '<div class="lft-tz-popup__title">' + escapeHtml(featureTitel(f)) + '</div>' +
        '<div class="lft-tz-popup__row lft-tz-popup__row--label">' +
        '<input type="text" class="form-input lft-tz-popup__label-input" value="' + escapeHtml(f.label || "") + '" placeholder="Beschriftung">' +
        '<button type="button" data-save-label>Speichern</button></div>' +
        (zeigeDrehGroesse ?
          '<div class="lft-tz-popup__row"><span>Drehen</span>' +
          '<button type="button" data-rot="-15">↺</button>' +
          '<button type="button" data-rot="15">↻</button></div>' +
          '<div class="lft-tz-popup__row"><span>Größe</span>' +
          '<button type="button" data-scale="0.75">S</button>' +
          '<button type="button" data-scale="1">M</button>' +
          '<button type="button" data-scale="1.5">L</button></div>'
          : '');
      layer.bindPopup(el);
      layer.on("popupopen", function () { beginEditing(f.id); });
      layer.on("popupclose", function () { endEditing(f.id); });

      function speichern(patch) {
        var current = layer.lft_feature;
        patch.version = current.version;
        fetchJson(apiBase + "/features/" + current.id, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": opts.csrfToken },
          body: JSON.stringify(patch)
        }).then(function (updated) {
          renderFeature(updated);
        }).catch(function () { ladeFeatures(); });
      }
      el.addEventListener("click", function (ev) {
        var btn = ev.target.closest("button");
        if (!btn) { return; }
        var current = layer.lft_feature;
        var patch = {};
        if (btn.dataset.rot) { patch.rotation = ((current.rotation || 0) + parseInt(btn.dataset.rot, 10) + 360) % 360; }
        if (btn.dataset.scale) { patch.scale = parseFloat(btn.dataset.scale); }
        if (btn.hasAttribute("data-save-label")) { patch.label = el.querySelector(".lft-tz-popup__label-input").value; }
        speichern(patch);
      });
      var labelInput = el.querySelector(".lft-tz-popup__label-input");
      labelInput.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { speichern({ label: labelInput.value }); }
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
          if (f.typ === "text") {
            return L.marker(latlng, { icon: divIcon("", [0, 0]) });
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

      if (f.typ === "distanz") {
        var text = (f.props && f.props.distanz_m != null) ? (f.props.distanz_m + " m") : "";
        if (text) { layer.bindTooltip(text, { permanent: true, direction: "center" }); }
      } else {
        bindFeaturePopup(layer, f);
        if (f.label && f.typ !== "meldung") {
          layer.bindTooltip(escapeHtml(f.label), {
            permanent: true, direction: "top",
            className: f.typ === "text" ? "lft-text-label" : undefined,
          });
        }
      }

      layer.addTo(layerZeichnung);
      if (opts.editierbar) { bindEditSync(layer, f); }
      applyLockVisual(layer, f);
      applyLabelVisibility(layer);
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

    // ── Lage-Replay (Phase 3, F-Replay): Vor-/Zurückspulen auf Basis der Chronologie ──
    // Rekonstruiert den Kartenzustand zu einem beliebigen Zeitpunkt ausschließlich aus den
    // (in ui_lagefuehrung.py angereicherten) Event-Payloads — kein eigener Server-Endpoint
    // nötig, alles läuft clientseitig gegen events.json?limit=2000. Betrifft nur die
    // manuellen Zeichnungen/Zeichen/Meldungen/Distanzmessungen (layerZeichnung); Fahrzeuge/
    // Einsatzort/Objekt/Wasserstellen sind Live-Layer ohne Zeitreise (siehe Konzept-Scoping).
    var layerReplay = L.layerGroup();
    var replayAktiv = false;
    var replayEvents = null; // aufsteigend sortiert
    var replayTimer = null;

    function replayStateAt(index) {
      var state = {}; // feature_id -> voller Feature-Snapshot (dict) oder entfernt bei delete
      for (var i = 0; i <= index; i++) {
        var e = replayEvents[i];
        if (e.ref_typ !== "feature" || e.ref_id == null) { continue; }
        if ((e.event_typ === "feature.created" || e.event_typ === "feature.updated") && e.payload) {
          state[e.ref_id] = e.payload;
        } else if (e.event_typ === "feature.deleted") {
          delete state[e.ref_id];
        }
      }
      return state;
    }

    function renderReplayFeature(f) {
      if (!f.geometry) { return; }
      var group = L.geoJSON(f.geometry, {
        style: featureStyle(f),
        pointToLayer: function (geoJsonPoint, latlng) {
          if (f.typ === "taktisches_zeichen" && f.zeichen_key) { return L.marker(latlng, { icon: tzFeatureIcon(f) }); }
          if (f.typ === "meldung") { return L.marker(latlng, { icon: divIcon('<div class="lft-meldung-icon">📢</div>', [14, 28]) }); }
          if (f.typ === "text") { return L.marker(latlng, { icon: divIcon("", [0, 0]) }); }
          if (f.typ === "distanz" && f.props && f.props.kind === "kreis") {
            return L.circle(latlng, { radius: f.props.distanz_m || 0, color: "#6b7280", weight: 2, dashArray: "6 4", fillOpacity: 0.04 });
          }
          return L.marker(latlng, { icon: markerFeatureIcon(f) });
        }
      });
      var layer = group.getLayers()[0];
      if (!layer) { return; }
      if (f.typ === "meldung") {
        layer.bindTooltip("📢 " + escapeHtml(f.label || ""), { permanent: false });
      } else if (f.typ === "distanz" && f.props && f.props.distanz_m != null) {
        layer.bindTooltip(f.props.distanz_m + " m", { permanent: true, direction: "center" });
      } else if (f.label) {
        layer.bindTooltip(escapeHtml(f.label), {
          permanent: true, direction: "top",
          className: f.typ === "text" ? "lft-text-label" : undefined,
        });
      }
      layer.addTo(layerReplay);
      applyLabelVisibility(layer);
    }

    function renderReplayAt(index) {
      layerReplay.clearLayers();
      var state = replayStateAt(index);
      Object.keys(state).forEach(function (fid) { renderReplayFeature(state[fid]); });
      var zeitEl = document.getElementById("lft-replay-zeit");
      if (zeitEl && replayEvents[index]) {
        zeitEl.textContent = new Date(replayEvents[index].ts).toLocaleString("de-AT");
      }
    }

    function stopReplayPlayback() {
      if (replayTimer) { clearInterval(replayTimer); replayTimer = null; }
      var playBtn = document.getElementById("lft-replay-play");
      if (playBtn) { playBtn.textContent = "▶"; }
    }

    function enterReplay() {
      fetchJson(apiBase + "/events.json?limit=2000").then(function (liste) {
        replayEvents = (liste || []).slice().reverse();
        if (!replayEvents.length) { alert("Keine Chronologie-Einträge vorhanden."); return; }
        replayAktiv = true;
        karte.removeLayer(layerZeichnung);
        layerReplay.addTo(karte);
        if (opts.editierbar && karte.pm) { karte.pm.removeControls(); }
        var slider = document.getElementById("lft-replay-slider");
        if (slider) {
          slider.min = 0; slider.max = replayEvents.length - 1; slider.value = replayEvents.length - 1;
        }
        var panel = document.getElementById("lft-replay-panel");
        if (panel) { panel.hidden = false; }
        renderReplayAt(replayEvents.length - 1);
      }).catch(function () { alert("Chronologie konnte nicht geladen werden."); });
    }

    function exitReplay() {
      replayAktiv = false;
      stopReplayPlayback();
      karte.removeLayer(layerReplay);
      layerZeichnung.addTo(karte);
      if (opts.editierbar && karte.pm) { enableDrawTools(); }
      var panel = document.getElementById("lft-replay-panel");
      if (panel) { panel.hidden = true; }
    }

    // ── Druck: WYSIWYG-Kartendruck (Muster GSL-Lagekarte) ────────────────────
    // Druckt exakt den aktuellen Kartenausschnitt mit den gerade eingeschalteten
    // Layern (inkl. Beschriftungen-Zustand) — kein separater Bericht/Journal.
    var DRUCK_LAYER_CHECKBOX_IDS = [
      "lft-layer-einsatzort", "lft-layer-fahrzeuge", "lft-layer-objekt",
      "lft-layer-wasserstellen", "lft-layer-zeichnung", "lft-layer-beschriftung"
    ];

    // QuickPrint (Konzept Kap. 2.2, lagekarte.info-Verhalten): Papierformat,
    // Basiskarte und die zuletzt gedruckten Layer je Browser/Nutzer merken, damit
    // beim naechsten Einsatz nicht wieder alles auf den Standard zurueckgesetzt ist.
    var DRUCK_SETTINGS_KEY = "lft_druck_settings";
    function speichereDruckEinstellungen() {
      try {
        var fmtSel = document.getElementById("lft-druck-format");
        var layers = DRUCK_LAYER_CHECKBOX_IDS.filter(function (id) {
          var cb = document.getElementById(id);
          return cb && cb.checked;
        });
        localStorage.setItem(DRUCK_SETTINGS_KEY, JSON.stringify({
          fmt: fmtSel ? fmtSel.value : null,
          layers: layers,
          baselayer: baselayerSelect ? baselayerSelect.value : null
        }));
      } catch (e) { /* z. B. Private Browsing ohne localStorage-Zugriff */ }
    }
    (function wendeGespeicherteDruckEinstellungenAn() {
      var gespeichert;
      try {
        var raw = localStorage.getItem(DRUCK_SETTINGS_KEY);
        gespeichert = raw ? JSON.parse(raw) : null;
      } catch (e) { gespeichert = null; }
      if (!gespeichert) { return; }
      var fmtSel = document.getElementById("lft-druck-format");
      if (fmtSel && gespeichert.fmt) { fmtSel.value = gespeichert.fmt; }
      if (gespeichert.baselayer && baselayerSelect && baselayerSelect.value !== gespeichert.baselayer) {
        baselayerSelect.value = gespeichert.baselayer;
        baselayerSelect.dispatchEvent(new Event("change"));
      }
      if (Array.isArray(gespeichert.layers)) {
        DRUCK_LAYER_CHECKBOX_IDS.forEach(function (id) {
          var cb = document.getElementById(id);
          if (!cb) { return; }
          var soll = gespeichert.layers.indexOf(id) !== -1;
          if (cb.checked !== soll) {
            cb.checked = soll;
            cb.dispatchEvent(new Event("change"));
          }
        });
      }
    })();

    var btnDruck = document.getElementById("lft-tool-druck");
    if (btnDruck) {
      btnDruck.addEventListener("click", function () {
        speichereDruckEinstellungen();
        var b = karte.getBounds();
        var aktiveLayer = [];
        [
          ["lft-layer-einsatzort", "einsatzort"],
          ["lft-layer-fahrzeuge", "fahrzeuge"],
          ["lft-layer-objekt", "objekt"],
          ["lft-layer-wasserstellen", "wasserstellen"],
          ["lft-layer-zeichnung", "zeichnung"],
          ["lft-layer-beschriftung", "beschriftung"],
        ].forEach(function (pair) {
          var cb = document.getElementById(pair[0]);
          if (cb && cb.checked) { aktiveLayer.push(pair[1]); }
        });
        var fmtSel = document.getElementById("lft-druck-format");
        var fmt = fmtSel ? fmtSel.value : "A4 landscape";
        var qs = "min_lat=" + b.getSouth().toFixed(6) + "&min_lng=" + b.getWest().toFixed(6) +
          "&max_lat=" + b.getNorth().toFixed(6) + "&max_lng=" + b.getEast().toFixed(6) +
          "&fmt=" + encodeURIComponent(fmt) + "&layers=" + encodeURIComponent(aktiveLayer.join(",")) +
          "&baselayer=" + (baselayerSelect ? baselayerSelect.value : "osm");
        window.open(apiBase + "/karte/druck?" + qs, "_blank");
      });
    }

    var btnReplay = document.getElementById("lft-tool-replay");
    if (btnReplay) { btnReplay.addEventListener("click", enterReplay); }
    var btnReplayExit = document.getElementById("lft-replay-exit");
    if (btnReplayExit) { btnReplayExit.addEventListener("click", exitReplay); }
    var replaySliderEl = document.getElementById("lft-replay-slider");
    if (replaySliderEl) {
      replaySliderEl.addEventListener("input", function () {
        stopReplayPlayback();
        renderReplayAt(parseInt(replaySliderEl.value, 10));
      });
    }
    var replayPlayBtn = document.getElementById("lft-replay-play");
    if (replayPlayBtn) {
      replayPlayBtn.addEventListener("click", function () {
        if (replayTimer) { stopReplayPlayback(); return; }
        replayPlayBtn.textContent = "⏸";
        replayTimer = setInterval(function () {
          var slider = document.getElementById("lft-replay-slider");
          var next = parseInt(slider.value, 10) + 1;
          if (next > parseInt(slider.max, 10)) { stopReplayPlayback(); return; }
          slider.value = next;
          renderReplayAt(next);
        }, 900);
      });
    }

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
          if (e.event_typ === "snapshot.erstellt" && e.ref_id) {
            var bildUrl = apiBase + "/momentaufnahme/" + e.ref_id + "/bild";
            var titel = zeit + " · Momentaufnahme" + ((e.payload && e.payload.label) ? " – " + e.payload.label : "");
            var a = document.createElement("a");
            a.className = "lft-chronologie__snapshot";
            a.href = bildUrl;
            a.target = "_blank";
            a.rel = "noopener";
            a.innerHTML = '<img src="' + bildUrl + '" alt="" loading="lazy"><span>' + escapeHtml(titel) + "</span>";
            li.appendChild(a);
          } else {
            li.textContent = zeit + " · " + e.event_typ;
          }
          el.appendChild(li);
        });
      }).catch(function () {});
    }
    ladeChronologie();

    // ── Geoman-Zeichenwerkzeuge (nur wenn editierbar) ────────────────────────
    function enableDrawTools() {
      karte.pm.addControls({
        position: "topleft",
        drawMarker: true, drawPolyline: true, drawRectangle: false,
        drawPolygon: true, drawCircle: false, drawCircleMarker: false,
        drawText: false, editMode: true, dragMode: true, cutPolygon: false,
        removalMode: true, rotateMode: false
      });
    }

    // Von der Flächen-Palette (Taktik-Tab) vorgemerkter Stil für die als Nächstes gezeichnete
    // Fläche — Auswahl dort startet automatisch das Polygon-Werkzeug (siehe renderFlaechenPicker).
    var pendingFlaecheStyle = null;

    if (opts.editierbar && karte.pm) {
      enableDrawTools();

      karte.on("pm:create", function (e) {
        var layer = e.layer;
        var geojson = layer.toGeoJSON().geometry;
        var typ = e.shape === "Marker" ? "marker" : "zeichnung";
        // Geoman hängt den frisch gezeichneten Layer direkt an die Karte (nicht an
        // layerZeichnung) — ohne karte.removeLayer(...) blieb die unstilisierte
        // Geoman-Rohform (Standard-Blau, Standard-Leaflet-Pin) dauerhaft sichtbar, zusätzlich
        // zur sauber gerenderten Version aus renderFeature() (Fehlerbild: "blaue Marker beim
        // Zeichnen"). layerZeichnung.removeLayer(layer) war ein No-Op.
        karte.removeLayer(layer);

        var payload = { typ: typ, geometry: geojson, layer_gruppe: "zeichnung" };
        if (pendingFlaecheStyle && e.shape === "Polygon") {
          payload.label = pendingFlaecheStyle.name;
          payload.props = {
            flaeche_key: pendingFlaecheStyle.id,
            color: pendingFlaecheStyle.stroke,
            hatch: pendingFlaecheStyle.hatch || null,
            hatchColor: pendingFlaecheStyle.hatchColor || null,
            dash: !!pendingFlaecheStyle.dash,
          };
        }
        pendingFlaecheStyle = null;
        createFeature(payload);
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

    function openTextForm(latlng) {
      var marker = L.marker(latlng, { icon: divIcon("", [0, 0]) }).addTo(karte);
      var el = document.createElement("div");
      el.className = "lft-meldung-form";
      el.innerHTML =
        '<input type="text" class="form-input" placeholder="Text">' +
        '<div class="lft-meldung-form__actions">' +
        '<button type="button" class="btn btn--sm btn--primary" data-action="save">Speichern</button>' +
        '<button type="button" class="btn btn--sm btn--ghost" data-action="cancel">Abbrechen</button></div>';
      marker.bindPopup(el, { closeOnClick: false }).openPopup();
      el.querySelector('[data-action="cancel"]').addEventListener("click", function () {
        karte.removeLayer(marker);
      });
      function speichern() {
        var text = el.querySelector("input").value.trim();
        karte.removeLayer(marker);
        if (!text) { return; }
        createFeature({
          typ: "text",
          geometry: { type: "Point", coordinates: [latlng.lng, latlng.lat] },
          label: text,
          layer_gruppe: "zeichnung"
        });
      }
      el.querySelector('[data-action="save"]').addEventListener("click", speichern);
      el.querySelector("input").addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { speichern(); }
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
      } else if (p.kind === "text") {
        disarmPlacement();
        openTextForm(latlng);
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
      } else if (p.kind === "wind") {
        createFeature({
          typ: "taktisches_zeichen",
          zeichen_key: "windrichtung",
          label: "Windrichtung",
          rotation: p.data.rotation || 0,
          geometry: { type: "Point", coordinates: [latlng.lng, latlng.lat] },
          layer_gruppe: "zeichnung"
        });
        disarmPlacement();
      } else if (p.kind === "fahrzeug-pin") {
        disarmPlacement();
        fetchJson(apiBase + "/vehicles/" + p.data.id + "/pin", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": opts.csrfToken },
          body: JSON.stringify({ lat: latlng.lat, lng: latlng.lng })
        }).then(function () {
          ladeFahrzeuge();
          ladeChronologie();
        }).catch(function () {
          alert("Fahrzeug konnte nicht platziert werden.");
        });
      }
    }

    karte.on("click", function (e) {
      if (!pendingPlacement || replayAktiv) { return; }
      handlePlacementClick(e.latlng);
    });

    if (opts.editierbar) {
      var btnMeldung = document.getElementById("lft-tool-meldung");
      if (btnMeldung) { btnMeldung.addEventListener("click", function () { armPlacement("meldung"); }); }
      var btnLinie = document.getElementById("lft-tool-distanzlinie");
      if (btnLinie) { btnLinie.addEventListener("click", function () { armPlacement("distanzlinie"); }); }
      var btnKreis = document.getElementById("lft-tool-distanzkreis");
      if (btnKreis) { btnKreis.addEventListener("click", function () { armPlacement("distanzkreis"); }); }

      // Windrichtung: aktuelle Richtung vorab laden (weather_service, meteorologische
      // "kommt von"-Richtung + 180° = "bläst nach"-Richtung, siehe wind.json-Docstring),
      // Symbol zeigt bei rotation=0 nach Norden (windrichtung.svg). Ohne Wetterdaten wird
      // mit rotation=0 platziert, danach über das Zeichen-Popup wie gewohnt drehbar.
      var btnWind = document.getElementById("lft-tool-wind");
      if (btnWind) {
        btnWind.addEventListener("click", function () {
          fetchJson(apiBase + "/wind.json").then(function (w) {
            var rot = (w && w.wind_direction_deg != null) ? Math.round((w.wind_direction_deg + 180) % 360) : 0;
            armPlacement("wind", { rotation: rot });
          }).catch(function () { armPlacement("wind", { rotation: 0 }); });
        });
      }

      var btnSnapshot = document.getElementById("lft-tool-snapshot");
      if (btnSnapshot) {
        btnSnapshot.addEventListener("click", function () {
          btnSnapshot.disabled = true;
          fetchJson(apiBase + "/momentaufnahme", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": opts.csrfToken },
            body: JSON.stringify({})
          }).then(function () {
            btnSnapshot.disabled = false;
            ladeChronologie();
          }).catch(function () {
            btnSnapshot.disabled = false;
            alert("Momentaufnahme konnte nicht erstellt werden.");
          });
        });
      }

      var tzPickerEl = document.getElementById("lft-tz-picker");
      var flaechenPickerEl = document.getElementById("lft-flaechen-picker");
      if (tzPickerEl) {
        fetch("/static/tz/tz-manifest.json").then(function (r) { return r.json(); }).then(function (m) {
          renderTzPicker(tzPickerEl, m.symbole || []);
          if (flaechenPickerEl) { renderFlaechenPicker(flaechenPickerEl, m.flaechen || []); }
        }).catch(function () {});
      }

      document.querySelectorAll("[data-lft-tzsub]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          document.querySelectorAll("[data-lft-tzsub]").forEach(function (b) { b.classList.remove("lft-tz-subtab--aktiv"); });
          btn.classList.add("lft-tz-subtab--aktiv");
          var sub = btn.getAttribute("data-lft-tzsub");
          document.querySelectorAll("[data-lft-tzsub-panel]").forEach(function (panel) {
            panel.hidden = panel.getAttribute("data-lft-tzsub-panel") !== sub;
          });
        });
      });

      var btnText = document.getElementById("lft-tool-text");
      if (btnText) { btnText.addEventListener("click", function () { armPlacement("text"); }); }

      initFahrzeugSuche();
    }

    // ── Fahrzeug hinzufügen (wird danach auch im Board angezeigt) ────────────
    function submitAddVehicle(vehicleMasterId) {
      var form = document.createElement("form");
      form.method = "post";
      form.action = "/einsatz/" + opts.incidentId + "/fahrzeug-hinzufuegen";
      form.style.display = "none";
      [
        ["_csrf", opts.csrfToken],
        ["vehicle_master_id", vehicleMasterId],
        ["next", "/einsatz/" + opts.incidentId + "/lagefuehrung"]
      ].forEach(function (pair) {
        var input = document.createElement("input");
        input.type = "hidden"; input.name = pair[0]; input.value = pair[1];
        form.appendChild(input);
      });
      document.body.appendChild(form);
      form.submit();
    }

    function initFahrzeugSuche() {
      var input = document.getElementById("lft-fahrzeug-suche");
      var resultsEl = document.getElementById("lft-fahrzeug-vorschlaege");
      if (!input || !resultsEl) { return; }
      var suchTimer = null;
      input.addEventListener("input", function () {
        var q = input.value.trim();
        if (suchTimer) { clearTimeout(suchTimer); }
        suchTimer = setTimeout(function () {
          fetchJson("/einsatz/" + opts.incidentId + "/fahrzeug-vorschlaege?q=" + encodeURIComponent(q))
            .then(function (data) {
              var items = (data && data.items) || [];
              resultsEl.innerHTML = "";
              if (!items.length) { resultsEl.hidden = true; return; }
              items.slice(0, 8).forEach(function (it) {
                var row = document.createElement("button");
                row.type = "button";
                row.className = "lft-fahrzeuge__vorschlag" + (it.in_use ? " lft-fahrzeuge__vorschlag--inuse" : "");
                row.disabled = it.in_use;
                row.textContent = it.display_label + (it.name ? " – " + it.name : "") + (it.in_use ? " (bereits im Einsatz)" : "");
                if (!it.in_use) {
                  row.addEventListener("click", function () { submitAddVehicle(it.id); });
                }
                resultsEl.appendChild(row);
              });
              resultsEl.hidden = false;
            }).catch(function () {});
        }, 250);
      });
      document.addEventListener("click", function (ev) {
        if (ev.target !== input && !resultsEl.contains(ev.target)) { resultsEl.hidden = true; }
      });
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

    // Flächen-Palette (tz-manifest.json flaechen[]): Auswahl merkt den Stil vor und startet
    // sofort das Geoman-Polygon-Werkzeug — analog dazu, dass die Symbol-Auswahl sofort den
    // Platzierungsmodus mit dem richtigen Zeichen aktiviert (siehe pm:create-Handler oben).
    function renderFlaechenPicker(el, flaechen) {
      el.innerHTML = "";
      var grid = document.createElement("div");
      grid.className = "lft-flaechen-picker__grid";
      flaechen.forEach(function (fl) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "lft-flaechen-picker__item";
        btn.title = fl.name;
        var swatch = document.createElement("span");
        swatch.className = "lft-flaechen-picker__swatch";
        swatch.style.background = fl.hatchColor || fl.stroke || "#e53e3e";
        swatch.style.borderColor = fl.stroke || "#e53e3e";
        if (fl.dash) { swatch.style.borderStyle = "dashed"; }
        btn.appendChild(swatch);
        var label = document.createElement("span");
        label.textContent = fl.name;
        btn.appendChild(label);
        btn.addEventListener("click", function () {
          pendingFlaecheStyle = fl;
          if (karte.pm) { karte.pm.enableDraw("Polygon"); }
        });
        grid.appendChild(btn);
      });
      el.appendChild(grid);
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
        } else if (data.type === "lagefuehrung.chronologie_changed") {
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
