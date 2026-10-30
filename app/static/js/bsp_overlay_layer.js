/* Gemeinsame BSP-Ebene: Einsatzvorgaben und persoenliche Anzeige getrennt halten. */
(function () {
  "use strict";

  function aufloesen(overlay, lokal) {
    var sichtbar = overlay.sichtbarkeit === "ON" ||
      (overlay.sichtbarkeit === "INHERIT" && overlay.standard_sichtbar);
    var deckkraft = overlay.deckkraft == null ? overlay.standard_deckkraft : overlay.deckkraft;
    return {
      sichtbar: lokal && typeof lokal.sichtbar === "boolean" ? lokal.sichtbar : !!sichtbar,
      deckkraft: lokal && typeof lokal.deckkraft === "number" ? lokal.deckkraft : deckkraft,
      lokal: !!lokal
    };
  }

  function bildLayer(karte, overlay, deckkraft) {
    var pane = karte.getPane("bsp") || karte.createPane("bsp");
    pane.style.zIndex = 250;
    pane.style.pointerEvents = "none";
    return L.imageOverlay(overlay.raster_url, overlay.bounds, {
      pane: "bsp", opacity: deckkraft, interactive: false, pmIgnore: true
    });
  }

  function init(karte, opts) {
    var overlays = [], layers = {}, lokaleWerte = {}, fehlgeschlagen = {};
    var auswahl = null, folge = 0, speichern = false;
    var scope = opts.kannEinstellen ? "alle" : "lokal";
    var root = document.getElementById("lft-bsp");
    function el(id) { return document.getElementById(id); }
    function hinweis(text) { if (el("lft-bsp-hinweis")) { el("lft-bsp-hinweis").textContent = text; } }
    function key(id) { return "ecp.bsp:" + opts.listUrl + ":" + id; }
    function lokal(id) {
      if (!Object.prototype.hasOwnProperty.call(lokaleWerte, id)) {
        try {
          var wert = JSON.parse(window.localStorage.getItem(key(id)) || "null");
          lokaleWerte[id] = wert && typeof wert.sichtbar === "boolean" &&
            typeof wert.deckkraft === "number" && wert.deckkraft >= 0.1 && wert.deckkraft <= 1 ? wert : null;
        } catch (e) { lokaleWerte[id] = null; }
      }
      return lokaleWerte[id];
    }
    function lokalSpeichern(id, wert) {
      lokaleWerte[id] = wert;
      try {
        if (wert) { window.localStorage.setItem(key(id), JSON.stringify(wert)); }
        else { window.localStorage.removeItem(key(id)); }
      } catch (e) { hinweis("Persönliche Einstellung gilt nur bis zum Schließen dieser Karte."); }
    }
    function gewaehlt() { return overlays.find(function (o) { return o.id === auswahl; }); }
    function entfernen(id) {
      if (layers[id]) { karte.removeLayer(layers[id].layer); delete layers[id]; }
    }
    function render() {
      Object.keys(layers).forEach(function (id) {
        if (!overlays.some(function (o) { return String(o.id) === id; })) { entfernen(id); }
      });
      overlays.forEach(function (o) {
        var zustand = aufloesen(o, lokal(o.id));
        if (!zustand.sichtbar || (layers[o.id] && layers[o.id].url !== o.raster_url)) { entfernen(o.id); }
        if (!zustand.sichtbar || fehlgeschlagen[o.id] === o.raster_url) { return; }
        if (!layers[o.id]) {
          var layer = bildLayer(karte, o, zustand.deckkraft);
          layers[o.id] = {layer: layer, url: o.raster_url};
          layer.on("error", function () {
            if (!layers[o.id] || layers[o.id].layer !== layer) { return; }
            fehlgeschlagen[o.id] = o.raster_url;
            entfernen(o.id);
            hinweis("Ein Brandschutzplan ist derzeit nicht verfügbar.");
          });
          layer.addTo(karte);
        } else { layers[o.id].layer.setOpacity(zustand.deckkraft); }
      });
      if (root) { root.hidden = !overlays.length; }
      var o = gewaehlt();
      if (!o || !root) { return; }
      if (opts.nurLokal || !o.kann_einstellen) { scope = "lokal"; }
      if (!opts.nurLokal) {
        el("lft-bsp-scope").value = scope;
        el("lft-bsp-alle").disabled = !o.kann_einstellen;
      }
      var zustand = aufloesen(o, scope === "lokal" ? lokal(o.id) : null);
      el("lft-layer-bsp").checked = zustand.sichtbar;
      el("lft-bsp-deckkraft").value = zustand.deckkraft;
      el("lft-bsp-prozent").textContent = Math.round(zustand.deckkraft * 100) + " %";
      el("lft-bsp-lokal").textContent = lokal(o.id) ? "Persönliche Überschreibung aktiv (nur für mich)." : "Einsatzvorgabe aktiv.";
      el("lft-layer-bsp").disabled = speichern;
      el("lft-bsp-deckkraft").disabled = speichern;
      el("lft-bsp-reset").disabled = speichern;
    }
    function listeRendern() {
      if (!root) { return; }
      var select = el("lft-bsp-auswahl");
      select.textContent = "";
      overlays.forEach(function (o) {
        var option = document.createElement("option");
        option.value = o.id;
        option.textContent = [o.objekt_name, o.name, o.geschoss, o.bereich,
          "Stand " + o.freigabe_nr + (o.freigegeben_am ? " / " + new Date(o.freigegeben_am).toLocaleString("de-AT") : "")
        ].filter(Boolean).join(" · ");
        select.appendChild(option);
      });
      select.value = auswahl;
    }
    function reload() {
      var nr = ++folge;
      return fetch(opts.listUrl, {credentials: "same-origin", cache: "no-store"}).then(function (r) {
        if (!r.ok) { throw new Error("Liste nicht verfügbar"); }
        return r.json();
      }).then(function (liste) {
        if (nr !== folge) { return; }
        overlays = liste;
        if (!gewaehlt()) { auswahl = overlays.length ? overlays[0].id : null; }
        listeRendern(); render();
      }).catch(function () {
        if (nr !== folge) { return; }
        Object.keys(layers).forEach(entfernen);
        hinweis("Brandschutzpläne konnten nicht aktualisiert werden.");
      });
    }
    function aendern(reset) {
      var o = gewaehlt();
      if (!o || speichern) { return; }
      var wert = {sichtbar: el("lft-layer-bsp").checked, deckkraft: Number(el("lft-bsp-deckkraft").value)};
      hinweis("");
      if (scope === "lokal") {
        lokalSpeichern(o.id, reset ? null : wert); render(); return;
      }
      if (!o.kann_einstellen) { return; }
      speichern = true; render();
      fetch(opts.postUrlTemplate.replace("{overlay_id}", o.id), {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": opts.csrfToken},
        body: JSON.stringify({sichtbarkeit: reset ? "INHERIT" : (wert.sichtbar ? "ON" : "OFF"),
          deckkraft: reset ? null : wert.deckkraft})
      }).then(function (r) {
        if (!r.ok) { throw new Error("Speichern fehlgeschlagen"); }
        return reload();
      }).catch(function () { hinweis("Einsatzvorgabe konnte nicht gespeichert werden."); })
        .finally(function () { speichern = false; render(); });
    }
    if (root) {
      el("lft-bsp-auswahl").addEventListener("change", function () { auswahl = Number(this.value); render(); });
      if (!opts.nurLokal) {
        el("lft-bsp-scope").addEventListener("change", function () { scope = this.value; render(); });
      }
      el("lft-layer-bsp").addEventListener("change", function () { aendern(false); });
      el("lft-bsp-deckkraft").addEventListener("input", function () {
        el("lft-bsp-prozent").textContent = Math.round(Number(this.value) * 100) + " %";
      });
      el("lft-bsp-deckkraft").addEventListener("change", function () { aendern(false); });
      el("lft-bsp-reset").addEventListener("click", function () { aendern(true); });
      el("lft-bsp-aus").addEventListener("click", function () {
        hinweis(""); scope = "lokal";
        overlays.forEach(function (o) {
          lokalSpeichern(o.id, {sichtbar: false, deckkraft: aufloesen(o, lokal(o.id)).deckkraft});
        });
        render();
      });
    }
    reload();
    return {
      reload: reload,
      handleEvent: function (data) {
        if (["lagefuehrung.bsp.changed", "objektgefahren", "objekt_einsatz.changed", "objekt_einsatz"].indexOf(data.type) !== -1) {
          return reload();
        }
      }
    };
  }
  window.EcpBspOverlay = {init: init, aufloesen: aufloesen, bildLayer: bildLayer};
})();
