/* Objekt-Lagekarte: Leaflet + Geoman-Editor mit Symbolpalette.
 *
 * Symbol-Rendering analog taktSymbolHtml der GSL-Karte (incident_major/karte.html):
 * rot umrandete Text-Boxen (FSD/BMZ/FBF/...), gelbe Gefahren-Dreiecke, Pfeile
 * fuer Zugaenge, Hydranten-Symbole. Kein Server-Symbolkatalog.
 *
 * initObjektKarte({ elementId, objektId, csrfToken, editierbar })
 */
(function () {
  "use strict";

  var SYMBOLE = {
    fsd:               { text: "FSD",  stil: "box" },
    schluesselbox:     { text: "BOX",  stil: "box" },
    bsp:               { text: "BSP",  stil: "box" },
    bmz:               { text: "BMZ",  stil: "box" },
    fbf:               { text: "FBF",  stil: "box" },
    dlk_stellplatz:    { text: "DLK",  stil: "box" },
    objektfunk:        { text: "FUNK", stil: "box" },
    sammelplatz:       { text: "SP",   stil: "gruen" },
    feuerloescher:     { text: "FL",   stil: "rot" },
    hauptzugang:       { text: "➜",    stil: "pfeil-voll" },
    nebenzugang:       { text: "➜",    stil: "pfeil-leer" },
    stiege:            { text: "ST",   stil: "gruen" },
    aufzug:            { text: "AZ",   stil: "box" },
    gefahr_ex:         { text: "EX",   stil: "dreieck" },
    gefahr_gas:        { text: "GAS",  stil: "dreieck" },
    gefahr_chemie:     { text: "CHE",  stil: "dreieck" },
    gefahr_strom:      { text: "kV",   stil: "dreieck" },
    gefahr_pv:         { text: "PV",   stil: "dreieck" },
    hydrant_ueberflur: { text: "H",    stil: "hydrant" },
    hydrant_unterflur: { text: "UH",   stil: "hydrant" }
  };

  // Org-Symbolkatalog (aus /objekte/karten-symbole.json) in die SYMBOLE-Map mergen.
  // Eingebaute Defaults bleiben als Fallback erhalten.
  function setObjektSymbole(liste) {
    (liste || []).forEach(function (s) {
      if (!s || !s.code) { return; }
      SYMBOLE[s.code] = { text: s.text || "", stil: s.stil || "box", bild: s.bild || null };
    });
  }
  window.setObjektSymbole = setObjektSymbole;

  var _katalogGeladen = null;
  function ladeObjektSymbole(url) {
    if (_katalogGeladen) { return _katalogGeladen; }
    _katalogGeladen = fetch(url || "/objekte/karten-symbole.json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.symbole) { setObjektSymbole(d.symbole); } })
      .catch(function () { /* Fallback: eingebaute SYMBOLE */ });
    return _katalogGeladen;
  }
  window.ladeObjektSymbole = ladeObjektSymbole;

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function objektSymbolHtml(typ, label, rotation) {
    // Unbekannte Typen (z. B. aus EUS-Import): Typ-Kuerzel statt "?" anzeigen
    var s = SYMBOLE[typ] || { text: String(typ || "?").substring(0, 4).toUpperCase(), stil: "box" };
    var inner;
    if (s.stil === "bild" && s.bild) {
      inner = '<div class="oks oks--bild"><img src="' + escapeHtml(s.bild) + '" alt="" draggable="false"></div>';
    } else if (s.stil === "dreieck") {
      inner = '<div class="oks oks--dreieck"><span>' + escapeHtml(s.text) + "</span></div>";
    } else if (s.stil === "hydrant") {
      inner = '<div class="oks oks--hydrant">' + escapeHtml(s.text) + "</div>";
    } else if (s.stil === "pfeil-voll") {
      inner = '<div class="oks oks--pfeil oks--pfeil-voll">' + escapeHtml(s.text) + "</div>";
    } else if (s.stil === "pfeil-leer") {
      inner = '<div class="oks oks--pfeil oks--pfeil-leer">' + escapeHtml(s.text) + "</div>";
    } else if (s.stil === "gruen") {
      inner = '<div class="oks oks--box oks--gruen">' + escapeHtml(s.text) + "</div>";
    } else if (s.stil === "rot") {
      inner = '<div class="oks oks--box oks--rot">' + escapeHtml(s.text) + "</div>";
    } else {
      inner = '<div class="oks oks--box">' + escapeHtml(s.text) + "</div>";
    }
    // Eigener innerer Wrapper: Leaflets Positions-Transform bleibt unberuehrt.
    var winkel = ((Number(rotation) || 0) % 360 + 360) % 360;
    inner = '<div style="transform: rotate(' + winkel + 'deg)">' + inner + "</div>";
    if (label) {
      inner += '<div class="oks__label">' + escapeHtml(label) + "</div>";
    }
    return '<div class="oks-wrap">' + inner + "</div>";
  }

  // Auch global verfuegbar (Alarm-Infoscreen rendert Symbole ohne initObjektKarte)
  window.objektSymbolHtml = objektSymbolHtml;

  var HYDRANT_TEXT = { ueberflur: "H", unterflur: "UH", loeschwasser: "≈" };
  var HYDRANT_LABEL = {
    ueberflur: "Überflurhydrant", unterflur: "Unterflurhydrant", loeschwasser: "Löschwasserstelle"
  };
  function hydrantIcon(typ) {
    return L.divIcon({
      html: '<div class="hydrant-icon hydrant-icon--' + (typ || "hydrant") + '">' +
        (HYDRANT_TEXT[typ] || "H") + "</div>",
      className: "hydrant-divicon", iconSize: null, iconAnchor: [11, 11]
    });
  }

  function symbolIcon(typ, label, rotation) {
    return L.divIcon({
      html: objektSymbolHtml(typ, label, rotation),
      className: "oks-divicon",
      iconSize: null,
      iconAnchor: [16, 16]
    });
  }

  var WARNUNGEN = {
    weit_vom_objekt: "Position liegt mehr als 2 km vom Objekt entfernt.",
    einsatzkritisch_freigabe_erforderlich: "Einsatzkritisches Symbol: Position und Bedeutung ausdrücklich geprüft.",
    moegliche_dublette: "Mögliche Dublette: mit bestehendem Marker oder anderem Vorschlag abgleichen.",
    position_manuell: "Position wurde manuell korrigiert.",
    georef_ungenau: "Georeferenz ist ungenau.",
    georef_veraltet: "Georeferenz ist veraltet; Position erneut prüfen.",
    georef_ungueltig: "Georeferenz ist ungültig.",
    georef_ungeprueft: "Georeferenz ist noch ungeprüft.",
    georef_entwurf: "Georeferenz ist noch ein Entwurf.",
    georef_abgelehnt: "Georeferenz wurde abgelehnt; Position erneut bestimmen."
  };
  function warntext(w) {
    return (WARNUNGEN[w.code] || "Unbekannte Warnung: " + w.code) +
      (w.marker_id ? " (Marker #" + w.marker_id + ")" : "") +
      (w.vorschlag_id ? " (Vorschlag #" + w.vorschlag_id + ")" : "");
  }
  function geometrie(v) {
    return typeof v.geometry_json === "string" ? JSON.parse(v.geometry_json) : v.geometry_json || v.geometry;
  }
  function position(v) {
    if (v.lat != null && v.lng != null) { return [v.lat, v.lng]; }
    var g = geometrie(v);
    return g && g.type === "Point" ? [g.coordinates[1], g.coordinates[0]] : null;
  }
  function prueffaehig(v) {
    return v.freigabe_status === "offen" && v.position_status === "vorgeschlagen" &&
      !!(position(v) || geometrie(v));
  }
  function filterPasst(v, filter) {
    return filter === "alle" || (filter === "neu" && v.operation === "hinzufuegen") ||
      (filter === "aenderungen" && v.operation !== "hinzufuegen") ||
      (filter === "unklar" && v.position_status === "pruefung_erforderlich") ||
      (filter === "veraltet" && v.position_status === "veraltet") ||
      (filter === "dubletten" && v.warnungen.some(function (w) { return w.code === "moegliche_dublette"; }));
  }
  // Kleine reine Helfer auch fuer die bestehenden Node-Tests.
  window.objektKarteReview = { prueffaehig: prueffaehig, filterPasst: filterPasst, warntext: warntext };

  // CRS.Simple: Latitude ist die negative Bild-Y-Achse, Bildmasse sind Pixel.
  function planNormiert(latlng, breite, hoehe) {
    return { x: latlng.lng / breite, y: -latlng.lat / hoehe };
  }
  window.objektKarteGeoref = { planNormiert: planNormiert };

  function georefEditor(root, v, karte, get, post, aktualisieren) {
    var dialog = document.createElement("dialog");
    dialog.className = "oks-georef";
    dialog.setAttribute("aria-label", "Georeferenz der Planseite bearbeiten");
    root.appendChild(dialog);
    function el(tag, text, parent, klasse) {
      var n = document.createElement(tag);
      if (text != null) { n.textContent = text; }
      if (klasse) { n.className = klasse; }
      parent.appendChild(n); return n;
    }
    function button(text, fn) {
      var b = el("button", text, aktionen, "btn btn--ghost btn--xs");
      b.type = "button"; b.onclick = fn; return b;
    }
    el("h3", "Georeferenz · Seite " + v.quelle_seite, dialog);
    var status = el("p", "Plan wird geladen ...", dialog);
    status.setAttribute("role", "status");
    var quelleStatus = el("p", "", dialog);
    var netz = el("p", "", dialog);
    var modellLabel = el("label", "Transformationsmodell ", dialog);
    var modell = el("select", null, modellLabel, "form-select");
    [["aehnlichkeit", "Ähnlichkeit (ab 3 Paaren)"], ["affin", "Affin (ab 4 Paaren)"],
      ["homographie", "Homographie (ab 6 Paaren)"]].forEach(function (item) {
      var o = el("option", item[1], modell); o.value = item[0];
    });
    el("p", "Zuerst im Plan klicken, dann den Gegenpunkt auf der Karte wählen. Gebäudeecken rasten ein. Nummerierte Punkte können verschoben werden.", dialog);
    var anweisung = el("p", "Punkt im Plan wählen.", dialog);
    var layout = el("div", null, dialog, "oks-georef__layout");
    var planDiv = el("div", null, layout, "oks-georef__karte");
    planDiv.setAttribute("aria-label", "Planseite mit Zoom und Verschieben");
    var mapDiv = el("div", null, layout, "oks-georef__karte");
    mapDiv.setAttribute("aria-label", "Karte mit Referenzpunkten");
    var liste = el("div", null, dialog);
    var aktionen = el("div", null, dialog, "oks-review__aktionen");
    var paare = [], pending = null, plan, map, meta, revision = 0, generation = 0, offen = true;
    var marker = [], vorgemerkt = null, saving = false;
    var speichern = button("Speichern", function () {
      speichern.disabled = true; saving = true;
      modell.disabled = true; verwerfen.disabled = true;
      post("/georef", payload(false)).then(function () {
        status.textContent = "Georeferenz gespeichert. Vorschläge wurden neu berechnet.";
        return aktualisieren();
      }).then(function () { dialog.close(); }).catch(fehler).finally(function () { saving = false; modell.disabled = false; verwerfen.disabled = false; });
    });
    speichern.disabled = true;
    var verwerfen = button("Georeferenz verwerfen", function () {
      speichern.disabled = true; saving = true; generation++;
      modell.disabled = true; verwerfen.disabled = true;
      post("/georef/verwerfen", payload(false)).then(function () {
        return aktualisieren();
      }).then(function () { dialog.close(); }).catch(fehler).finally(function () { saving = false; modell.disabled = false; verwerfen.disabled = false; });
    });
    verwerfen.disabled = true;
    button("Schließen", function () { dialog.close(); });
    dialog.addEventListener("close", function () {
      offen = false; generation++;
      if (plan) { plan.remove(); } if (map) { map.remove(); }
      dialog.remove();
    });
    function fehler(err) { if (offen) { status.textContent = err.message; speichern.disabled = true; } }
    function payload(pruefen) {
      return { dokument_id: v.quelle_dokument_id, seite: v.quelle_seite, punkte: paare,
        modell: modell.value, pruefen: pruefen, expected_revision: revision, expected_rotation: meta.rotation };
    }
    function guete(node, wert, grenze) {
      node.className = wert != null && wert <= grenze ? "oks-georef__gut" : "oks-georef__fehler";
    }
    function pruefen() {
      if (saving) { return; }
      render();
      var lauf = ++generation;
      speichern.disabled = true;
      status.textContent = "Referenzpunkte werden geprüft ...";
      post("/georef", payload(true)).then(function (antwort) {
        if (!offen || lauf !== generation) { return; }
        var g = antwort.georeferenz;
        status.replaceChildren();
        el("span", g.status + " · ", status);
        var rmse = el("span", "RMSE: " + meter(g.rmse_m), status);
        guete(rmse, g.rmse_m, meta.schwellen.rmse_m);
        el("span", " · ", status);
        var maximum = el("span", "Maximum: " + meter(g.max_fehler_m), status);
        guete(maximum, g.max_fehler_m, meta.schwellen.max_fehler_m);
        if (g.ablehngrund) { el("span", " · " + g.ablehngrund, status); }
        guete(status, g.status === "gueltig" ? 0 : null, 0);
        (g.residuen || []).forEach(function (r) {
          var zeile = liste.querySelector('[data-punkt="' + String(r.id).replace(/[^0-9]/g, "") + '"]');
          if (zeile) {
            var rest = el("span", " Residuum: " + meter(r.fehler_m), zeile);
            guete(rest, r.fehler_m, meta.schwellen.max_fehler_m);
          }
        });
        speichern.disabled = g.status !== "gueltig" || pending != null || saving;
      }).catch(function (err) { if (lauf === generation) { fehler(err); } });
    }
    function meter(n) { return n == null ? "unbekannt" : n.toFixed(2) + " m"; }
    function nummer(n) {
      var span = document.createElement("span"); span.textContent = String(n);
      return L.divIcon({ html: span, className: "oks-georef__nummer", iconSize: [24, 24] });
    }
    function render() {
      marker.forEach(function (m) { m.remove(); }); marker = [];
      liste.replaceChildren();
      paare.forEach(function (p, i) {
        var a = L.marker([-p.y * meta.bildgroesse[1], p.x * meta.bildgroesse[0]],
          { draggable: true, icon: nummer(i + 1) }).addTo(plan);
        var b = L.marker([p.lat, p.lng], { draggable: true, icon: nummer(i + 1) }).addTo(map);
        marker.push(a, b);
        a.on("dragend", function () {
          if (saving) { render(); return; }
          var xy = planNormiert(a.getLatLng(), meta.bildgroesse[0], meta.bildgroesse[1]);
          if (xy.x >= 0 && xy.x <= 1 && xy.y >= 0 && xy.y <= 1) { p.x = xy.x; p.y = xy.y; }
          pruefen();
        });
        b.on("dragend", function () {
          if (saving) { render(); return; }
          var pos = b.getLatLng(); p.lat = pos.lat; p.lng = pos.lng;
          p.quelle = map.hasLayer(ortho) ? "orthofoto_klick" : "manuell";
          p.beschreibung = "Verschobener Kartenpunkt"; pruefen();
        });
        var row = el("p", (i + 1) + ": " + p.quelle + " · " + p.beschreibung + " ", liste);
        row.dataset.punkt = String(p.id);
        var del = el("button", "Paar löschen", row, "btn btn--ghost btn--xs");
        del.type = "button"; del.onclick = function () { if (saving) { return; } paare.splice(i, 1); pruefen(); };
      });
    }
    function gegenpunkt(pos, quelle, beschreibung) {
      if (!pending || saving) { return; }
      var id = paare.reduce(function (m, p) { return Math.max(m, Number(p.id) || 0); }, 0) + 1;
      paare.push({ id: id, x: pending.x, y: pending.y, lat: pos.lat, lng: pos.lng,
        quelle: quelle, beschreibung: beschreibung });
      pending = null; if (vorgemerkt) { vorgemerkt.remove(); vorgemerkt = null; }
      anweisung.textContent = "Punkt im Plan wählen.";
      pruefen();
    }
    var ortho;
    dialog.showModal();
    get("/georef.json?dokument_id=" + v.quelle_dokument_id + "&seite=" + v.quelle_seite).then(function (daten) {
      if (!offen) { return; }
      meta = daten;
      var g = daten.georeferenz;
      revision = g ? g.revision : 0;
      modell.value = g ? g.modell : "aehnlichkeit";
      // Veraltete Koordinaten duerfen nach einer Drehung nicht wieder gespeichert werden.
      paare = g && g.status !== "veraltet" ? g.referenzpunkte.map(function (p, i) { return Object.assign({}, p, { id: i + 1 }); }) : [];
      quelleStatus.textContent = g ? "Gespeicherter Satz: " + g.status +
        (g.ablehngrund ? " · " + g.ablehngrund : "") : "Noch keine Georeferenz.";
      status.textContent = "Punktpaare erfassen und prüfen.";
      if (g && g.status === "veraltet") {
        quelleStatus.textContent += " · Bildkoordinaten neu erfassen.";
        quelleStatus.className = "oks-georef__fehler";
      }
      plan = L.map(planDiv, { crs: L.CRS.Simple, minZoom: -5 });
      var bounds = [[-meta.bildgroesse[1], 0], [0, meta.bildgroesse[0]]];
      L.imageOverlay(meta.seitenbild_url, bounds).addTo(plan); plan.fitBounds(bounds);
      map = L.map(mapDiv).setView(karte.getCenter(), Math.max(karte.getZoom(), 18));
      var osm = window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
      ortho = L.tileLayer("https://mapsneu.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg",
        { maxZoom: 20, attribution: "Datenquelle: basemap.at" });
      L.control.layers({ OpenStreetMap: osm, Orthofoto: ortho }).addTo(map);
      plan.on("click", function (ev) {
        if (saving) { return; }
        var xy = planNormiert(ev.latlng, meta.bildgroesse[0], meta.bildgroesse[1]);
        if (xy.x < 0 || xy.x > 1 || xy.y < 0 || xy.y > 1) { return; }
        pending = xy; generation++; speichern.disabled = true;
        if (vorgemerkt) { vorgemerkt.remove(); }
        vorgemerkt = L.circleMarker(ev.latlng, { radius: 7 }).addTo(plan);
        anweisung.textContent = "Jetzt Gegenpunkt auf der Karte wählen (Gebäudeecke oder freier Klick).";
      });
      map.on("click", function (ev) {
        gegenpunkt(ev.latlng, map.hasLayer(ortho) ? "orthofoto_klick" : "manuell", "Freier Kartenpunkt");
      });
      modell.onchange = pruefen;
      render();
      verwerfen.disabled = !g;
      if (g && g.status !== "veraltet") { pruefen(); }
      get("/gebaeude.json").then(function (antwort) {
        if (!offen) { return; }
        netz.textContent = antwort.gebaeude.length ? "OSM-Gebäudeecken geladen." : "Keine OSM-Gebäudeecken gefunden. Freie Kartenklicks sind möglich.";
        antwort.gebaeude.forEach(function (haus) { haus.ecken.forEach(function (ecke) {
          var e = L.circleMarker([ecke.lat, ecke.lng], { radius: 6, color: "#2879b9", bubblingMouseEvents: false }).addTo(map);
          var text = document.createElement("span"); text.textContent = "OSM " + haus.osm_id + " · Ecke " + ecke.nummer;
          e.bindTooltip(text);
          e.on("click", function () { gegenpunkt(ecke, "osm_gebaeude", text.textContent); });
        }); });
      }).catch(function (err) { if (offen) { netz.textContent = err.message + " Freie Kartenklicks sind weiterhin möglich."; } });
    }).catch(fehler);
  }

  function initReview(root, karte, get, post, markerErsetzen, modusWechsel) {
    var modus = root.querySelector("[data-review-modus]");
    var panel = root.querySelector("[data-review-panel]");
    var liste = root.querySelector("[data-review-liste]");
    var detail = root.querySelector("[data-review-detail]");
    var filter = root.querySelector("[data-review-filter]");
    var freigabe = root.querySelector("[data-review-freigeben]");
    var dialog = root.querySelector("[data-review-dialog]");
    var fehler = root.querySelector("[data-review-fehler]");
    var sperre = root.querySelector("[data-review-sperre]");
    var layout = root.nextElementSibling;
    // Panel neben der Karte; Schalter und Dialog bleiben oberhalb erreichbar.
    if (layout) { layout.insertBefore(panel, layout.firstChild); }
    var gesperrt = root.dataset.gesperrt === "1";
    var daten = [], layers = [], auswahl = new Set(), aktiv = null, busy = false;
    var operationen = { hinzufuegen: "Neu", aendern: "Änderung", entfernen: "Entfernen" };

    function el(tag, text, parent, klasse) {
      var node = document.createElement(tag);
      if (text != null) { node.textContent = text; }
      if (klasse) { node.className = klasse; }
      if (parent) { parent.appendChild(node); }
      return node;
    }
    function button(text, parent, aktion) {
      var b = el("button", text, parent, "btn btn--ghost btn--xs");
      b.type = "button";
      b.disabled = busy;
      b.onclick = aktion;
      return b;
    }
    function meldung(err) {
      fehler.hidden = false;
      fehler.textContent = err.message || "Vorschläge konnten nicht geladen werden.";
      var app = document.querySelector('[x-data="appState()"]');
      if (app && window.Alpine) { window.Alpine.$data(app).addToast(fehler.textContent, "error"); }
    }
    function leeren() {
      layers.forEach(function (layer) { karte.removeLayer(layer); });
      layers = [];
    }
    function layerDazu(layer) { layer.addTo(karte); layers.push(layer); return layer; }
    function waehlen(v, zentrieren) {
      aktiv = v.id;
      if (zentrieren) {
        if (position(v)) { karte.setView(position(v), Math.max(karte.getZoom(), 19)); }
        else if (geometrie(v)) { karte.fitBounds(L.geoJSON(geometrie(v)).getBounds()); }
      }
      details(v);
    }
    function korrigieren(v, pos, rotation, label) {
      return mutation("/vorschlaege/" + v.id + "/korrigieren", {
        lat: pos[0], lng: pos[1], rotation: rotation, label: label
      });
    }
    function zeichnen() {
      leeren();
      if (!modus.checked) { return; }
      daten.filter(function (v) { return filterPasst(v, filter.value); }).forEach(function (v) {
        var pos = position(v), g = geometrie(v), layer;
        if (!pos && v.position_status === "pruefung_erforderlich") { return; }
        var stil = { color: "#7654be", dashArray: "6 5", weight: 3, fillOpacity: 0.12, pmIgnore: true };
        if (pos) {
          var icon = symbolIcon(v.symbol_code, v.label, v.rotation);
          icon.options ? icon.options.className += " oks-review__marker" : icon.className += " oks-review__marker";
          layer = L.marker(pos, { icon: icon, draggable: !busy, pmIgnore: true });
          layer.on("dragend", function (ev) {
            var p = ev.target.getLatLng();
            korrigieren(v, [p.lat, p.lng], v.rotation, v.label);
          });
          if (v.genauigkeit_m != null) {
            layerDazu(L.circle(pos, Object.assign({ radius: v.genauigkeit_m, interactive: false }, stil)));
          }
        } else if (g && v.position_status !== "pruefung_erforderlich") {
          layer = L.geoJSON(g, { style: stil, pmIgnore: true });
        }
        if (layer) { layer.on("click", function () { waehlen(v, false); }); layerDazu(layer); }
        var alt = v.basis_marker;
        if (alt && v.operation !== "hinzufuegen") {
          var altpos = position(alt);
          if (altpos) {
            var geist = symbolIcon(alt.typ, alt.label, alt.rotation);
            geist.options ? geist.options.className += " oks-review__geist" : geist.className += " oks-review__geist";
            layerDazu(L.marker(altpos, { icon: geist, interactive: false, pmIgnore: true }));
            if (pos) { layerDazu(L.polyline([altpos, pos], Object.assign({ interactive: false }, stil))); }
          } else if (alt.geometry) {
            layerDazu(L.geoJSON(alt.geometry, { style: Object.assign({ opacity: 0.4 }, stil), pmIgnore: true }));
          }
        }
      });
    }
    function render() {
      sperre.hidden = !gesperrt;
      freigabe.disabled = gesperrt || busy || auswahl.size === 0;
      root.querySelector("[data-review-count]").textContent = daten.length;
      liste.replaceChildren();
      daten.filter(function (v) { return filterPasst(v, filter.value); }).forEach(function (v) {
        var row = el("div", null, liste, "oks-review__eintrag");
        if (prueffaehig(v)) {
          var check = el("input", null, row);
          check.type = "checkbox";
          check.checked = auswahl.has(v.id);
          check.disabled = busy || gesperrt;
          check.setAttribute("aria-label", "Vorschlag #" + v.id + " auswählen");
          check.onchange = function () {
            if (check.checked) { auswahl.add(v.id); } else { auswahl.delete(v.id); }
            freigabe.disabled = gesperrt || busy || auswahl.size === 0;
          };
        }
        button("#" + v.id + " · " + operationen[v.operation] + " · " + (v.label || v.symbol_code) +
          (v.position_status === "veraltet" ? " · Veraltet" : ""), row, function () { waehlen(v, true); });
      });
      if (!liste.childNodes.length) { el("p", "Keine offenen Vorschläge für diesen Filter.", liste); }
      var v = daten.find(function (v) { return v.id === aktiv; });
      if (v) { details(v); } else { detail.textContent = "Vorschlag aus der Liste oder auf der Karte auswählen."; }
      zeichnen();
    }
    function feld(text, wert, typ, parent) {
      var gruppe = el("div", null, parent, "form-group");
      var label = el("label", text, gruppe, "form-label");
      var input = el("input", null, gruppe, "form-input");
      input.type = typ;
      input.value = wert == null ? "" : wert;
      input.id = "oks-review-feld-" + text.replace(/[^a-z]/gi, "");
      label.htmlFor = input.id;
      input.disabled = busy;
      return input;
    }
    function details(v) {
      detail.replaceChildren();
      el("h4", "#" + v.id + " · " + operationen[v.operation] + " · Symbol: " + v.symbol_code, detail);
      var quelle = el("div", null, detail, "oks-review__quelle");
      var texte = el("div", null, quelle);
      el("p", "Positionsherkunft: " + v.position_methode, texte);
      el("p", "Genauigkeit: " + (v.genauigkeit_m == null ? "unbekannt" : v.genauigkeit_m + " m"), texte);
      el("p", "Erkennungskonfidenz: " + (v.erkennungs_konfidenz == null ? "unbekannt" :
        Math.round(v.erkennungs_konfidenz * 100) + " %"), texte);
      el("p", "Begründung: " + (v.begruendung || "Keine Angabe"), texte);
      el("p", "Quelle: " + (v.quelle_dokument_name || (v.quelle_dokument_id ? "Dokument #" + v.quelle_dokument_id : "Keine Angabe")) +
        (v.quelle_seite ? " · Seite " + v.quelle_seite : ""), texte);
      if (Number.isInteger(v.quelle_seite_id)) {
        var link = el("a", "Dokumentseite öffnen", texte);
        link.href = "/objekt-medien/seite/" + v.quelle_seite_id + "/pdf";
        link.target = "_blank"; link.rel = "noopener";
        var bildLink = el("a", null, quelle);
        bildLink.href = link.href; bildLink.target = "_blank"; bildLink.rel = "noopener";
        var bild = el("img", null, bildLink);
        bild.src = "/objekt-medien/seite/" + v.quelle_seite_id + "/thumb";
        bild.alt = "Vorschau Quelldokument, Seite " + v.quelle_seite;
        bild.loading = "lazy";
      }
      if (v.quelle_dokument_id && v.quelle_seite) {
        button("Georeferenz", texte, function () { georefEditor(root, v, karte, get, post, laden); });
      }
      if (v.altposition && v.altposition.lat != null) {
        el("p", "Altposition (Georeferenz ungültig): " + v.altposition.lat + ", " + v.altposition.lng, texte);
      }
      v.warnungen.forEach(function (w) { el("p", warntext(w), detail); });
      var form = el("form", null, detail);
      var pos = position(v);
      el("p", "Position per Marker verschieben oder Koordinaten eingeben. Korrektur wird als manuell geprüft gespeichert.", form, "form-hint");
      var lat = feld("Breitengrad", pos && pos[0], "number", form);
      var lng = feld("Längengrad", pos && pos[1], "number", form);
      lat.step = lng.step = "any"; lat.required = lng.required = true;
      lat.min = "-90"; lat.max = "90"; lng.min = "-180"; lng.max = "180";
      var rotation = feld("Drehung in Grad", v.rotation, "number", form);
      rotation.step = "1"; rotation.required = true;
      var label = feld("Beschriftung", v.label, "text", form); label.maxLength = 100;
      // Geometrien duerfen durch eine Textkorrektur nicht versehentlich zu Punkten werden.
      var speichern = button("Korrektur speichern", form, null);
      var nurGeometrie = !!(geometrie(v) && !pos);
      speichern.type = "submit"; speichern.disabled = busy || nurGeometrie;
      if (nurGeometrie) {
        [lat, lng, rotation, label].forEach(function (input) { input.disabled = true; });
        el("p", "Die Positionskorrektur ist hier nur für Punktsymbole verfügbar. Linien und Flächen können geprüft, freigegeben oder abgelehnt werden.", form, "form-hint");
      }
      form.onsubmit = function (ev) {
        ev.preventDefault();
        korrigieren(v, [Number(lat.value), Number(lng.value)], Number(rotation.value), label.value);
      };
      var grund = feld("Ablehngrund", "", "text", detail); grund.maxLength = 10000;
      var aktionen = el("div", null, detail, "oks-review__aktionen");
      button("Ablehnen", aktionen, function () {
        if (!grund.value.trim()) { grund.focus(); meldung(new Error("Bitte einen Ablehngrund eingeben.")); return; }
        mutation("/vorschlaege/" + v.id + "/ablehnen", { grund: grund.value });
      });
      var b = button("Freigeben", aktionen, function () { freigeben([v]); });
      b.disabled = busy || gesperrt || !prueffaehig(v);
    }
    function laden() {
      // Review-GET markiert veraltete Quellen; produktive Marker danach lesen.
      return get("/vorschlaege.json").then(function (review) {
        return get("/objekte.json").then(function (karteDaten) {
          daten = review.vorschlaege.filter(function (v) { return v.freigabe_status === "offen"; });
          gesperrt = review.freigabe_gesperrt;
          auswahl.clear();
          markerErsetzen(karteDaten.eintraege);
          render();
        });
      });
    }
    function mutation(pfad, payload) {
      if (busy) { return Promise.resolve(); }
      busy = true; fehler.hidden = true; render();
      dialog.querySelectorAll("button, input").forEach(function (e) { e.disabled = true; });
      return post(pfad, payload).then(function () {
        if (dialog.open) { dialog.close(); }
        return laden();
      }).catch(function (err) {
        meldung(err);
        // Auch Drag-Vorschau zuruecksetzen und Konflikte neu einlesen.
        return laden().catch(meldung);
      }).finally(function () {
        busy = false;
        render();
        // Ein alter Dialog darf nach Konflikten keine veraltete Auswahl bestaetigen.
        if (dialog.open) { dialog.close(); }
      });
    }
    function freigeben(vorschlaege) {
      if (busy || gesperrt || !vorschlaege.length) { return; }
      if (vorschlaege.length > 30) { meldung(new Error("Bitte maximal 30 Vorschläge auswählen.")); return; }
      dialog.replaceChildren();
      el("h3", "Freigabe bestätigen", dialog);
      var checks = [];
      vorschlaege.forEach(function (v) {
        el("p", "#" + v.id + " · " + (v.label || v.symbol_code), dialog);
        var warnungen = v.warnungen.slice();
        var kritisch = function (code) {
          return ["fsd", "bmz", "fbf", "hauptzugang", "nebenzugang", "dlk_stellplatz"].includes(code) || /^(gefahr_|hydrant_)/.test(code);
        };
        if ((kritisch(v.symbol_code) || (v.basis_marker && kritisch(v.basis_marker.typ))) &&
            !warnungen.some(function (w) { return w.code === "einsatzkritisch_freigabe_erforderlich"; })) {
          warnungen.push({ code: "einsatzkritisch_freigabe_erforderlich" });
        }
        warnungen.forEach(function (w) {
          var label = el("label", null, dialog);
          var check = el("input", null, label); check.type = "checkbox";
          el("span", " " + warntext(w), label);
          checks.push({ id: v.id, code: w.code, input: check });
        });
      });
      var aktionen = el("div", null, dialog, "oks-review__aktionen");
      button("Abbrechen", aktionen, function () { dialog.close(); });
      var bestaetigen = button("Jetzt freigeben", aktionen, function () {
        if (checks.some(function (c) { return !c.input.checked; })) { return; }
        var bestaetigungen = {};
        checks.forEach(function (c) {
          if (!bestaetigungen[c.id]) { bestaetigungen[c.id] = []; }
          bestaetigungen[c.id].push(c.code);
        });
        mutation("/vorschlaege/freigeben", {
          ids: vorschlaege.map(function (v) { return v.id; }), bestaetigte_warnungen: bestaetigungen
        });
      });
      function aktualisieren() { bestaetigen.disabled = checks.some(function (c) { return !c.input.checked; }); }
      checks.forEach(function (c) { c.input.onchange = aktualisieren; });
      aktualisieren(); dialog.showModal();
    }
    freigabe.onclick = function () { freigeben(daten.filter(function (v) { return auswahl.has(v.id); })); };
    filter.onchange = render;
    modus.onchange = function () {
      panel.hidden = !modus.checked;
      if (layout) { layout.classList.toggle("oks-review__layout", modus.checked); }
      modusWechsel(modus.checked);
      karte.invalidateSize();
      if (modus.checked) { laden().catch(meldung); } else { leeren(); }
    };
  }

  window.initObjektKarte = function (opts) {
    var karte = L.map(opts.elementId, { zoomControl: true });
    window.EinsatzcockpitMapConfig.addOsmTileLayer(karte);

    var layerById = {};
    var aktiverTyp = null;

    function apiUrl(pfad) {
      return "/objekte/" + opts.objektId + "/karte" + pfad;
    }

    function apiGet(pfad) {
      return fetch(apiUrl(pfad)).then(antwortLesen);
    }
    function antwortLesen(r) {
      return r.json().then(function (daten) {
        if (!r.ok) {
          var detail = daten.detail;
          var text = typeof detail === "string" ? detail : (detail && detail.meldung) || "Ungültige Eingabe";
          if (detail && detail.ergebnisse) {
            text += ": " + detail.ergebnisse.map(function (e) { return "#" + e.id + " " + e.ursache; }).join("; ");
          }
          throw new Error((r.status === 409 ? "Konflikt (Revision oder Arbeitskopie). " : "") + text +
            (r.status === 409 || r.status === 422 ? " Es wurde nichts übernommen. Bitte Daten neu laden und prüfen." : ""));
        }
        return daten;
      });
    }
    function apiPost(pfad, daten) {
      return fetch(apiUrl(pfad), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": opts.csrfToken
        },
        body: JSON.stringify(daten)
      }).then(antwortLesen);
    }

    function eintragEntfernen(id) {
      apiPost("/objekte/" + id + "/loeschen", {}).then(function () {
        if (layerById[id]) { karte.removeLayer(layerById[id]); delete layerById[id]; }
      }).catch(function () { alert("Löschen fehlgeschlagen"); });
    }

    function popupHtml(e) {
      var html = "<strong>" + (e.label || e.typ).replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</strong>";
      if (opts.editierbar) {
        var status = { bestaetigt: "Bestätigt", veraltet: "Veraltet" }[e.position_status] || "Legacy / ungeprüft";
        html += '<span class="oks-review__status">' + status + '</span>';
        if (!e.geometry || e.geometry.type === "Point") {
          html += '<div>Drehung: <span data-winkel>' + (e.rotation || 0) + '</span>°</div>' +
            '<div><button type="button" class="btn btn--xs" data-drehung="-15">-15°</button> ' +
            '<button type="button" class="btn btn--xs" data-drehung="15">+15°</button> ' +
            '<button type="button" class="btn btn--xs" data-drehung="90">+90°</button> ' +
            '<button type="button" class="btn btn--xs" data-reset>Zurücksetzen</button></div>' +
            '<input type="range" min="0" max="359" step="1" aria-label="Drehwinkel" value="' +
            (e.rotation || 0) + '" data-drehwinkel>';
        }
        html += '<br><button type="button" class="btn btn--danger btn--xs" ' +
          'onclick="window._oksDelete(' + e.id + ')">Entfernen</button>';
      }
      return html;
    }
    window._oksDelete = eintragEntfernen;

    function eintragAnzeigen(e) {
      var layer;
      var punktGeometrie = e.geometry && e.geometry.type === "Point";
      if (e.geometry && !punktGeometrie) {
        layer = L.geoJSON(e.geometry, {
          style: { color: "#d42225", weight: 3, fillOpacity: 0.12 }
        });
        if (opts.editierbar) {
          layer.eachLayer(function (geometrieLayer) {
            geometrieLayer.on("pm:edit pm:rotateend", function () {
              var gj = geometrieLayer.toGeoJSON();
              apiPost("/objekte/" + e.id, { geometry: gj.geometry })
                .catch(function () { alert("Geometrie speichern fehlgeschlagen"); });
            });
          });
        }
      } else {
        // Punkte immer als Symbol-Marker rendern — auch wenn sie (z. B. aus dem
        // EUS-Import) als GeoJSON-Point statt lat/lng gespeichert sind. L.geoJSON
        // wuerde sonst Leaflets Default-Icon nutzen, dessen marker-icon.png es
        // unter /static nicht gibt (kaputtes-Bild-Symbol).
        var lat = punktGeometrie ? e.geometry.coordinates[1] : e.lat;
        var lng = punktGeometrie ? e.geometry.coordinates[0] : e.lng;
        layer = L.marker([lat, lng], {
          icon: symbolIcon(e.typ, e.label, e.rotation),
          pmIgnore: true,
          draggable: !!opts.editierbar
        });
        if (opts.editierbar) {
          layer.on("dragend", function (ev) {
            var pos = ev.target.getLatLng();
            var daten = punktGeometrie
              ? { geometry: { type: "Point", coordinates: [pos.lng, pos.lat] } }
              : { lat: pos.lat, lng: pos.lng };
            apiPost("/objekte/" + e.id, daten).catch(function () {});
          });
        }
      }
      layer.bindPopup(function () { return popupHtml(e); });
      if (opts.editierbar && (!e.geometry || punktGeometrie)) {
        layer.on("popupopen", function (ev) {
          var popup = ev.popup.getElement();
          var slider = popup.querySelector("[data-drehwinkel]");
          var anzeige = popup.querySelector("[data-winkel]");
          var controls = popup.querySelectorAll("button, input");
          function vorschau(winkel) {
            winkel = (winkel % 360 + 360) % 360;
            layer.setIcon(symbolIcon(e.typ, e.label, winkel));
            anzeige.textContent = winkel;
            slider.value = winkel;
            return winkel;
          }
          function speichern(winkel) {
            winkel = vorschau(winkel);
            controls.forEach(function (control) { control.disabled = true; });
            apiPost("/objekte/" + e.id, { rotation: winkel }).then(function (gespeichert) {
              e.rotation = gespeichert.rotation;
              vorschau(e.rotation);
            }).catch(function () {
              vorschau(e.rotation || 0);
              alert("Drehung speichern fehlgeschlagen");
            }).finally(function () {
              controls.forEach(function (control) { control.disabled = false; });
            });
          }
          popup.querySelectorAll("[data-drehung]").forEach(function (button) {
            button.onclick = function () { speichern((e.rotation || 0) + Number(button.dataset.drehung)); };
          });
          popup.querySelector("[data-reset]").onclick = function () { speichern(0); };
          slider.oninput = function () { vorschau(Number(slider.value)); };
          slider.onchange = function () { speichern(Number(slider.value)); };
        });
      }
      layer.addTo(karte);
      layerById[e.id] = layer;
    }

    // Erst den Org-Symbolkatalog laden (auch Bildsymbole), dann Marker rendern.
    ladeObjektSymbole(opts.symbolKatalogUrl)
      .then(function () { return fetch(apiUrl("/objekte.json")); })
      .then(function (r) { return r.json(); })
      .then(function (daten) {
        var o = daten.objekt;
        if (o.lat != null && o.lng != null) {
          karte.setView([o.lat, o.lng], 18);
        } else {
          karte.setView([47.4652, 9.7503], 15); /* Fallback Wolfurt */
        }
        daten.eintraege.forEach(eintragAnzeigen);
        var punkte = daten.eintraege.filter(function (e) { return e.lat != null; });
        if (o.lat == null && punkte.length) {
          karte.setView([punkte[0].lat, punkte[0].lng], 18);
        }
      });

    /* ── Hydranten-/Löschwasser-Layer (OSM/OSMHydrant) ── */
    fetch("/objekte/" + opts.objektId + "/hydranten.json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { return; }
        (d.hydranten || []).forEach(function (h) {
          // Manuelle Objekt-Hydranten sind bereits Symbole auf der Karte — nur OSM zeichnen.
          if (h.quelle === "objekt" || h.lat == null || h.lng == null) { return; }
          L.marker([h.lat, h.lng], { icon: hydrantIcon(h.typ) }).addTo(karte)
            .bindPopup("<strong>" + (HYDRANT_LABEL[h.typ] || "Hydrant") + "</strong>" +
              (h.ref ? "<br>" + h.ref : "") +
              (h.entfernung_m != null ? "<br>" + h.entfernung_m + " m" : ""));
        });
      })
      .catch(function () {});

    if (!opts.editierbar) { return karte; }
    var review = document.getElementById("oks-review");
    if (review) {
      initReview(review, karte, apiGet, apiPost, function (eintraege) {
        Object.keys(layerById).forEach(function (id) { karte.removeLayer(layerById[id]); });
        layerById = {};
        eintraege.forEach(eintragAnzeigen);
      }, function (aktiv) {
        aktiverTyp = null;
        if (karte.pm) { karte.pm.disableGlobalEditMode(); karte.pm.disableDraw(); karte.pm.disableGlobalRotateMode(); }
        var paletteElement = document.getElementById("oks-palette");
        if (paletteElement) {
          paletteElement.closest("aside").hidden = aktiv;
          paletteElement.querySelectorAll(".oks-palette__eintrag--aktiv").forEach(function (b) {
            b.classList.remove("oks-palette__eintrag--aktiv");
          });
        }
        if (karte.pm) {
          if (aktiv) { karte.pm.removeControls(); } else { karte.pm.addControls(geomanOptionen); }
        }
      });
    }

    /* ── Editor: Palette (Klick waehlt Symbol, Kartenklick platziert) ── */
    var palette = document.getElementById("oks-palette");
    if (palette) {
      palette.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-typ]");
        if (!btn) { return; }
        var vorher = palette.querySelector(".oks-palette__eintrag--aktiv");
        if (vorher) { vorher.classList.remove("oks-palette__eintrag--aktiv"); }
        if (aktiverTyp === btn.dataset.typ) {
          aktiverTyp = null;
          return;
        }
        aktiverTyp = btn.dataset.typ;
        btn.classList.add("oks-palette__eintrag--aktiv");
      });
    }

    karte.on("click", function (ev) {
      if (!aktiverTyp) { return; }
      var typ = aktiverTyp;
      var label = "";
      var labelFeld = document.getElementById("oks-label");
      if (labelFeld) { label = labelFeld.value.trim(); }
      apiPost("/objekte", { typ: typ, lat: ev.latlng.lat, lng: ev.latlng.lng, label: label })
        .then(function (e) {
          eintragAnzeigen(e);
          if (labelFeld) { labelFeld.value = ""; }
        })
        .catch(function () { alert("Speichern fehlgeschlagen"); });
    });

    /* ── Geoman: Linien/Flaechen ── */
    if (karte.pm) {
      var geomanOptionen = {
        position: "topleft",
        drawMarker: false,
        drawCircleMarker: false,
        drawCircle: false,
        drawText: false,
        drawPolyline: true,
        drawRectangle: true,
        drawPolygon: true,
        editMode: true,
        dragMode: false,
        cutPolygon: false,
        removalMode: false,
        rotateMode: true
      };
      karte.pm.addControls(geomanOptionen);
      karte.on("pm:create", function (ev) {
        var gj = ev.layer.toGeoJSON();
        var label = "";
        var labelFeld = document.getElementById("oks-label");
        if (labelFeld) { label = labelFeld.value.trim(); }
        apiPost("/objekte", { typ: "geometrie", geometry: gj.geometry, label: label })
          .then(function (e) {
            karte.removeLayer(ev.layer);
            eintragAnzeigen(e);
            if (labelFeld) { labelFeld.value = ""; }
          })
          .catch(function () {
            karte.removeLayer(ev.layer);
            alert("Speichern fehlgeschlagen");
          });
      });
    }

    return karte;
  };
})();
