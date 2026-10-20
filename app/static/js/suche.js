(function () {
  "use strict";
  var link = document.querySelector("[data-suche-link]");
  if (link) {
    document.addEventListener("keydown", function (event) {
      var element = event.target;
      var eingabe = element instanceof Element && element.closest("input, textarea, select, [contenteditable]:not([contenteditable='false']), [role='textbox']");
      if (event.isComposing || event.defaultPrevented) return;
      if (((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") ||
          (event.key === "/" && !eingabe && !event.ctrlKey && !event.metaKey && !event.altKey)) {
        event.preventDefault();
        window.location.assign(link.href);
      }
    });
  }
  var form = document.getElementById("suche-form");
  if (!form) return;
  var schluessel = "ecp-suche:" + document.body.dataset.kontaktCacheOwner + ":" + form.dataset.speicherKontext;
  var felder = Array.from(form.querySelectorAll("input:not([type='hidden'])"));
  function speichern() {
    try {
      sessionStorage.setItem(schluessel, JSON.stringify(felder.map(function (feld) {
        return feld.type === "checkbox" ? feld.checked : feld.value;
      })));
    } catch (error) { /* Suche funktioniert auch ohne Browser-Speicher. */ }
  }
  function wiederherstellen() {
    try {
      var werte = JSON.parse(sessionStorage.getItem(schluessel) || "null");
      if (!Array.isArray(werte) || werte.length !== felder.length) return;
      felder.forEach(function (feld, index) {
        if (feld.type === "checkbox") feld.checked = werte[index] === true;
        else if (typeof werte[index] === "string") feld.value = werte[index];
      });
    } catch (error) { /* Gesperrter oder ungueltiger Speicher verhindert die Suche nicht. */ }
    if (document.getElementById("suche-q").value.trim() && window.htmx) {
      window.htmx.trigger(form, "submit");
    }
  }
  form.addEventListener("input", speichern);
  form.addEventListener("change", speichern);
  form.addEventListener("submit", speichern);
  window.addEventListener("pageshow", wiederherstellen);
})();
