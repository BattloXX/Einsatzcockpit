/* Bild-Annotation-Editor (Konva).
 * PR1: Bild laden, Freihand-Stift, Farben, Strichstaerken, Undo/Redo, Autosave.
 * PR2: Pfeil, Linie, Rechteck, Ellipse, X-Markierung, Text (Kontrast-Box, S/M/L),
 *      Auswahl/Transformer (verschieben/skalieren/drehen), Element-Radierer.
 * Konfiguration ueber window.ANNOTATE_CONFIG. Koordinaten = native Bildaufloesung.
 */
(function () {
  "use strict";
  var cfg = window.ANNOTATE_CONFIG;
  if (!cfg || typeof Konva === "undefined") { return; }

  var stage, imageLayer, annLayer, tr = null, natW = 0, natH = 0;
  var tool = "pen", color = "#e11d1d", width = 9, textSize = 40;
  var drawing = false, shape = null, startPt = null;
  var undoStack = [], redoStack = [];
  var saveTimer = null, dirty = false, saving = false;
  var UNDO_MAX = 60;

  function byId(id) { return document.getElementById(id); }
  function pos() { return annLayer.getRelativePointerPosition(); }

  // ── Stage-Fit (responsiv) ──────────────────────────────────────────────────
  function fit() {
    if (!stage) { return; }
    var wrap = byId(cfg.stageId).parentElement;
    var cw = wrap.clientWidth, ch = wrap.clientHeight;
    if (!cw || !ch || !natW || !natH) { return; }
    var scale = Math.min(cw / natW, ch / natH);
    stage.width(natW * scale);
    stage.height(natH * scale);
    stage.scale({ x: scale, y: scale });
    stage.batchDraw();
  }

  // ── Transformer (Auswahl) ──────────────────────────────────────────────────
  function ensureTransformer() {
    tr = new Konva.Transformer({
      rotateEnabled: true, borderStroke: "#93c5fd", anchorStroke: "#93c5fd",
      anchorFill: "#111827", anchorSize: 12, ignoreStroke: true, padding: 4,
    });
    annLayer.add(tr);
  }
  function attachTr(node) { if (tr) { tr.nodes([node]); annLayer.batchDraw(); } }
  function detachTr() { if (tr) { tr.nodes([]); annLayer.batchDraw(); } }

  // ── Undo/Redo (Transformer aus dem Snapshot ausklammern) ────────────────────
  function snapshot() {
    var had = tr && tr.getLayer() === annLayer;
    if (had) { tr.remove(); }
    var j = annLayer.toJSON();
    if (had) { annLayer.add(tr); }
    return j;
  }
  function wireListeners(node) {
    node.off(".anno");
    node.on("dragstart.anno transformstart.anno", pushUndo);
    node.on("dragend.anno transformend.anno", markDirty);
  }
  function wireShape(node) { node.draggable(tool === "select"); wireListeners(node); }

  function restore(json) {
    var tmp = Konva.Node.create(json);
    annLayer.destroyChildren();  // zerstoert auch den Transformer
    tr = null;
    // Konva 9: getChildren() liefert ein Array — vor dem Verschieben kopieren
    Array.prototype.slice.call(tmp.getChildren()).forEach(function (ch) { ch.moveTo(annLayer); });
    tmp.destroy();
    ensureTransformer();
    var sel = (tool === "select");
    annLayer.getChildren().forEach(function (n) {
      if (n !== tr) { n.draggable(sel); wireListeners(n); }
    });
    annLayer.draw();
  }

  function pushUndo() {
    undoStack.push(snapshot());
    if (undoStack.length > UNDO_MAX) { undoStack.shift(); }
    redoStack.length = 0;
    aktualisiereButtons();
  }
  function undo() {
    if (!undoStack.length) { return; }
    redoStack.push(snapshot());
    restore(undoStack.pop());
    markDirty(); aktualisiereButtons();
  }
  function redo() {
    if (!redoStack.length) { return; }
    undoStack.push(snapshot());
    restore(redoStack.pop());
    markDirty(); aktualisiereButtons();
  }
  function alleLoeschen() {
    var echte = annLayer.getChildren().filter(function (n) { return n !== tr; });
    if (!echte.length) { return; }
    if (!window.confirm("Alle Annotationen löschen?")) { return; }
    pushUndo();
    echte.forEach(function (n) { n.destroy(); });
    detachTr(); annLayer.draw(); markDirty();
  }
  function aktualisiereButtons() {
    var u = document.querySelector('[data-anno-action="undo"]');
    var r = document.querySelector('[data-anno-action="redo"]');
    if (u) { u.disabled = !undoStack.length; }
    if (r) { r.disabled = !redoStack.length; }
  }

  // ── Werkzeug-Umschaltung ────────────────────────────────────────────────────
  function setTool(t) {
    tool = t;
    document.querySelectorAll("[data-anno-tool]").forEach(function (b) {
      b.classList.toggle("is-active", b.getAttribute("data-anno-tool") === t);
    });
    var sel = (t === "select");
    annLayer.getChildren().forEach(function (n) { if (n !== tr) { n.draggable(sel); } });
    if (!sel) { detachTr(); }
    if (stage) { stage.container().style.cursor = sel ? "default" : "crosshair"; }
  }

  // ── Auswahl / Radierer ─────────────────────────────────────────────────────
  function zielNode(e) {
    var t = e.target;
    if (!t || t === stage) { return null; }
    if (t.getLayer && t.getLayer() === imageLayer) { return null; }
    var p = t.getParent && t.getParent();
    // Klick auf Transformer-Anker ignorieren
    if (p && p.className === "Transformer") { return "handle"; }
    // Gruppen/Label als Ganzes waehlen
    while (p && p.getLayer && p.getLayer() === annLayer && p !== annLayer &&
           (p.className === "Group" || p.className === "Label")) {
      t = p; p = t.getParent();
    }
    return (t.getLayer && t.getLayer() === annLayer && t !== tr) ? t : null;
  }
  function onSelectDown(e) {
    var n = zielNode(e);
    if (n === "handle") { return; }
    if (n) { n.draggable(true); attachTr(n); } else { detachTr(); }
  }
  function onErase(e) {
    var n = zielNode(e);
    if (!n || n === "handle") { return; }
    pushUndo(); n.destroy(); detachTr(); markDirty(); annLayer.batchDraw();
  }

  // ── Text ────────────────────────────────────────────────────────────────────
  function _luminanz(hex) {
    var h = hex.replace("#", "");
    if (h.length === 3) { h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]; }
    var r = parseInt(h.substr(0, 2), 16), g = parseInt(h.substr(2, 2), 16), b = parseInt(h.substr(4, 2), 16);
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  }
  function onTextPlace(e) {
    var p = pos();
    var ev = e.evt;
    // Verhindert, dass der mousedown-Default den Fokus auf den Canvas legt und die
    // gerade erzeugte Textarea sofort wieder blurrt (Commit-leer -> weg).
    if (ev && ev.preventDefault) { ev.preventDefault(); }
    var cx = ev && ev.clientX != null ? ev.clientX : (ev.touches && ev.touches[0].clientX) || 100;
    var cy = ev && ev.clientY != null ? ev.clientY : (ev.touches && ev.touches[0].clientY) || 100;
    var ta = document.createElement("textarea");
    ta.rows = 1;
    ta.style.cssText = "position:fixed;z-index:5000;left:" + cx + "px;top:" + cy +
      "px;font-size:16px;padding:5px 7px;border:1px solid #93c5fd;border-radius:6px;" +
      "background:#0d150f;color:#fff;min-width:140px;resize:none;";
    document.body.appendChild(ta);
    setTimeout(function () { ta.focus(); }, 0);  // nach dem Klick-Fokuszyklus fokussieren
    var done = false;
    function commit() {
      if (done) { return; } done = true;
      var val = ta.value.replace(/\s+$/, ""); ta.remove();
      if (!val) { return; }
      pushUndo();
      var boxDunkel = _luminanz(color) > 0.5;  // helle Schrift -> dunkle Box
      var label = new Konva.Label({ x: p.x, y: p.y });
      label.add(new Konva.Tag({ fill: boxDunkel ? "rgba(0,0,0,0.55)" : "rgba(255,255,255,0.8)", cornerRadius: 4 }));
      label.add(new Konva.Text({ text: val, fontSize: textSize, fill: color, padding: 7, fontStyle: "bold" }));
      annLayer.add(label); wireShape(label); markDirty(); annLayer.batchDraw();
    }
    ta.addEventListener("blur", commit);
    ta.addEventListener("keydown", function (k) {
      if (k.key === "Enter" && !k.shiftKey) { k.preventDefault(); ta.blur(); }
      if (k.key === "Escape") { ta.value = ""; ta.blur(); }
    });
  }

  // ── X-Markierung ────────────────────────────────────────────────────────────
  function placeX() {
    pushUndo();
    var p = pos();
    var s = Math.max(28, width * 4);
    var g = new Konva.Group({ x: p.x, y: p.y });
    g.add(new Konva.Line({ points: [-s / 2, -s / 2, s / 2, s / 2], stroke: color, strokeWidth: width, lineCap: "round" }));
    g.add(new Konva.Line({ points: [-s / 2, s / 2, s / 2, -s / 2], stroke: color, strokeWidth: width, lineCap: "round" }));
    annLayer.add(g); wireShape(g); markDirty(); annLayer.batchDraw();
  }

  // ── Zeichnen (Freihand + Formen) ────────────────────────────────────────────
  function onDown(e) {
    if (!cfg.canWrite) { return; }
    var ev = e.evt;
    if (ev && ev.touches && ev.touches.length > 1) { return; }
    if (tool === "select") { onSelectDown(e); return; }
    if (tool === "eraser") { onErase(e); return; }
    if (tool === "text") { onTextPlace(e); return; }
    if (ev && ev.preventDefault) { ev.preventDefault(); }
    if (tool === "x") { placeX(); return; }

    pushUndo();
    drawing = true;
    var p = pos(); startPt = p;
    var head = Math.max(12, width * 2.2);
    if (tool === "pen") {
      shape = new Konva.Line({ points: [p.x, p.y], stroke: color, strokeWidth: width, lineCap: "round", lineJoin: "round", tension: 0.4 });
    } else if (tool === "line") {
      shape = new Konva.Line({ points: [p.x, p.y, p.x, p.y], stroke: color, strokeWidth: width, lineCap: "round" });
    } else if (tool === "arrow") {
      shape = new Konva.Arrow({ points: [p.x, p.y, p.x, p.y], stroke: color, fill: color, strokeWidth: width, pointerLength: head, pointerWidth: head, lineCap: "round" });
    } else if (tool === "rect") {
      shape = new Konva.Rect({ x: p.x, y: p.y, width: 0, height: 0, stroke: color, strokeWidth: width });
    } else if (tool === "ellipse") {
      shape = new Konva.Ellipse({ x: p.x, y: p.y, radiusX: 0, radiusY: 0, stroke: color, strokeWidth: width });
    }
    if (shape) { annLayer.add(shape); }
  }

  function onMove(e) {
    if (!drawing || !shape) { return; }
    var ev = e.evt;
    if (ev && ev.touches && ev.touches.length > 1) { onUp(); return; }
    if (ev && ev.preventDefault) { ev.preventDefault(); }
    var p = pos();
    if (tool === "pen") {
      var pts = shape.points(); pts.push(p.x, p.y); shape.points(pts);
    } else if (tool === "line" || tool === "arrow") {
      shape.points([startPt.x, startPt.y, p.x, p.y]);
    } else if (tool === "rect") {
      shape.x(Math.min(startPt.x, p.x)); shape.y(Math.min(startPt.y, p.y));
      shape.width(Math.abs(p.x - startPt.x)); shape.height(Math.abs(p.y - startPt.y));
    } else if (tool === "ellipse") {
      shape.x((startPt.x + p.x) / 2); shape.y((startPt.y + p.y) / 2);
      shape.radiusX(Math.abs(p.x - startPt.x) / 2); shape.radiusY(Math.abs(p.y - startPt.y) / 2);
    }
    annLayer.batchDraw();
  }

  function onUp() {
    if (!drawing) { return; }
    drawing = false;
    if (shape) {
      var tooSmall = false;
      if (tool === "pen") { tooSmall = shape.points().length <= 2; }
      else if (tool === "rect") { tooSmall = Math.abs(shape.width()) < 3 && Math.abs(shape.height()) < 3; }
      else if (tool === "ellipse") { tooSmall = shape.radiusX() < 2 && shape.radiusY() < 2; }
      else if (tool === "line" || tool === "arrow") {
        var pt = shape.points(); tooSmall = Math.hypot(pt[2] - pt[0], pt[3] - pt[1]) < 5;
      }
      if (tooSmall) { shape.destroy(); undoStack.pop(); aktualisiereButtons(); }
      else { wireShape(shape); markDirty(); }
    }
    shape = null; annLayer.batchDraw();
  }

  // ── Autosave ────────────────────────────────────────────────────────────────
  function markDirty() {
    dirty = true; setStatus("nicht gespeichert");
    if (saveTimer) { clearTimeout(saveTimer); }
    saveTimer = setTimeout(save, 10000);
  }
  function flachesPng() {
    var visTr = tr && tr.visible();
    if (tr) { tr.hide(); }
    var longNat = Math.max(natW, natH);
    var target = Math.min(longNat, 2400);
    var longStage = Math.max(stage.width(), stage.height()) || 1;
    var url = stage.toDataURL({ pixelRatio: target / longStage, mimeType: "image/png" });
    if (tr && visTr) { tr.show(); }
    return url;
  }
  function save(useKeepalive) {
    if (!dirty || saving || !cfg.canWrite) { return; }
    saving = true; setStatus("speichert …");
    var body = JSON.stringify({ annotation_json: snapshot(), png: flachesPng() });
    var opts = { method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": cfg.csrf }, body: body };
    if (useKeepalive) { opts.keepalive = true; }
    return fetch(cfg.saveUrl, opts).then(function (r) {
      saving = false;
      if (r.ok) { dirty = false; setStatus("gespeichert"); }
      else { setStatus("Speichern fehlgeschlagen"); bufferOffline(body); }
    }).catch(function () { saving = false; setStatus("offline – gepuffert"); bufferOffline(body); });
  }
  function bufferOffline(body) {
    try { sessionStorage.setItem("anno_buffer_" + cfg.saveUrl, body); } catch (e) { /* ignore */ }
  }
  function setStatus(txt) { var el = byId("anno-status"); if (el) { el.textContent = txt; } }

  // ── Toolbar ────────────────────────────────────────────────────────────────
  function setActive(selector, aktiv) {
    document.querySelectorAll(selector).forEach(function (b) { b.classList.remove("is-active"); });
    if (aktiv) { aktiv.classList.add("is-active"); }
  }
  function bindToolbar() {
    document.querySelectorAll("[data-anno-tool]").forEach(function (b) {
      b.addEventListener("click", function () { setTool(b.getAttribute("data-anno-tool")); });
    });
    document.querySelectorAll("[data-anno-color]").forEach(function (b) {
      b.addEventListener("click", function () { color = b.getAttribute("data-anno-color"); setActive("[data-anno-color]", b); });
    });
    document.querySelectorAll("[data-anno-width]").forEach(function (b) {
      b.addEventListener("click", function () { width = parseInt(b.getAttribute("data-anno-width"), 10) || 9; setActive("[data-anno-width]", b); });
    });
    document.querySelectorAll("[data-anno-textsize]").forEach(function (b) {
      b.addEventListener("click", function () { textSize = parseInt(b.getAttribute("data-anno-textsize"), 10) || 40; setActive("[data-anno-textsize]", b); });
    });
    document.querySelectorAll("[data-anno-action]").forEach(function (b) {
      b.addEventListener("click", function () {
        var a = b.getAttribute("data-anno-action");
        if (a === "undo") { undo(); }
        else if (a === "redo") { redo(); }
        else if (a === "clear") { alleLoeschen(); }
        else if (a === "done") {
          var pr = save();
          var go = function () { if (cfg.backUrl) { location.href = cfg.backUrl; } else { history.back(); } };
          if (pr && pr.then) { pr.then(go); } else { go(); }
        }
      });
    });
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  function initStage(img) {
    natW = img.naturalWidth || img.width;
    natH = img.naturalHeight || img.height;

    stage = new Konva.Stage({ container: cfg.stageId, width: natW, height: natH });
    imageLayer = new Konva.Layer({ listening: false });
    imageLayer.add(new Konva.Image({ image: img, width: natW, height: natH }));
    imageLayer.cache();
    stage.add(imageLayer);

    if (cfg.annotationJson) {
      try { annLayer = Konva.Node.create(cfg.annotationJson); }
      catch (e) { annLayer = new Konva.Layer(); }
    } else {
      annLayer = new Konva.Layer();
    }
    stage.add(annLayer);
    ensureTransformer();
    annLayer.getChildren().forEach(function (n) { if (n !== tr) { wireListeners(n); n.draggable(false); } });

    fit();
    window.addEventListener("resize", fit);

    if (cfg.canWrite) {
      stage.on("mousedown touchstart", onDown);
      stage.on("mousemove touchmove", onMove);
      window.addEventListener("mouseup", onUp);
      stage.on("touchend", onUp);
      window.addEventListener("keydown", function (e) {
        if ((e.key === "Delete" || e.key === "Backspace") && tr && tr.nodes().length) {
          if (document.activeElement && document.activeElement.tagName === "TEXTAREA") { return; }
          e.preventDefault(); pushUndo();
          tr.nodes().forEach(function (n) { n.destroy(); });
          detachTr(); markDirty(); annLayer.batchDraw();
        }
      });
    }
    setTool(cfg.canWrite ? "pen" : "select");
    aktualisiereButtons();
    setStatus(cfg.canWrite ? "bereit" : "schreibgeschützt");
  }

  function boot() {
    bindToolbar();
    var c0 = document.querySelector('[data-anno-color]');
    var w0 = document.querySelector('[data-anno-width="9"]') || document.querySelector('[data-anno-width]');
    var ts0 = document.querySelector('[data-anno-textsize="40"]') || document.querySelector('[data-anno-textsize]');
    if (c0) { setActive("[data-anno-color]", c0); color = c0.getAttribute("data-anno-color"); }
    if (w0) { setActive("[data-anno-width]", w0); width = parseInt(w0.getAttribute("data-anno-width"), 10) || 9; }
    if (ts0) { setActive("[data-anno-textsize]", ts0); textSize = parseInt(ts0.getAttribute("data-anno-textsize"), 10) || 40; }

    var img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = function () { initStage(img); };
    img.onerror = function () { setStatus("Bild konnte nicht geladen werden"); };
    img.src = cfg.bildUrl;

    window.addEventListener("pagehide", function () { if (dirty) { save(true); } });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && dirty) { save(true); }
    });
  }

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", boot); }
  else { boot(); }
})();
