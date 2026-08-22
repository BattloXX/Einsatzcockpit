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
})();
