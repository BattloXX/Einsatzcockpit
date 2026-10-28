(function () {
  "use strict";

  function planNormiert(latlng, breite, hoehe) {
    return { x: latlng.lng / breite, y: -latlng.lat / hoehe };
  }
  function affineMatrix(pxTL, pxTR, pxBL, breite, hoehe) {
    return { a: (pxTR.x - pxTL.x) / breite, b: (pxTR.y - pxTL.y) / breite,
      c: (pxBL.x - pxTL.x) / hoehe, d: (pxBL.y - pxTL.y) / hoehe, e: pxTL.x, f: pxTL.y };
  }
  function naechsterSchritt(paare, pending) {
    var n = paare.length;
    if (pending) { return { text: "Ecke " + (n + 1) + " von " + Math.max(4, n + 1) + " · jetzt auf der Karte klicken", aktiv: "karte" }; }
    if (n >= 4) { return { text: "Vier Ecken erfasst · jetzt Prüfen", aktiv: "" }; }
    return { text: "Ecke " + (n + 1) + " von 4 · jetzt im Plan klicken", aktiv: "plan" };
  }
  function affineOverlay(url, ecken, groesse, deckkraft) {
    var Overlay = L.Layer.extend({
      onAdd: function (map) {
        this.bild = document.createElement("img");
        this.bild.src = url; this.bild.alt = "";
        Object.assign(this.bild.style, { position: "absolute", width: groesse[0] + "px",
          height: groesse[1] + "px", maxWidth: "none", transformOrigin: "0 0", pointerEvents: "none", opacity: String(deckkraft) });
        map.getPane("overlayPane").appendChild(this.bild);
        map.on("zoom move viewreset", this.zeichnen, this);
        this.zeichnen();
      },
      zeichnen: function () {
        var map = this._map;
        function px(e) { return map.latLngToLayerPoint([e.lat, e.lng]); }
        var m = affineMatrix(px(ecken[0]), px(ecken[1]), px(ecken[3]), groesse[0], groesse[1]);
        this.bild.style.transform = "matrix(" + [m.a, m.b, m.c, m.d, m.e, m.f].join(",") + ")";
      },
      onRemove: function (map) {
        map.off("zoom move viewreset", this.zeichnen, this); this.bild.remove();
      }
    });
    return new Overlay();
  }
  function open(opts) {
    var seitenModus = opts.modus === "seite" || !!opts.container;
    var dialog = seitenModus ? opts.container : document.createElement("dialog");
    dialog.className = seitenModus ? "ogx" : "ogx oks-georef";
    dialog.setAttribute("aria-label", "Georeferenz der Planseite bearbeiten");
    if (!seitenModus) { (opts.root || document.body).appendChild(dialog); }
    function anfrage(pfad, daten) {
      var options = { headers: { "X-CSRF-Token": opts.csrfToken || "" } };
      if (daten !== undefined) {
        options.method = "POST"; options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(daten);
      }
      return fetch(opts.basisUrl + pfad, options).then(function (r) {
        return r.text().then(function (text) {
          var d;
          try { d = JSON.parse(text); } catch (_) { d = { detail: text }; }
          if (!r.ok) {
            var detail = d.detail || d;
            var meldung = typeof detail === "string" ? detail : Array.isArray(detail) ?
              detail.map(function (fehler) { return fehler.msg || fehler.meldung || "Ungültige Eingabe"; }).join(" · ") :
              detail.meldung || detail.message;
            throw new Error(meldung || "Anfrage fehlgeschlagen (" + r.status + ").");
          }
          return d;
        });
      });
    }
    var get = opts.get || function (pfad) { return anfrage(pfad); };
    var post = opts.post || anfrage;
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
    el("h3", "Georeferenz · Seite " + opts.seite, dialog);
    var status = el("p", "Plan wird geladen ...", dialog);
    status.setAttribute("role", "status");
    var hinweise = el("ul", null, dialog);
    hinweise.setAttribute("role", "status");
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
    anweisung.setAttribute("aria-live", "polite");
    var layout = el("div", null, dialog, "ogx__layout");
    var planDiv = el("div", null, layout, "ogx__karte");
    planDiv.setAttribute("aria-label", "Planseite mit Zoom und Verschieben");
    var mapDiv = el("div", null, layout, "ogx__karte");
    mapDiv.setAttribute("aria-label", "Karte mit Referenzpunkten");
    var liste = el("div", null, dialog);
    var aktionen = el("div", null, dialog, "ogx__aktionen");
    var paare = [], pending = null, plan, map, meta, revision = 0, generation = 0, offen = true;
    var marker = [], vorgemerkt = null, saving = false, hatGeoref = false;
    var vorschau = null, ecken = null, erstmals = true;
    var overlayLabel = el("label", "Plan über Karte legen ", dialog);
    var overlayAn = el("input", null, overlayLabel); overlayAn.type = "checkbox";
    var deckLabel = el("label", "Deckkraft ", dialog);
    var deckkraft = el("input", null, deckLabel); deckkraft.type = "range";
    deckkraft.min = "0"; deckkraft.max = "100"; deckkraft.value = "50";
    overlayAn.onchange = zeigeVorschau; deckkraft.oninput = zeigeVorschau;
    button("Prüfen", pruefen);
    var weitere = button("Weitere Punkte hinzufügen", function () { zusatz = true; schritt(); });
    var zusatz = false;
    var zurueck = button("Letztes Paar entfernen", letztesEntfernen);
    document.addEventListener("keydown", tastatur);
    function abbrechen() {
      if (saving || !pending) { return; }
      pending = null;
      if (vorgemerkt) { vorgemerkt.remove(); vorgemerkt = null; }
      pruefen();
    }
    function letztesEntfernen() {
      if (saving || !paare.length) { return; }
      paare.pop(); pruefen();
    }
    function tastatur(ev) {
      if (ev.key === "Escape" && pending) { ev.preventDefault(); abbrechen(); }
      var ziel = ev.target;
      if (ziel && (/^(INPUT|SELECT|TEXTAREA)$/i.test(ziel.tagName) || ziel.isContentEditable)) { return; }
      if (ev.key === "Backspace" && paare.length) { ev.preventDefault(); letztesEntfernen(); }
    }
    function schritt() {
      var s = naechsterSchritt(paare, pending);
      anweisung.textContent = s.text;
      var aktiv = s.aktiv || (zusatz ? "plan" : "");
      if (zusatz && !pending && paare.length >= 4) { anweisung.textContent = "Weiteren Punkt im Plan wählen."; }
      planDiv.className = "ogx__karte" + (aktiv === "plan" ? " ogx__aktiv" : "");
      mapDiv.className = "ogx__karte" + (aktiv === "karte" ? " ogx__aktiv" : "");
      weitere.hidden = paare.length < 4 || !!pending;
      zurueck.disabled = saving || !paare.length;
    }
    function entferneVorschau() { if (vorschau) { vorschau.remove(); vorschau = null; } }
    function zeigeVorschau() {
      entferneVorschau();
      if (ecken && overlayAn.checked) {
        vorschau = affineOverlay(meta.seitenbild_url, ecken, meta.bildgroesse, Number(deckkraft.value) / 100).addTo(map);
      }
    }
    var speichern = button("Speichern", function () {
      if (saving || speichern.disabled || !meta) { return; }
      speichern.disabled = true; saving = true; generation++;
      modell.disabled = true; verwerfen.disabled = true;
      post("/georef", payload(false)).then(function (antwort) {
        revision = antwort.georeferenz ? antwort.georeferenz.revision : revision + 1;
        hatGeoref = true;
        status.textContent = "Georeferenz gespeichert. Vorschläge wurden neu berechnet.";
        return opts.onGespeichert ? opts.onGespeichert() : null;
      }).then(function () {
        if (seitenModus) {
          status.textContent = "Georeferenz gespeichert";
          var zurueckLink = el("a", " · Zurück zur Dokumentenseite", status);
          zurueckLink.href = opts.zurueckUrl || opts.basisUrl.replace(/\/karte$/, "");
        } else { dialog.close(); }
      }).catch(fehler).finally(function () { saving = false; modell.disabled = false; verwerfen.disabled = !hatGeoref; schritt(); });
    });
    speichern.disabled = true;
    var verwerfen = button("Georeferenz verwerfen", function () {
      if (saving || !meta) { return; }
      speichern.disabled = true; saving = true; generation++;
      modell.disabled = true; verwerfen.disabled = true;
      post("/georef/verwerfen", payload(false)).then(function (antwort) {
        if (antwort.georeferenz) { revision = antwort.georeferenz.revision; }
        hatGeoref = false; ecken = null; entferneVorschau(); hinweise.replaceChildren();
        return opts.onGespeichert ? opts.onGespeichert() : null;
      }).then(function () {
        if (seitenModus) { paare = []; pending = null; if (vorgemerkt) { vorgemerkt.remove(); vorgemerkt = null; } verwerfen.disabled = true; render(); status.textContent = "Georeferenz verworfen"; } else { dialog.close(); }
      }).catch(fehler).finally(function () { saving = false; modell.disabled = false; verwerfen.disabled = !hatGeoref; schritt(); });
    });
    verwerfen.disabled = true;
    if (!seitenModus) { button("Schließen", schliessen); }
    function aufraeumen() {
      offen = false; generation++;
      document.removeEventListener("keydown", tastatur);
      if (plan) { plan.remove(); } if (map) { map.remove(); }
      if (!seitenModus) { dialog.remove(); }
      if (opts.onSchliessen) { opts.onSchliessen(); }
    }
    function schliessen() {
      if (saving) { return; }
      if (seitenModus) { aufraeumen(); dialog.replaceChildren(); } else { dialog.close(); }
    }
    if (!seitenModus) {
      dialog.addEventListener("close", aufraeumen);
      dialog.addEventListener("cancel", function (ev) {
        if (saving || pending) { ev.preventDefault(); abbrechen(); }
      });
    }
    function fehler(err) { if (offen) { status.textContent = err.message; speichern.disabled = true; } }
    function payload(pruefen) {
      return { dokument_id: opts.dokumentId, seite: opts.seite, punkte: paare,
        modell: modell.value, pruefen: pruefen, expected_revision: revision, expected_rotation: meta.rotation };
    }
    function guete(node, wert, grenze) {
      node.className = wert != null && wert <= grenze ? "ogx__gut" : "ogx__fehler";
    }
    function pruefen() {
      if (saving) { return; }
      if (!meta) { return; }
      ecken = null; entferneVorschau(); hinweise.replaceChildren();
      render();
      var lauf = ++generation;
      speichern.disabled = true;
      status.textContent = "Referenzpunkte werden geprüft ...";
      post("/georef", payload(true)).then(function (antwort) {
        if (!offen || lauf !== generation) { return; }
        var g = antwort.georeferenz;
        (g.hinweise || []).forEach(function (h) { el("li", h.text, hinweise); });
        if (g.status === "gueltig" && g.vorschau_ecken && g.vorschau_ecken.length === 4) {
          ecken = g.vorschau_ecken;
          if (erstmals) { overlayAn.checked = true; erstmals = false; }
          zeigeVorschau();
        }
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
      return L.divIcon({ html: span, className: "ogx__nummer", iconSize: [24, 24] });
    }
    function render() {
      schritt();
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
      zusatz = false; schritt();
      pruefen();
    }
    var ortho;
    schritt();
    if (!seitenModus) { dialog.showModal(); }
    get("/georef.json?dokument_id=" + opts.dokumentId + "&seite=" + opts.seite).then(function (daten) {
      if (!offen) { return; }
      meta = daten;
      var g = daten.georeferenz;
      hatGeoref = !!g;
      revision = g ? g.revision : 0;
      modell.value = g ? g.modell : "aehnlichkeit";
      // Veraltete Koordinaten duerfen nach einer Drehung nicht wieder gespeichert werden.
      paare = g && g.status !== "veraltet" ? g.referenzpunkte.map(function (p, i) { return Object.assign({}, p, { id: i + 1 }); }) : [];
      quelleStatus.textContent = g ? "Gespeicherter Satz: " + g.status +
        (g.ablehngrund ? " · " + g.ablehngrund : "") : "Noch keine Georeferenz.";
      status.textContent = "Punktpaare erfassen und prüfen.";
      if (g && g.status === "veraltet") {
        quelleStatus.textContent += " · Bildkoordinaten neu erfassen.";
        quelleStatus.className = "ogx__fehler";
      }
      plan = L.map(planDiv, { crs: L.CRS.Simple, minZoom: -5 });
      var bounds = [[-meta.bildgroesse[1], 0], [0, meta.bildgroesse[0]]];
      L.imageOverlay(meta.seitenbild_url, bounds).addTo(plan); plan.fitBounds(bounds);
      map = L.map(mapDiv).setView(opts.startCenter || [47.5, 14.5], opts.startZoom || 18);
      var osm = window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
      ortho = L.tileLayer("https://mapsneu.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg",
        { maxZoom: 20, attribution: "Datenquelle: basemap.at" });
      if (osm) { osm.remove(); }
      ortho.addTo(map);
      L.control.layers({ OpenStreetMap: osm, Orthofoto: ortho }).addTo(map);
      plan.on("click", function (ev) {
        if (saving) { return; }
        var xy = planNormiert(ev.latlng, meta.bildgroesse[0], meta.bildgroesse[1]);
        if (xy.x < 0 || xy.x > 1 || xy.y < 0 || xy.y > 1) { return; }
        pending = xy; generation++; speichern.disabled = true;
        ecken = null; entferneVorschau(); hinweise.replaceChildren();
        if (vorgemerkt) { vorgemerkt.remove(); }
        vorgemerkt = L.circleMarker(ev.latlng, { radius: 7 }).addTo(plan);
        schritt();
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
    return { schliessen: schliessen };
  }
  window.objektGeorefEditor = { open: open, planNormiert: planNormiert, affineMatrix: affineMatrix, naechsterSchritt: naechsterSchritt };
  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("DOMContentLoaded", function () {
      var container = document.getElementById("georef-editor");
      if (!container) { return; }
      var d = container.dataset;
      open({ modus: "seite", container: container, basisUrl: d.basisUrl, dokumentId: Number(d.dokumentId),
        seite: Number(d.seite), csrfToken: d.csrf, zurueckUrl: d.zurueckUrl,
        startCenter: d.lat && d.lng ? [Number(d.lat), Number(d.lng)] : null });
    });
  }
})();
