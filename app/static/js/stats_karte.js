(function () {
  function renderStats() {
    var mapElement = document.getElementById("stats-map");
    var mapDataElement = document.getElementById("stats-map-data");
    if (mapElement && mapDataElement && window.L) {
      var markers = JSON.parse(mapDataElement.textContent || "[]");
      var map = L.map(mapElement).setView([47.5, 14.5], 7);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {attribution: "&copy; OpenStreetMap"}).addTo(map);
      var group = window.L.markerClusterGroup ? L.markerClusterGroup() : L.layerGroup();
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
    chart("stats-category-chart", {type: "doughnut", data: {labels: ["Brand", "Technisch", "Sonstige"], datasets: [{data: data.categories, backgroundColor: ["#d42225", "#1877f2", "#687386"]}]}});
    chart("stats-month-chart", {type: "bar", data: {labels: data.months.map(function (x) { return x.month; }), datasets: [{data: data.months.map(function (x) { return x.count; }), backgroundColor: "#d42225"}]}, options: {plugins: {legend: {display: false}}}});
    chart("stats-vehicle-chart", {type: "bar", data: {labels: data.vehicles.map(function (x) { return x.code; }), datasets: [{data: data.vehicles.map(function (x) { return x.count; }), backgroundColor: "#1877f2"}]}, options: {indexAxis: "y", plugins: {legend: {display: false}}}});
  }
  document.addEventListener("DOMContentLoaded", renderStats);
  document.body.addEventListener("htmx:afterSwap", function (event) { if (event.detail.target.id === "stats-content") renderStats(); });
})();
