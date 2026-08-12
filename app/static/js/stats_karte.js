(function () {
  var MONTHS_AT = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
  Chart.defaults.color = "#dae2fd";
  function formatMonthLabel(key) {
    var parts = key.split("-");
    return MONTHS_AT[parseInt(parts[1], 10) - 1] + " '" + parts[0].slice(2);
  }
  function renderStats() {
    var mapElement = document.getElementById("stats-map");
    var mapDataElement = document.getElementById("stats-map-data");
    if (mapElement && mapDataElement && window.L) {
      var markers = JSON.parse(mapDataElement.textContent || "[]");
      var map = L.map(mapElement).setView([47.5, 14.5], 7);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {attribution: "&copy; OpenStreetMap"}).addTo(map);
      var group = L.featureGroup();
      markers.forEach(function (item) {
        var color = (item.category === "B" || item.category === "F") ? "#d42225" : (item.category === "T" ? "#1877f2" : "#687386");
        var icon = L.divIcon({className: "stats-marker", html: "<span style='background:" + color + "'></span>", iconSize: [18, 18]});
        L.marker([item.lat, item.lng], {icon: icon}).bindPopup((item.alarm_type_code || "") + "<br>" + (item.address || "")).addTo(group);
      });
      group.addTo(map);
      if (markers.length) map.fitBounds(group.getBounds(), {padding: [20, 20], maxZoom: 14});
      setTimeout(function () { map.invalidateSize(); }, 0);
    }
    var chartData = document.getElementById("stats-chart-data");
    if (!chartData || !window.Chart) return;
    var data = JSON.parse(chartData.textContent || "{}");
    data.months = data.months || [];
    data.vehicles = data.vehicles || [];
    data.categories = data.categories || [0, 0, 0];
    function chart(id, config) { var el = document.getElementById(id); if (el) new Chart(el, config); }
    chart("stats-category-chart", {type: "doughnut", data: {
      labels: ["Brand", "Technisch", "Sonstige"],
      datasets: [{data: data.categories, backgroundColor: ["#d42225", "#1877f2", "#687386"]}]
    }, options: {
      radius: "65%",
      plugins: {
        legend: {labels: {generateLabels: function (chart) {
          var values = chart.data.datasets[0].data;
          var colors = chart.data.datasets[0].backgroundColor;
          var total = values.reduce(function (a, b) { return a + b; }, 0);
          return chart.data.labels.map(function (label, i) {
            var pct = total ? Math.round(values[i] * 1000 / total) / 10 : 0;
            return {text: label + ": " + pct + " %", fillStyle: colors[i], strokeStyle: colors[i], index: i};
          });
        }}},
        tooltip: {callbacks: {label: function (ctx) {
          var total = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
          var pct = total ? Math.round(ctx.parsed * 1000 / total) / 10 : 0;
          return ctx.label + ": " + ctx.parsed + " (" + pct + " %)";
        }}}
      }
    }});
    chart("stats-month-chart", {type: "bar", data: {
      labels: data.months.map(function (x) { return formatMonthLabel(x.month); }),
      datasets: [
        {label: "Brand", data: data.months.map(function (x) { return x.fire; }), backgroundColor: "#d42225"},
        {label: "Technisch", data: data.months.map(function (x) { return x.technical; }), backgroundColor: "#1877f2"},
        {label: "Sonstige", data: data.months.map(function (x) { return x.other; }), backgroundColor: "#687386"}
      ]},
      options: {scales: {x: {stacked: true}, y: {stacked: true}}, plugins: {legend: {display: true}}}
    });
    chart("stats-vehicle-chart", {type: "bar", data: {labels: data.vehicles.map(function (x) { return x.code; }), datasets: [{data: data.vehicles.map(function (x) { return x.count; }), backgroundColor: "#1877f2"}]}, options: {indexAxis: "y", plugins: {legend: {display: false}}}});
  }
  document.addEventListener("DOMContentLoaded", renderStats);
  document.body.addEventListener("htmx:afterSwap", function (event) { if (event.detail.target.id === "stats-content") renderStats(); });
})();
