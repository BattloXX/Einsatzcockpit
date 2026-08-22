(function () {
  "use strict";

  if (!window.Chart) return;
  Chart.defaults.color = "#dae2fd";

  function updateQueuePollUrl() {
    var marker = document.getElementById("queue-poll-url");
    var container = document.getElementById("queue-status");
    if (marker && container) container.setAttribute("hx-get", marker.dataset.url);
  }
  updateQueuePollUrl();
  document.body.addEventListener("htmx:afterSwap", updateQueuePollUrl);

  function data(id) {
    var element = document.getElementById(id);
    return element ? JSON.parse(element.textContent || "{}") : null;
  }

  var dashboard = data("mailing-dashboard-data");
  if (dashboard) {
    new Chart(document.getElementById("campaignRates"), {
      type: "bar",
      data: {labels: dashboard.campaign_labels, datasets: [
        {label: "Open-Rate %", data: dashboard.campaign_open_rates, backgroundColor: "#d42225"},
        {label: "Click-Rate %", data: dashboard.campaign_click_rates, backgroundColor: "#f2b02e"}
      ]},
      options: {maintainAspectRatio: false, scales: {y: {beginAtZero: true, max: 100}}}
    });
    new Chart(document.getElementById("sendsOverTime"), {
      type: "line",
      data: {labels: dashboard.day_labels, datasets: [{label: "Versendet", data: dashboard.day_sends, borderColor: "#d42225", backgroundColor: "rgba(212,34,37,.18)", fill: true, tension: 0.25}]},
      options: {maintainAspectRatio: false, scales: {y: {beginAtZero: true}}}
    });
    new Chart(document.getElementById("failureBreakdown"), {
      type: "doughnut",
      data: {labels: dashboard.failure_labels, datasets: [{data: dashboard.failure_values, backgroundColor: ["#d42225", "#f2b02e", "#1877f2", "#687386"]}]},
      options: {maintainAspectRatio: false}
    });
  }

  var reaction = data("mailing-reaction-data");
  if (reaction) {
    new Chart(document.getElementById("reactionChart"), {
      type: "line",
      data: {labels: reaction.labels, datasets: [
        {label: "Geöffnet", data: reaction.opens, borderColor: "#18a957", tension: 0.25},
        {label: "Geklickt", data: reaction.clicks, borderColor: "#f2b02e", tension: 0.25}
      ]},
      options: {maintainAspectRatio: false, scales: {y: {beginAtZero: true, ticks: {precision: 0}}}}
    });
  }
})();
