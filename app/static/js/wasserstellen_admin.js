/* Wasserstellen-Admin: interaktive Karte + Registry-Tabelle.
 *
 * Karte (Standard-OSM-Look wie alle anderen Karten der App):
 *   - Marker je Icon-Kategorie (rot Überflur / blau Unterflur / teal Löschwasser),
 *     Status-Modifikator für Wartung (gelber Ring) und Defekt (gestrichelt/blass).
 *   - Klick auf Marker → Popup mit "Bearbeiten"/"Löschen".
 *   - Klick auf freie Karte → ziehbarer Pin + Modal "Neue Wasserstelle" (Koordinaten vorbefüllt).
 *   - Klick auf Koordinaten-Button in der Tabelle → Karte fokussiert den Marker.
 *
 * Tabelle: Client-seitige Suche/Filter + sortierbare Spalten.
 */
(function () {
  "use strict";

  // Leaflet-Marker-Icons auf lokalen Pfad umlenken (wie map-picker.js)
  if (typeof L !== "undefined") {
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
      iconUrl: "/static/img/leaflet/marker-icon.png",
      iconRetinaUrl: "/static/img/leaflet/marker-icon-2x.png",
      shadowUrl: "/static/img/leaflet/marker-shadow.png",
    });
  }

  var GLYPH = { ueberflur: "H", unterflur: "U", loeschwasser: "≈" };

  var WS = {
    map: null,
    byId: {},        // id -> Datensatz
    markerById: {},  // id -> Leaflet-Marker
    newPin: null,    // ziehbarer Pin fuer "Neue Wasserstelle"
  };
  window.WS = WS;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function markerIcon(kat, status) {
    var cls = "hydrant-icon hydrant-icon--" + (kat || "loeschwasser");
    if (status === "wartung") { cls += " hydrant-icon--st-wartung"; }
    else if (status === "defekt") { cls += " hydrant-icon--st-defekt"; }
    return L.divIcon({
      html: '<span class="' + cls + '">' + (GLYPH[kat] || "≈") + "</span>",
      className: "hydrant-divicon", iconSize: null, iconAnchor: [11, 11],
    });
  }

  function popupHtml(w) {
    var flow = (w.ergiebigkeit_l_min != null)
      ? '<div style="opacity:.75;">' + esc(w.ergiebigkeit_l_min) + " l/min</div>" : "";
    var st = (w.status && w.status !== "bereit")
      ? '<div><em>' + esc(w.status_label || w.status) + "</em></div>" : "";
    var hint = w.hinweis ? '<div style="opacity:.75;">' + esc(w.hinweis) + "</div>" : "";
    return '<div style="min-width:150px;">'
      + '<strong>' + esc(w.bezeichnung) + "</strong>"
      + '<div style="opacity:.75;">' + esc(w.typ_label) + "</div>"
      + flow + hint + st
      + '<div style="display:flex;gap:6px;margin-top:8px;">'
      + '<button type="button" class="btn btn--secondary btn--xs" onclick="wsOpenEdit(' + w.id + ')">Bearbeiten</button>'
      + '<button type="button" class="btn btn--ghost btn--xs" onclick="wsDelete(' + w.id + ')">Löschen</button>'
      + "</div></div>";
  }

  // ── Karte aufbauen ─────────────────────────────────────────────────────────
  var el = document.getElementById("ws-map");
  if (el && typeof L !== "undefined") {
    var karte = L.map(el, { zoomControl: true }).setView([47.4652, 9.7503], 13);
    WS.map = karte;
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende',
      subdomains: "abc", maxZoom: 19,
    }).addTo(karte);
    setTimeout(function () { karte.invalidateSize(); }, 150);
    setTimeout(function () { karte.invalidateSize(); }, 600);
    window.addEventListener("resize", function () { karte.invalidateSize(); });

    var bounds = [];
    fetch("/admin/wasserstellen.json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { return; }
        (d.wasserstellen || []).forEach(function (w) {
          WS.byId[w.id] = w;
          if (w.lat == null || w.lng == null) { return; }
          var m = L.marker([w.lat, w.lng], { icon: markerIcon(w.icon_kat, w.status) }).addTo(karte);
          m.bindPopup(popupHtml(w));
          WS.markerById[w.id] = m;
          bounds.push([w.lat, w.lng]);
        });
        if (bounds.length) { karte.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 }); }
      })
      .catch(function () {});

    // Klick auf freie Karte → Pin setzen + Neu-Modal
    karte.on("click", function (ev) {
      var lat = Math.round(ev.latlng.lat * 1e6) / 1e6;
      var lng = Math.round(ev.latlng.lng * 1e6) / 1e6;
      wsBeginNew(lat, lng);
    });
  }

  // ── Pin für neue Wasserstelle setzen / verschieben ──────────────────────────
  function setNewPin(lat, lng) {
    if (!WS.map) { return; }
    if (WS.newPin) {
      WS.newPin.setLatLng([lat, lng]);
    } else {
      WS.newPin = L.marker([lat, lng], { draggable: true, className: "ws-newpin" }).addTo(WS.map);
      WS.newPin.on("drag move dragend", function () {
        var p = WS.newPin.getLatLng();
        setVal("ws-neu-lat", Math.round(p.lat * 1e6) / 1e6);
        setVal("ws-neu-lng", Math.round(p.lng * 1e6) / 1e6);
      });
    }
    WS.newPin.bindPopup("Neue Wasserstelle hier").openPopup();
  }
  function clearNewPin() {
    if (WS.newPin && WS.map) { WS.map.removeLayer(WS.newPin); }
    WS.newPin = null;
  }

  function setVal(id, val) {
    var inp = document.getElementById(id);
    if (inp) { inp.value = val; }
  }

  // ── Öffentliche Aktionen (Buttons/Popups) ──────────────────────────────────
  window.wsBeginNew = function (lat, lng) {
    var modal = document.getElementById("newModal");
    if (!modal) { return; }
    if (lat == null && WS.map) {
      var c = WS.map.getCenter();
      lat = Math.round(c.lat * 1e6) / 1e6; lng = Math.round(c.lng * 1e6) / 1e6;
    }
    setVal("ws-neu-lat", lat == null ? "" : lat);
    setVal("ws-neu-lng", lng == null ? "" : lng);
    if (lat != null) { setNewPin(lat, lng); }
    // Beim Schließen Pin entfernen (einmalig registrieren)
    if (!modal._wsClose) {
      modal._wsClose = true;
      modal.addEventListener("close", clearNewPin);
    }
    modal.showModal();
  };

  window.wsOpenEdit = function (id) {
    var w = WS.byId[id];
    var form = document.getElementById("editForm");
    if (!w || !form) { return; }
    form.action = "/admin/wasserstellen/" + id + "/bearbeiten";
    setVal("edit-bez", w.bezeichnung || "");
    setVal("edit-typ", w.typ || "");
    setVal("edit-status", w.status || "bereit");
    setVal("edit-lat", w.lat == null ? "" : w.lat);
    setVal("edit-lng", w.lng == null ? "" : w.lng);
    setVal("edit-flow", w.ergiebigkeit_l_min == null ? "" : w.ergiebigkeit_l_min);
    setVal("edit-hinweis", w.hinweis || "");
    var m = WS.markerById[id];
    if (m) { m.closePopup(); }
    document.getElementById("editModal").showModal();
  };

  window.wsDelete = function (id) {
    var w = WS.byId[id];
    var name = w ? w.bezeichnung : "";
    if (!confirm("Wasserstelle „" + name + "“ wirklich löschen?")) { return; }
    var form = document.createElement("form");
    form.method = "post";
    form.action = "/admin/wasserstellen/" + id + "/loeschen";
    var csrf = (document.cookie.match(/(?:^|;\s*)ec_csrf=([^;]+)/) || [])[1];
    if (csrf) {
      var c = document.createElement("input");
      c.type = "hidden"; c.name = "_csrf"; c.value = decodeURIComponent(csrf);
      form.appendChild(c);
    }
    document.body.appendChild(form);
    form.submit();
  };

  window.wsFocus = function (id) {
    var m = WS.markerById[id];
    if (m && WS.map) {
      WS.map.setView(m.getLatLng(), Math.max(WS.map.getZoom(), 16));
      m.openPopup();
      var mc = document.querySelector(".ws-map-card");
      if (mc) { mc.scrollIntoView({ behavior: "smooth", block: "center" }); }
    }
  };

  // ── Filter ──────────────────────────────────────────────────────────────────
  window.wsApplyFilter = function () {
    var q = (val("wsSearch") || "").trim().toLowerCase();
    var typ = val("wsFilterTyp");
    var status = val("wsFilterStatus");
    var rows = document.querySelectorAll("#wsTbody tr[data-id]");
    var visible = 0;
    rows.forEach(function (tr) {
      var okText = !q || (tr.dataset.search || "").toLowerCase().indexOf(q) >= 0;
      var okTyp = !typ || tr.dataset.typ === typ;
      var okStatus = !status || tr.dataset.status === status;
      var show = okText && okTyp && okStatus;
      tr.style.display = show ? "" : "none";
      if (show) { visible++; }
    });
    var c = document.getElementById("wsFilterCount");
    if (c) { c.textContent = visible + " sichtbar"; }
  };

  // ── Sortierung ────────────────────────────────────────────────────────────
  var sortState = { col: null, dir: "asc" };
  window.wsSort = function (col) {
    if (sortState.col === col) {
      sortState.dir = sortState.dir === "asc" ? "desc" : "asc";
    } else {
      sortState = { col: col, dir: "asc" };
    }
    var tbody = document.getElementById("wsTbody");
    if (!tbody) { return; }
    var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr[data-id]"));
    var th = document.querySelector('#wsTable th[data-sort="' + col + '"]');
    var numeric = th && th.dataset.num === "1";
    var key = col === "typ" ? "typlabel" : col;
    rows.sort(function (a, b) {
      var av = a.dataset[key] || "", bv = b.dataset[key] || "";
      if (numeric) {
        av = parseFloat(av) || 0; bv = parseFloat(bv) || 0;
      } else {
        av = av.toLowerCase(); bv = bv.toLowerCase();
      }
      if (av < bv) { return sortState.dir === "asc" ? -1 : 1; }
      if (av > bv) { return sortState.dir === "asc" ? 1 : -1; }
      return 0;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
    document.querySelectorAll("#wsTable th.sortable").forEach(function (h) {
      h.classList.remove("sort-asc", "sort-desc");
      if (h.dataset.sort === col) {
        h.classList.add(sortState.dir === "asc" ? "sort-asc" : "sort-desc");
      }
    });
  };

  // ── CSV-Export (client-seitig) ──────────────────────────────────────────────
  window.wsExportCsv = function () {
    fetch("/admin/wasserstellen.json")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var rows = d.wasserstellen || [];
        var cell = function (v) {
          v = (v == null ? "" : String(v));
          return /[;"\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
        };
        var csv = "bezeichnung;typ;lat;lng;ergiebigkeit_l_min;zustand;quelle\n";
        rows.forEach(function (w) {
          csv += [w.bezeichnung, w.typ_label, w.lat, w.lng, w.ergiebigkeit_l_min,
                  w.status_label || (w.aktiv ? "Bereit" : "Defekt"), w.quelle].map(cell).join(";") + "\n";
        });
        var blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "wasserstellen.csv";
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
      })
      .catch(function () {});
  };

  function val(id) { var e = document.getElementById(id); return e ? e.value : ""; }

  // Initialer Filter-Zähler
  document.addEventListener("DOMContentLoaded", function () {
    if (window.wsApplyFilter) { window.wsApplyFilter(); }
  });
})();
