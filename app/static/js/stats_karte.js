(function () {
  "use strict";

  var MONTHS_AT = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
  var RED_SHADES = ["#fca5a5", "#f87171", "#ef4444", "#dc2626", "#b91c1c", "#7f1d1d"];
  var BLUE_SHADES = ["#93c5fd", "#60a5fa", "#3b82f6", "#2563eb", "#1d4ed8", "#1e3a8a"];
  var activeMap = null;
  var activeCharts = [];

  Chart.defaults.color = "#dae2fd";

  function formatMonthLabel(key) {
    var parts = key.split("-");
    return MONTHS_AT[parseInt(parts[1], 10) - 1] + " '" + parts[0].slice(2);
  }

  function cleanupStats() {
    if (activeMap) {
      activeMap.remove();
      activeMap = null;
    }
    activeCharts.forEach(function (chart) {
      chart.destroy();
    });
    activeCharts = [];
  }

  function alarmNumber(code) {
    var match = /\d+/.exec(code || "");
    return match ? parseInt(match[0], 10) : 0;
  }

  function family(category) {
    if (category === "B" || category === "F") return "fire";
    if (category === "T") return "technical";
    return "other";
  }

  function alarmColors(rows) {
    var ranks = {};
    ["fire", "technical"].forEach(function (name) {
      rows.filter(function (row) { return family(row.category) === name; })
        .slice()
        .sort(function (a, b) {
          return alarmNumber(a.code) - alarmNumber(b.code) || (a.code || "").localeCompare(b.code || "");
        })
        .forEach(function (row, index) { ranks[name + ":" + row.code] = index; });
    });
    return rows.map(function (row) {
      var name = family(row.category);
      if (name === "other") return "#687386";
      var palette = name === "fire" ? RED_SHADES : BLUE_SHADES;
      return palette[Math.min(ranks[name + ":" + row.code], palette.length - 1)];
    });
  }

  var segmentPercentPlugin = {
    id: "statsSegmentPercent",
    afterDatasetsDraw: function (chart) {
      var ctx = chart.ctx;
      var values = chart.data.datasets[0].data;
      var total = values.reduce(function (sum, value) { return sum + value; }, 0);
      if (!total) return;
      ctx.save();
      ctx.fillStyle = "#ffffff";
      ctx.font = "600 11px JetBrains Mono, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      chart.getDatasetMeta(0).data.forEach(function (arc, index) {
        var point = arc.tooltipPosition();
        var percent = Math.round(values[index] * 1000 / total) / 10;
        ctx.fillText(percent + " %", point.x, point.y);
      });
      ctx.restore();
    }
  };

  function renderStats() {
    cleanupStats();

    var mapElement = document.getElementById("stats-map");
    var mapDataElement = document.getElementById("stats-map-data");
    if (mapElement && mapDataElement && window.L) {
      var markers = JSON.parse(mapDataElement.textContent || "[]");
      activeMap = L.map(mapElement).setView([47.5, 14.5], 7);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {attribution: "&copy; OpenStreetMap"}).addTo(activeMap);
      var group = L.featureGroup();
      markers.forEach(function (item) {
        var color = (item.category === "B" || item.category === "F") ? "#d42225" : (item.category === "T" ? "#1877f2" : "#687386");
        var icon = L.divIcon({className: "stats-marker", html: "<span style='background:" + color + "'></span>", iconSize: [18, 18]});
        L.marker([item.lat, item.lng], {icon: icon}).bindPopup((item.alarm_type_code || "") + "<br>" + (item.address || "")).addTo(group);
      });
      group.addTo(activeMap);
      if (markers.length) activeMap.fitBounds(group.getBounds(), {padding: [20, 20], maxZoom: 14});
      setTimeout(function () { if (activeMap) activeMap.invalidateSize(); }, 0);
    }

    var chartData = document.getElementById("stats-chart-data");
    if (!chartData || !window.Chart) return;
    var data = JSON.parse(chartData.textContent || "{}");
    data.months = data.months || [];
    data.vehicles = data.vehicles || [];
    data.by_alarm_type = data.by_alarm_type || [];

    function chart(id, config) {
      var element = document.getElementById(id);
      if (element) activeCharts.push(new Chart(element, config));
    }

    var alarmRows = data.by_alarm_type;
    var alarmValues = alarmRows.map(function (row) { return row.count; });
    var alarmBackgrounds = alarmColors(alarmRows);
    chart("stats-category-chart", {
      type: "doughnut",
      data: {
        labels: alarmRows.map(function (row) { return row.code; }),
        datasets: [{data: alarmValues, backgroundColor: alarmBackgrounds}]
      },
      plugins: [segmentPercentPlugin],
      options: {
        maintainAspectRatio: false,
        radius: "82%",
        plugins: {
          legend: {labels: {generateLabels: function (chartInstance) {
            var values = chartInstance.data.datasets[0].data;
            var colors = chartInstance.data.datasets[0].backgroundColor;
            var total = values.reduce(function (sum, value) { return sum + value; }, 0);
            return chartInstance.data.labels.map(function (label, index) {
              var percent = total ? Math.round(values[index] * 1000 / total) / 10 : 0;
              return {text: label + ": " + values[index] + " (" + percent + " %)", fillStyle: colors[index], strokeStyle: colors[index], index: index};
            });
          }}},
          tooltip: {callbacks: {label: function (ctx) {
            var total = ctx.dataset.data.reduce(function (sum, value) { return sum + value; }, 0);
            var percent = total ? Math.round(ctx.parsed * 1000 / total) / 10 : 0;
            return ctx.label + ": " + ctx.parsed + " (" + percent + " %)";
          }}}
        }
      }
    });
    chart("stats-month-chart", {type: "bar", data: {
      labels: data.months.map(function (row) { return formatMonthLabel(row.month); }),
      datasets: [
        {label: "Brand", data: data.months.map(function (row) { return row.fire; }), backgroundColor: "#d42225"},
        {label: "Technisch", data: data.months.map(function (row) { return row.technical; }), backgroundColor: "#1877f2"},
        {label: "Sonstige", data: data.months.map(function (row) { return row.other; }), backgroundColor: "#687386"}
      ]},
      options: {scales: {x: {stacked: true}, y: {stacked: true}}, plugins: {legend: {display: true}}}
    });
    chart("stats-vehicle-chart", {type: "bar", data: {labels: data.vehicles.map(function (row) { return row.code; }), datasets: [{data: data.vehicles.map(function (row) { return row.count; }), backgroundColor: "#1877f2"}]}, options: {indexAxis: "y", plugins: {legend: {display: false}}}});
  }

  document.addEventListener("DOMContentLoaded", renderStats);
  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.detail.target.id === "stats-content") renderStats();
  });
})();
