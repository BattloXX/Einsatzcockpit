/* Bild-Annotation-Editor (Konva). PR1: Bild laden, Freihand-Stift, Farbpalette,
 * 3 Strichstaerken, Undo/Redo, Autosave (Vektor-JSON + flaches PNG).
 * Konfiguration ueber window.ANNOTATE_CONFIG (vom Editor-Template gesetzt).
 * Koordinatensystem = native Bildaufloesung; die Stage wird responsiv skaliert.
 */
(function () {
  "use strict";
  var cfg = window.ANNOTATE_CONFIG;
  if (!cfg || typeof Konva === "undefined") { return; }

  var stage, imageLayer, annLayer, natW = 0, natH = 0;
  var tool = "pen", color = "#e11d1d", width = 9;
  var drawing = false, curLine = null;
  var undoStack = [], redoStack = [];
  var saveTimer = null, dirty = false, saving = false;

  var UNDO_MAX = 60;

  function byId(id) { return document.getElementById(id); }

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

  // ── Undo/Redo ──────────────────────────────────────────────────────────────
  function snapshot() { return annLayer.toJSON(); }

  function restore(json) {
    var tmp = Konva.Node.create(json);
    annLayer.destroyChildren();
    tmp.getChildren().toArray().forEach(function (ch) { ch.moveTo(annLayer); });
    tmp.destroy();
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
    markDirty();
    aktualisiereButtons();
  }

  function redo() {
    if (!redoStack.length) { return; }
    undoStack.push(snapshot());
    restore(redoStack.pop());
    markDirty();
    aktualisiereButtons();
  }

  function alleLoeschen() {
    if (!annLayer.getChildren().length) { return; }
    if (!window.confirm("Alle Annotationen löschen?")) { return; }
    pushUndo();
    annLayer.destroyChildren();
    annLayer.draw();
    markDirty();
  }

  function aktualisiereButtons() {
    var u = document.querySelector('[data-anno-action="undo"]');
    var r = document.querySelector('[data-anno-action="redo"]');
    if (u) { u.disabled = !undoStack.length; }
    if (r) { r.disabled = !redoStack.length; }
  }

  // ── Zeichnen (Freihand) ────────────────────────────────────────────────────
  function pointerPos() { return annLayer.getRelativePointerPosition(); }

  function startDraw(e) {
    if (!cfg.canWrite || tool !== "pen") { return; }
    var ev = e.evt;
    if (ev && ev.touches && ev.touches.length > 1) { return; }  // 2-Finger: nicht zeichnen
    if (ev && ev.preventDefault) { ev.preventDefault(); }
    pushUndo();
    drawing = true;
    var p = pointerPos();
    curLine = new Konva.Line({
      points: [p.x, p.y], stroke: color, strokeWidth: width,
      lineCap: "round", lineJoin: "round", tension: 0.4,
    });
    annLayer.add(curLine);
  }

  function moveDraw(e) {
    if (!drawing || !curLine) { return; }
    var ev = e.evt;
    if (ev && ev.touches && ev.touches.length > 1) { endDraw(); return; }
    if (ev && ev.preventDefault) { ev.preventDefault(); }
    var p = pointerPos();
    var pts = curLine.points();
    pts.push(p.x, p.y);
    curLine.points(pts);
    annLayer.batchDraw();
  }

  function endDraw() {
    if (!drawing) { return; }
    drawing = false;
    if (curLine && curLine.points().length <= 2) {
      // reiner Klick ohne Bewegung -> als Punkt behalten (kleiner Kreis waere
      // sauberer, aber Line mit 1 Punkt zeichnet nichts) -> Undo-Snapshot bleibt
      curLine.destroy();
      undoStack.pop();  // den soeben gesetzten Snapshot wieder verwerfen
      aktualisiereButtons();
    } else {
      markDirty();
    }
    curLine = null;
    annLayer.batchDraw();
  }

  // ── Autosave ───────────────────────────────────────────────────────────────
  function markDirty() {
    dirty = true;
    setStatus("nicht gespeichert");
    if (saveTimer) { clearTimeout(saveTimer); }
    saveTimer = setTimeout(save, 10000);
  }

  function flachesPng() {
    // Native Aufloesung, lange Kante auf max 2400 px gedeckelt (Payload/PDF-Balance)
    var longNat = Math.max(natW, natH);
    var target = Math.min(longNat, 2400);
    var longStage = Math.max(stage.width(), stage.height()) || 1;
    var pixelRatio = target / longStage;
    return stage.toDataURL({ pixelRatio: pixelRatio, mimeType: "image/png" });
  }

  function save(useKeepalive) {
    if (!dirty || saving || !cfg.canWrite) { return; }
    saving = true;
    setStatus("speichert …");
    var body = JSON.stringify({ annotation_json: annLayer.toJSON(), png: flachesPng() });
    var opts = {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": cfg.csrf },
      body: body,
    };
    if (useKeepalive) { opts.keepalive = true; }
    return fetch(cfg.saveUrl, opts).then(function (r) {
      saving = false;
      if (r.ok) { dirty = false; setStatus("gespeichert"); }
      else { setStatus("Speichern fehlgeschlagen"); bufferOffline(body); }
    }).catch(function () {
      saving = false;
      setStatus("offline – gepuffert");
      bufferOffline(body);
    });
  }

  function bufferOffline(body) {
    // Offline-Puffer (voller Ausbau in PR6): letzten Stand in sessionStorage
    try { sessionStorage.setItem("anno_buffer_" + cfg.saveUrl, body); } catch (e) { /* ignore */ }
  }

  function setStatus(txt) {
    var el = byId("anno-status");
    if (el) { el.textContent = txt; }
  }

  // ── Toolbar ────────────────────────────────────────────────────────────────
  function bindToolbar() {
    document.querySelectorAll("[data-anno-color]").forEach(function (b) {
      b.addEventListener("click", function () {
        color = b.getAttribute("data-anno-color");
        setActive("[data-anno-color]", b);
      });
    });
    document.querySelectorAll("[data-anno-width]").forEach(function (b) {
      b.addEventListener("click", function () {
        width = parseInt(b.getAttribute("data-anno-width"), 10) || 9;
        setActive("[data-anno-width]", b);
      });
    });
    document.querySelectorAll("[data-anno-action]").forEach(function (b) {
      b.addEventListener("click", function () {
        var a = b.getAttribute("data-anno-action");
        if (a === "undo") { undo(); }
        else if (a === "redo") { redo(); }
        else if (a === "clear") { alleLoeschen(); }
        else if (a === "done") {
          var p = save();
          var go = function () { if (cfg.backUrl) { location.href = cfg.backUrl; } else { history.back(); } };
          if (p && p.then) { p.then(go); } else { go(); }
        }
      });
    });
  }

  function setActive(selector, aktiv) {
    document.querySelectorAll(selector).forEach(function (b) { b.classList.remove("is-active"); });
    if (aktiv) { aktiv.classList.add("is-active"); }
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

    fit();
    window.addEventListener("resize", fit);

    if (cfg.canWrite) {
      stage.on("mousedown touchstart", startDraw);
      stage.on("mousemove touchmove", moveDraw);
      window.addEventListener("mouseup", endDraw);
      stage.on("touchend", endDraw);
    }
    aktualisiereButtons();
    setStatus(cfg.canWrite ? "bereit" : "schreibgeschützt");
  }

  function boot() {
    bindToolbar();
    // Standard-Aktiv-Zustände setzen
    var c0 = document.querySelector('[data-anno-color]');
    var w0 = document.querySelector('[data-anno-width="9"]') || document.querySelector('[data-anno-width]');
    if (c0) { setActive("[data-anno-color]", c0); color = c0.getAttribute("data-anno-color"); }
    if (w0) { setActive("[data-anno-width]", w0); width = parseInt(w0.getAttribute("data-anno-width"), 10) || 9; }

    var img = new Image();
    img.crossOrigin = "anonymous";  // gleiche Origin, erlaubt aber sauberes toDataURL
    img.onload = function () { initStage(img); };
    img.onerror = function () { setStatus("Bild konnte nicht geladen werden"); };
    img.src = cfg.bildUrl;

    // Speichern beim Verlassen (dirty)
    window.addEventListener("pagehide", function () { if (dirty) { save(true); } });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && dirty) { save(true); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
