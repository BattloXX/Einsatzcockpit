(function () {
  "use strict";
  var initial = document.getElementById("stats-infoscreen-data");
  if (!initial) return;
  var data = JSON.parse(initial.textContent);
  var frame = document.getElementById("stats-frame");
  var common = window.ECPStats;
  var charts = {};
  var activeMap = null, mapLayers = null, mapSignature = "";
  var number = new Intl.NumberFormat("de-AT", {maximumFractionDigits: 1});
  var nextRefresh = Date.now() + 300000, pending = false, revoked = false;
  var timezone = data.timezone;
  var currentView = "overview", rotation = true, interval = 30, nextView = Date.now() + 30000;
  var preferences = frame.dataset.preferences;
  var params = new URLSearchParams(window.location.search);
  var stored = {};
  try { stored = JSON.parse(localStorage.getItem(preferences) || "{}"); } catch (_) { /* Privatmodus */ }
  rotation = params.has("rotate") ? params.get("rotate") !== "0" : stored.rotation !== false;
  var configuredInterval = Number(params.get("interval") || stored.interval || 30);
  interval = Number.isFinite(configuredInterval) ? Math.max(10, Math.min(300, configuredInterval)) : 30;
  nextView = Date.now() + interval * 1000;

  function text(id, value) { document.getElementById(id).textContent = value; }
  function formatted(value) { return value == null ? "–" : number.format(value); }
  function savePreferences() {
    try { localStorage.setItem(preferences, JSON.stringify({rotation: rotation, interval: interval})); } catch (_) { /* Privatmodus */ }
  }
  function rotationLabel() {
    text("rotation-toggle", rotation ? "Wechsel · " + interval + " s" : "Wechsel pausiert");
    document.getElementById("rotation-toggle").setAttribute("aria-pressed", String(rotation));
  }
  function showView(view) {
    currentView = view;
    ["overview", "development"].forEach(function (name) {
      var section = document.getElementById("stats-" + name);
      section.classList.toggle("is-active", name === view);
      section.setAttribute("aria-hidden", String(name !== view));
      section.inert = name !== view;
      document.getElementById("view-" + name).setAttribute("aria-pressed", String(name === view));
    });
    nextView = Date.now() + interval * 1000;
    if (view === "overview" && activeMap) activeMap.invalidateSize({pan: false});
  }
  ["overview", "development"].forEach(function (view) {
    document.getElementById("view-" + view).addEventListener("click", function () { showView(view); });
  });
  document.getElementById("rotation-toggle").addEventListener("click", function () {
    rotation = !rotation;
    nextView = Date.now() + interval * 1000;
    savePreferences(); rotationLabel();
  });

  function scaleFrame() {
    var scale = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    frame.style.transform = "scale(" + scale + ")";
    frame.style.left = Math.max(0, (window.innerWidth - 1920 * scale) / 2) + "px";
    frame.style.top = Math.max(0, (window.innerHeight - 1080 * scale) / 2) + "px";
    Object.values(charts).forEach(function (chart) {
      chart.options.devicePixelRatio = Math.max(1, (window.devicePixelRatio || 1) * scale);
      chart.resize(chart.canvas.parentElement.clientWidth, chart.canvas.parentElement.clientHeight);
    });
  }
  window.addEventListener("resize", scaleFrame);

  function alarmRows(rows) {
    var sorted = rows.slice().sort(function (a, b) { return b.count - a.count || a.code.localeCompare(b.code); });
    var total = sorted.reduce(function (sum, row) { return sum + row.count; }, 0);
    var shown = [], further = {code: "Weitere", category: "", count: 0};
    sorted.forEach(function (row) {
      if (shown.length < 7 && (row.count / total >= .04 || shown.length < 3)) shown.push(row);
      else further.count += row.count;
    });
    if (further.count) shown.push(further);
    return shown.sort(function (a, b) { return b.count - a.count || a.code.localeCompare(b.code); });
  }

  var totalsPlugin = {
    id: "statsBarTotals",
    afterDatasetsDraw: function (chart) {
      var rows = chart.$rows || [];
      var horizontal = chart.options.indexAxis === "y";
      var ctx = chart.ctx;
      ctx.save(); ctx.font = "700 20px Hanken Grotesk, sans-serif"; ctx.textBaseline = "middle";
      rows.forEach(function (row, index) {
        var element = chart.getDatasetMeta(0).data[index];
        if (!element) return;
        ctx.fillStyle = row.current ? "#ffb3ac" : row.future ? "#94a3b8" : "#dae2fd";
        ctx.textAlign = horizontal ? "left" : "center";
        var label = row.count == null ? (row.future ? "offen" : "–") : formatted(row.count);
        var x = horizontal ? chart.scales.x.getPixelForValue(row.count || 0) + 10 : element.x;
        var y = horizontal ? element.y : chart.scales.y.getPixelForValue(row.count || 0) - 15;
        ctx.fillText(label, x, y);
      });
      ctx.restore();
    }
  };
  var currentMonthPlugin = {
    id: "statsCurrentMonth",
    beforeDraw: function (chart) {
      if (!chart.$rows || !chart.scales.x) return;
      var index = chart.$rows.findIndex(function (row) { return row.current; });
      if (index < 0) return;
      var x = chart.scales.x;
      var width = x.width / 12;
      chart.ctx.save(); chart.ctx.fillStyle = "rgba(173,198,255,.09)";
      chart.ctx.fillRect(x.getPixelForValue(index) - width / 2, chart.chartArea.top, width, chart.chartArea.height);
      chart.ctx.restore();
    }
  };
  function updateChart(id, config, rows) {
    if (!window.Chart) return;
    var chart = charts[id];
    if (chart) { chart.data = config.data; chart.$rows = rows; chart.update("none"); }
    else {
      // Chart.js misst transformierte Eltern über getBoundingClientRect. Die
      // logische Größe explizit setzen, damit 4K nicht doppelt skaliert wird.
      config.options = Object.assign({responsive: false, maintainAspectRatio: false, animation: false,
        devicePixelRatio: Math.max(1, (window.devicePixelRatio || 1) * Math.min(window.innerWidth / 1920, window.innerHeight / 1080))}, config.options);
      chart = charts[id] = new Chart(document.getElementById(id), config);
      chart.resize(chart.canvas.parentElement.clientWidth, chart.canvas.parentElement.clientHeight);
      chart.$rows = rows; chart.update("none");
    }
  }
  function barOptions(horizontal) {
    return {indexAxis: horizontal ? "y" : "x", layout: {padding: {top: 26, right: horizontal ? 55 : 12}},
      plugins: {legend: {display: false}, tooltip: {enabled: false}},
      scales: {
        x: {beginAtZero: true, stacked: !horizontal, grace: horizontal ? "15%" : undefined,
          grid: {display: horizontal, color: "#ffffff0d"}, border: {display: false},
          ticks: {font: {size: horizontal ? 16 : 20}, precision: 0, color: "#bcc7de", autoSkip: false, maxRotation: 0}},
        y: {beginAtZero: true, stacked: !horizontal, grace: horizontal ? undefined : "20%",
          grid: {display: !horizontal, color: "#ffffff0d"}, border: {display: false},
          ticks: {font: {size: horizontal ? 20 : 16}, precision: 0, color: "#bcc7de", autoSkip: false}}
      }};
  }
  function distributions(id, rows, sampleCount) {
    text(id + "-samples", formatted(sampleCount) + " Einsätze");
    document.getElementById(id + "-empty").hidden = rows.some(function (row) { return row.count != null; });
    updateChart("stats-" + id + "-chart", {type: "bar", plugins: [totalsPlugin],
      data: {labels: rows.map(function (row) { return row.label; }),
        datasets: [{data: rows.map(function (row) { return row.count; }), backgroundColor: "#adc6ff", borderRadius: 4, barPercentage: .65}]},
      options: barOptions(true)}, rows);
  }
  function renderCharts(value) {
    var rows = alarmRows(value.alarm_types);
    var colors = common.alarmColors(rows);
    updateChart("stats-category-chart", {type: "doughnut", plugins: [common.segmentCountPlugin],
      data: {labels: rows.map(function (row) { return row.code; }),
        datasets: [{data: rows.map(function (row) { return row.count; }), backgroundColor: colors, borderWidth: 3, borderColor: "#171f33"}]},
      options: {cutout: "48%", layout: {padding: 8}, plugins: {legend: {display: false}, tooltip: {enabled: false},
        statsSegmentCount: {fontSize: 22, minPercent: 8, autoContrast: true}}}}, rows);
    document.getElementById("alarm-empty").hidden = rows.length > 0;
    var legend = document.getElementById("alarm-legend");
    legend.replaceChildren();
    rows.forEach(function (row, index) {
      var item = document.createElement("div"); item.className = "alarm-row";
      var key = document.createElement("i"); key.className = "alarm-key"; key.style.background = colors[index];
      var label = document.createElement("span"); label.textContent = row.code;
      var count = document.createElement("strong"); count.textContent = formatted(row.count);
      item.append(key, label, count); legend.append(item);
    });
    var opts = barOptions(false);
    opts.scales.x.ticks.color = function (context) {
      var row = value.months[context.index]; return row && row.current ? "#ffb3ac" : row && row.future ? "#94a3b8" : "#bcc7de";
    };
    opts.scales.x.ticks.font = function (context) { return {size: 20, weight: value.months[context.index] && value.months[context.index].current ? "bold" : "normal"}; };
    updateChart("stats-month-chart", {type: "bar", plugins: [totalsPlugin, currentMonthPlugin],
      data: {labels: value.months.map(function (row) { return row.label; }), datasets: [
        {label: "Brand", data: value.months.map(function (row) { return row.fire; }), backgroundColor: "#ef4444"},
        {label: "Technisch", data: value.months.map(function (row) { return row.technical; }), backgroundColor: "#3b82f6"},
        {label: "Sonstige", data: value.months.map(function (row) { return row.other; }), backgroundColor: "#94a3b8"}
      ]}, options: opts}, value.months);
    distributions("time", value.time_of_day, value.kpis.total);
    distributions("weekday", value.weekdays, value.kpis.total);
    distributions("duration", value.durations, value.samples.duration);
  }

  function renderFleet(rows) {
    var list = document.getElementById("fleet-list");
    list.replaceChildren(); list.dataset.dense = String(rows.length > 9);
    if (!rows.length) { list.textContent = "Keine Fuhrparkdaten vorhanden"; return; }
    var maximum = Math.max.apply(null, rows.map(function (row) { return row.count; })) || 1;
    rows.forEach(function (row) {
      var item = document.createElement("div"); item.className = "fleet-row";
      var reading = document.createElement("div"); reading.className = "fleet-reading";
      var name = document.createElement("span"); name.textContent = row.code;
      var count = document.createElement("strong"); count.textContent = formatted(row.count);
      var bar = document.createElement("div"); bar.className = "fleet-bar";
      var fill = document.createElement("span"); fill.style.width = (row.count / maximum * 100) + "%";
      reading.append(name, count); bar.append(fill); item.append(reading, bar); list.append(item);
    });
  }
  function renderMap(value) {
    if (!window.L) return;
    text("map-count", formatted(value.map.length) + " Einsätze verortet");
    var signature = JSON.stringify([value.map_center, value.map]);
    if (signature === mapSignature) return;
    mapSignature = signature;
    if (!activeMap) {
      activeMap = L.map("stats-map", {zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false,
        scrollWheelZoom: false}).setView(value.map_center, 13);
      window.EinsatzcockpitMapConfig.addOsmTileLayer(activeMap);
      mapLayers = L.markerClusterGroup({animate: false, showCoverageOnHover: false, maxClusterRadius: 48,
        iconCreateFunction: function (cluster) {
          var children = cluster.getAllChildMarkers();
          var families = new Set(children.map(function (marker) { return marker.options.statsFamily; }));
          var name = families.size === 1 ? " stats-cluster--" + children[0].options.statsFamily : "";
          return L.divIcon({html: String(cluster.getChildCount()), className: "stats-cluster" + name, iconSize: [48, 48]});
        }}).addTo(activeMap);
    }
    mapLayers.clearLayers();
    var nearby = [];
    value.map.forEach(function (row) {
      var family = common.family(row.category);
      var color = family === "fire" ? "#ef4444" : family === "technical" ? "#3b82f6" : "#94a3b8";
      var point = L.latLng(row.lat, row.lng);
      if (point.distanceTo(value.map_center) <= 25000) nearby.push(point);
      var icon = L.divIcon({className: "stats-marker", html: "<span style='background:" + color + "'></span>", iconSize: [20, 20]});
      mapLayers.addLayer(L.marker(point, {icon: icon, statsFamily: family, interactive: false}));
    });
    // Nur Umgebungspunkte bestimmen den Ausschnitt. Ausreißer bleiben in der
    // Ebene verfügbar, ohne den Monitor überregional herauszuzoomen.
    if (nearby.length) activeMap.fitBounds(L.latLngBounds(nearby).extend(value.map_center), {padding: [35, 35], maxZoom: 14, animate: false});
    else activeMap.setView(value.map_center, 13, {animate: false});
    var outside = value.map.length - nearby.length;
    text("map-note", "Orte ca. 1 km gerundet" + (outside ? " · " + outside + " außerhalb der Umgebung" : "") +
      (value.kpis.total > value.map.length ? " · " + (value.kpis.total - value.map.length) + " ohne Ort" : ""));
  }
  function render(value) {
    timezone = value.timezone;
    document.querySelectorAll("[data-kpi]").forEach(function (element) { element.textContent = formatted(value.kpis[element.dataset.kpi]); });
    text("stats-period", "Jahr " + value.year + " · 01.01.–" + new Intl.DateTimeFormat("de-AT", {timeZone: timezone, day: "2-digit", month: "2-digit"}).format(new Date(value.generated_at)));
    text("note-total", "Laufendes Jahr"); text("note-fire", "Brandalarmierungen"); text("note-technical", "Technische Hilfeleistung");
    text("note-current_month", "Monat läuft noch");
    text("note-monthly_average", value.observed_months ? "Seit " + new Intl.DateTimeFormat("de-AT", {timeZone: timezone, month: "2-digit", year: "numeric"}).format(new Date(value.coverage_from + "T12:00:00Z")) + " · " + value.observed_months + " Monate*" : "Kein Datenzeitraum");
    text("note-duration", value.samples.duration ? "Basis: " + value.samples.duration + " Einsätze" : "Keine gültigen Endzeiten");
    text("note-arrival", value.samples.arrival ? "Median · " + value.samples.arrival + " Einsätze" : "Keine gültigen Zeitpaare");
    text("month-year", value.year);
    text("month-note", "Aktueller Monat läuft · künftige Monate offen · – vor erstem erfassten Einsatz. * Ø ab erstem Einsatzmonat, inkl. laufendem Monat.");
    text("last-update", "Datenstand " + new Intl.DateTimeFormat("de-AT", {timeZone: timezone, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"}).format(new Date(value.generated_at)));
    renderFleet(value.fleet); renderCharts(value); renderMap(value);
  }
  function validData(value) {
    function count(n) { return Number.isFinite(n) && n >= 0; }
    function optionalCount(n) { return n === null || count(n); }
    function series(rows, length) {
      return Array.isArray(rows) && rows.length === length && rows.every(function (row) {
        return row && typeof row.label === "string" && optionalCount(row.count);
      });
    }
    if (!value || !value.kpis || !value.samples || !Number.isFinite(Date.parse(value.generated_at)) ||
        !Number.isInteger(value.year) || !count(value.observed_months) ||
        (value.observed_months && !Number.isFinite(Date.parse(value.coverage_from))) ||
        !["total", "fire", "technical"].every(function (key) { return count(value.kpis[key]); }) ||
        !["current_month", "monthly_average", "duration", "arrival"].every(function (key) { return optionalCount(value.kpis[key]); }) ||
        !count(value.samples.duration) || !count(value.samples.arrival) || !series(value.months, 12) ||
        !series(value.time_of_day, 4) || !series(value.weekdays, 7) || !series(value.durations, 4) ||
        !value.months.every(function (row) { return ["fire", "technical", "other"].every(function (key) { return optionalCount(row[key]); }); }) ||
        !Array.isArray(value.fleet) || !value.fleet.every(function (row) { return row && typeof row.code === "string" && count(row.count); }) ||
        !Array.isArray(value.alarm_types) || !value.alarm_types.every(function (row) { return row && typeof row.code === "string" && count(row.count); }) ||
        !Array.isArray(value.map_center) || value.map_center.length !== 2 || !value.map_center.every(Number.isFinite) ||
        !Array.isArray(value.map) || !value.map.every(function (row) { return row && Number.isFinite(row.lat) && Number.isFinite(row.lng); })) return false;
    try { new Intl.DateTimeFormat("de-AT", {timeZone: value.timezone}); } catch (_) { return false; }
    return true;
  }
  async function refresh() {
    if (pending || revoked) return;
    pending = true;
    text("refresh-label", "Daten werden aktualisiert …");
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 20000);
    try {
      var response = await fetch(window.location.pathname.replace(/\/$/, "") + "/daten", {cache: "no-store", credentials: "same-origin", signal: controller.signal, headers: {Accept: "application/json"}});
      if ([401, 403, 404].includes(response.status)) {
        revoked = true; rotation = false; rotationLabel();
        text("refresh-label", "Zugriff nicht mehr verfügbar");
        document.getElementById("refresh-status").classList.add("is-stale");
        return;
      }
      if (!response.ok) throw new Error("Aktualisierung fehlgeschlagen");
      var updated = await response.json();
      // Fehlerhafte Antworten dürfen den letzten gültigen Stand nicht ersetzen.
      if (!validData(updated)) throw new Error("Ungültige Statistikdaten");
      render(updated); data = updated;
      document.getElementById("refresh-status").classList.remove("is-stale");
      nextRefresh = Date.now() + 300000;
    } catch (_) {
      document.getElementById("refresh-status").classList.add("is-stale");
      text("refresh-label", "Verbindung unterbrochen");
      nextRefresh = Date.now() + 60000;
    } finally { clearTimeout(timeout); pending = false; }
  }
  function tick() {
    var now = new Date();
    text("hdr-time", new Intl.DateTimeFormat("de-AT", {timeZone: timezone, hour: "2-digit", minute: "2-digit", second: "2-digit"}).format(now));
    text("hdr-date", new Intl.DateTimeFormat("de-AT", {timeZone: timezone, weekday: "long", day: "numeric", month: "long", year: "numeric"}).format(now));
    if (rotation && Date.now() >= nextView) showView(currentView === "overview" ? "development" : "overview");
    if (!revoked && Date.now() >= nextRefresh) refresh();
    else if (!pending && !revoked && !document.getElementById("refresh-status").classList.contains("is-stale")) {
      var remaining = Math.max(0, Math.ceil((nextRefresh - Date.now()) / 1000));
      text("refresh-label", "Aktualisierung in " + Math.floor(remaining / 60) + ":" + String(remaining % 60).padStart(2, "0"));
    }
  }
  scaleFrame(); render(data); rotationLabel(); showView(params.get("view") === "development" ? "development" : "overview"); tick();
  setInterval(tick, 1000);
})();
