(function () {
  "use strict";
  var activeEditor = document.getElementById("mailing-body-html");
  ["mailing-body-html", "mailing-body-text"].forEach(function (id) {
    var field = document.getElementById(id);
    if (field) field.addEventListener("focus", function () { activeEditor = field; });
  });
  document.querySelectorAll(".mailing-variable").forEach(function (button) {
    button.addEventListener("click", function () {
      if (!activeEditor) return;
      var token = "{{ " + button.dataset.variable + " }}";
      var start = activeEditor.selectionStart;
      var end = activeEditor.selectionEnd;
      activeEditor.value = activeEditor.value.slice(0, start) + token + activeEditor.value.slice(end);
      var position = start + token.length;
      activeEditor.setSelectionRange(position, position);
      activeEditor.focus();
      activeEditor.dispatchEvent(new Event("input", {bubbles: true}));
    });
  });

  var preview = document.getElementById("preview");
  var previewButtons = document.querySelectorAll("[data-preview-view]");
  function setPreviewView(view) {
    var isMobile = view === "mobile";
    if (preview) preview.classList.toggle("mailing-preview--mobile", isMobile);
    previewButtons.forEach(function (button) {
      var isActive = button.dataset.previewView === view;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", String(isActive));
    });
    try { localStorage.setItem("ec_mailing_preview_view", view); } catch (e) {}
  }
  if (preview && previewButtons.length) {
    var savedView = "desktop";
    try { savedView = localStorage.getItem("ec_mailing_preview_view") || savedView; } catch (e) {}
    if (savedView !== "mobile") savedView = "desktop";
    setPreviewView(savedView);
    previewButtons.forEach(function (button) {
      button.addEventListener("click", function () { setPreviewView(button.dataset.previewView); });
    });
  }

  var darkButton = document.querySelector("[data-preview-dark]");
  function setPreviewDark(isDark) {
    if (preview) preview.classList.toggle("mailing-preview--dark", isDark);
    if (darkButton) {
      darkButton.classList.toggle("is-active", isDark);
      darkButton.setAttribute("aria-pressed", String(isDark));
    }
    try { localStorage.setItem("ec_mailing_preview_dark", isDark ? "1" : "0"); } catch (e) {}
  }
  if (preview && darkButton) {
    var savedDark = false;
    try { savedDark = localStorage.getItem("ec_mailing_preview_dark") === "1"; } catch (e) {}
    setPreviewDark(savedDark);
    darkButton.addEventListener("click", function () {
      setPreviewDark(!preview.classList.contains("mailing-preview--dark"));
    });
  }
})();
