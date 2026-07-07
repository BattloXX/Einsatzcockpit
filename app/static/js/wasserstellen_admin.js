/* Admin-Übersichtskarte der Wasserstellen-Stammdaten (taktisches Dark-Layout).
 * - Zeigt alle Entnahmestellen als farbige, leuchtende Marker je Icon-Kategorie.
 * - Klick auf die Karte übernimmt lat/lng ins Formular "Neue Wasserstelle".
 * Die Kachel-Abdunklung passiert per CSS (.wsx-mapwrap .leaflet-tile-pane).
 */
(function () {
  "use strict";

  var el = document.getElementById("ws-map");
  if (!el || typeof L === "undefined") { return; }

  var COLOR = { ueberflur: "#4d8eff", unterflur: "#ffb95f", loeschwasser: "#5ad1c0" };
  var TEXT = { ueberflur: "H", unterflur: "UH", loeschwasser: "≈" };

  var karte = L.map(el, { zoomControl: true }).setView([47.4652, 9.7503], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap"
  }).addTo(karte);
  setTimeout(function () { karte.invalidateSize(); }, 200);
  window.addEventListener("resize", function () { karte.invalidateSize(); });

  // status: 'bereit' | 'wartung' | 'defekt' (Fallback: bereit)
  function icon(kat, status) {
    var base = COLOR[kat] || COLOR.loeschwasser;
    var style;
    if (status === "defekt") {
      style = "background:#8c909f;opacity:.55;border-style:dashed;";
    } else if (status === "wartung") {
      style = "background:" + base + ";border-color:#ffb95f;box-shadow:0 0 9px #ffb95f;";
    } else {
      style = "background:" + base + ";box-shadow:0 0 9px " + base + ";";
    }
    return L.divIcon({
      html: '<div class="wsx-marker" style="' + style + '">' + (TEXT[kat] || "≈") + "</div>",
      className: "wsx-marker-wrap", iconSize: null, iconAnchor: [9, 9]
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  var bounds = [];
  fetch("/admin/wasserstellen.json")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d) { return; }
      (d.wasserstellen || []).forEach(function (w) {
        if (w.lat == null || w.lng == null) { return; }
        var m = L.marker([w.lat, w.lng], { icon: icon(w.icon_kat, w.status) }).addTo(karte);
        var flow = (w.ergiebigkeit_l_min != null)
          ? '<br><span style="opacity:.7;">' + w.ergiebigkeit_l_min + " l/min</span>" : "";
        var st = (w.status && w.status !== "bereit")
          ? '<br><em>' + escapeHtml(w.status_label || w.status) + "</em>" : "";
        m.bindPopup("<strong>" + escapeHtml(w.bezeichnung) + "</strong><br>"
          + escapeHtml(w.typ_label) + flow + st);
        bounds.push([w.lat, w.lng]);
      });
      if (bounds.length) { karte.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 }); }
    })
    .catch(function () {});

  // Klick auf die Karte → Koordinaten ins Neu-Formular + temporärer Marker
  var tempMarker = null;
  karte.on("click", function (ev) {
    var lat = Math.round(ev.latlng.lat * 1e6) / 1e6;
    var lng = Math.round(ev.latlng.lng * 1e6) / 1e6;
    setVal("ws-neu-lat", lat);
    setVal("ws-neu-lng", lng);
    if (tempMarker) { karte.removeLayer(tempMarker); }
    tempMarker = L.marker([lat, lng], { icon: icon("ueberflur", "bereit") }).addTo(karte)
      .bindPopup("Neue Wasserstelle hier").openPopup();
  });

  function setVal(id, val) {
    var inp = document.getElementById(id);
    if (inp) {
      inp.value = val;
      inp.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }
})();
