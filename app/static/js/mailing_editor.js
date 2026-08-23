(function () {
  "use strict";
  var activeEditor = document.getElementById("mailing-body-html");
  var htmlTextarea = document.getElementById("mailing-body-html");
  var quillContainer = document.getElementById("mailing-body-html-quill");
  var quill = null;
  var htmlMode = true;

  function dispatchHtmlInput() {
    htmlTextarea.dispatchEvent(new Event("input", {bubbles: true}));
  }

  function toggleHtmlMode() {
    htmlMode = !htmlMode;
    var container = quillContainer.parentElement.querySelector(".ql-container");
    var toolbar = quillContainer.parentElement.querySelector(".ql-toolbar");
    var button = toolbar.querySelector(".ql-html-source");
    if (htmlMode) {
      htmlTextarea.value = quill.root.innerHTML;
      container.style.display = "none";
      toolbar.style.opacity = "0.45";
      toolbar.style.pointerEvents = "none";
      button.style.opacity = "1";
      button.style.pointerEvents = "auto";
      button.classList.add("ql-active");
      htmlTextarea.style.display = "block";
      htmlTextarea.focus();
      dispatchHtmlInput();
    } else {
      quill.clipboard.dangerouslyPasteHTML(htmlTextarea.value);
      htmlTextarea.style.display = "none";
      container.style.display = "";
      toolbar.style.opacity = "";
      toolbar.style.pointerEvents = "";
      button.style.opacity = "";
      button.style.pointerEvents = "";
      button.classList.remove("ql-active");
      quill.focus();
    }
  }

  if (htmlTextarea && quillContainer && typeof Quill !== "undefined") {
    quill = new Quill(quillContainer, {
      theme: "snow",
      placeholder: "HTML-Inhalt hier eingeben...",
      modules: {
        toolbar: {
          container: [
            [{header: [1, 2, 3, false]}],
            ["bold", "italic", "underline"],
            ["link"],
            [{list: "ordered"}, {list: "bullet"}],
            ["clean"],
            ["html-source"]
          ],
          handlers: {"html-source": toggleHtmlMode}
        }
      }
    });
    if (htmlTextarea.value.trim()) quill.clipboard.dangerouslyPasteHTML(htmlTextarea.value);
    quill.root.addEventListener("focus", function () { activeEditor = htmlTextarea; });
    quill.on("text-change", function () {
      if (htmlMode) return;
      htmlTextarea.value = quill.root.innerHTML;
      dispatchHtmlInput();
    });
    var initialToolbar = quillContainer.parentElement.querySelector(".ql-toolbar");
    var initialButton = initialToolbar.querySelector(".ql-html-source");
    quillContainer.parentElement.querySelector(".ql-container").style.display = "none";
    initialToolbar.style.opacity = "0.45";
    initialToolbar.style.pointerEvents = "none";
    initialButton.style.opacity = "1";
    initialButton.style.pointerEvents = "auto";
    initialButton.classList.add("ql-active");
  }
  ["mailing-body-html", "mailing-body-text"].forEach(function (id) {
    var field = document.getElementById(id);
    if (field) field.addEventListener("focus", function () { activeEditor = field; });
  });
  document.querySelectorAll(".mailing-variable").forEach(function (button) {
    button.addEventListener("click", function () {
      if (!activeEditor) return;
      var token = "{{ " + button.dataset.variable + " }}";
      if (quill && activeEditor === htmlTextarea && !htmlMode) {
        quill.focus();
        var range = quill.getSelection();
        var index = range ? range.index : quill.getLength();
        quill.insertText(index, token, Quill.sources.USER);
        quill.setSelection(index + token.length, 0);
        return;
      }
      var start = activeEditor.selectionStart;
      var end = activeEditor.selectionEnd;
      activeEditor.value = activeEditor.value.slice(0, start) + token + activeEditor.value.slice(end);
      var position = start + token.length;
      activeEditor.setSelectionRange(position, position);
      activeEditor.focus();
      activeEditor.dispatchEvent(new Event("input", {bubbles: true}));
    });
  });

  var templateForm = document.getElementById("mailing-template-form");
  if (templateForm && quill) {
    templateForm.addEventListener("submit", function () {
      if (!htmlMode) htmlTextarea.value = quill.root.innerHTML;
    });
  }

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
