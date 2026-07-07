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
  var saveTimer = null, dirty = false, saving = false, lockHeartbeat = null;
  var UNDO_MAX = 60;

  // Taktische Zeichen / Flaechen (aus /static/tz/tz-manifest.json)
  var TZ = { symbole: {}, flaechen: {} };
  var imgCache = {};
  var aktSymbol = null, aktFlaeche = null;

  // Pointer/Palm/Pinch/Pan (PR4)
  var pointers = {};          // pointerId -> {x, y, type}
  var penActive = false, penTimer = null;
  var panning = false, lastPan = null, pinch = null, spaceDown = false;
  var baseScale = 1;          // Fit-Skalierung; User-Zoom liegt darueber

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
    stage.position({ x: 0, y: 0 });   // Zoom/Pan beim Resize zuruecksetzen
    baseScale = scale;
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
      if (n !== tr) { hydrate(n); n.draggable(sel); wireListeners(n); }
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
    markPickerAktiv();
  }

  // ── Auswahl / Radierer (Knoten aus der Pointer-Position ermitteln) ──────────
  function getNodeAtPointer() {
    var p = stage.getPointerPosition(); if (!p) { return null; }
    var s = stage.getIntersection(p); if (!s) { return null; }
    if (s.getLayer && s.getLayer() === imageLayer) { return null; }
    var n = s, par = n.getParent();
    if (par && par.className === "Transformer") { return "handle"; }
    while (par && par.getLayer && par.getLayer() === annLayer && par !== annLayer &&
           (par.className === "Group" || par.className === "Label")) {
      n = par; par = n.getParent();
    }
    return (n.getLayer && n.getLayer() === annLayer && n !== tr) ? n : null;
  }
  function selectAt() {
    var n = getNodeAtPointer();
    if (n === "handle") { return; }
    if (n) { n.draggable(true); attachTr(n); } else { detachTr(); }
  }
  function eraseAt() {
    var n = getNodeAtPointer();
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
  function onTextPlace(ev) {
    var p = pos();
    // Verhindert, dass der Default den Fokus auf den Canvas legt und die gerade
    // erzeugte Textarea sofort wieder blurrt (Commit-leer -> weg).
    if (ev && ev.cancelable && ev.preventDefault) { ev.preventDefault(); }
    var cx = ev && ev.clientX != null ? ev.clientX : 100;
    var cy = ev && ev.clientY != null ? ev.clientY : 100;
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

  // ── Taktische Zeichen (Symbole) + Flaechen ──────────────────────────────────
  function hatchTile(f) {
    var c = document.createElement("canvas"); c.width = 14; c.height = 14;
    var x = c.getContext("2d");
    if (f.bg) { x.fillStyle = f.bg; x.fillRect(0, 0, 14, 14); }
    x.strokeStyle = f.hatchColor || f.stroke; x.lineWidth = 3; x.lineCap = "round";
    x.beginPath();
    if (f.hatch === "horiz") {
      x.moveTo(0, 4); x.lineTo(14, 4); x.moveTo(0, 10); x.lineTo(14, 10);
    } else {  // diag
      x.moveTo(0, 14); x.lineTo(14, 0);
      x.moveTo(-4, 4); x.lineTo(4, -4);
      x.moveTo(10, 18); x.lineTo(18, 10);
    }
    x.stroke();
    return c;
  }
  function applyFlaeche(rect, f) {
    rect.stroke(f.stroke); rect.strokeWidth(3);
    rect.dash(f.dash ? [12, 7] : []);
    if (f.hatch) { rect.fillPatternImage(hatchTile(f)); rect.fillPatternRepeat("repeat"); }
    else { rect.fill(null); }
    rect.setAttr("annoType", "area"); rect.setAttr("flaeche", f.id);
  }
  function placeSymbol(item) {
    var img = imgCache[item.datei];
    if (!img) { img = new Image(); img.src = item.datei; imgCache[item.datei] = img; }
    pushUndo();
    var p = pos();
    var s = Math.max(48, Math.min(natW, natH) * 0.09);
    var node = new Konva.Image({ image: img, x: p.x - s / 2, y: p.y - s / 2, width: s, height: s });
    node.setAttr("annoType", "symbol"); node.setAttr("symbolSrc", item.datei);
    if (!img.complete) { img.onload = function () { annLayer.batchDraw(); }; }
    annLayer.add(node); wireShape(node); markDirty(); annLayer.batchDraw();
  }
  // Nach Restore/Load die nicht serialisierten Teile (Bild-Bitmap, Muster) neu setzen
  function hydrate(node) {
    var t = node.getAttr && node.getAttr("annoType");
    if (t === "symbol") {
      var src = node.getAttr("symbolSrc");
      if (src) {
        var im = imgCache[src];
        if (!im) { im = new Image(); im.src = src; imgCache[src] = im; }
        node.image(im);
        if (!im.complete) { im.onload = function () { node.image(im); annLayer.batchDraw(); }; }
      }
    } else if (t === "area") {
      var f = TZ.flaechen[node.getAttr("flaeche")];
      if (f && f.hatch) { node.fillPatternImage(hatchTile(f)); node.fillPatternRepeat("repeat"); }
    }
  }

  // ── Zeichnen (Freihand + Formen) ────────────────────────────────────────────
  function drawDown(ev) {
    if (!cfg.canWrite) { return; }
    if (tool === "select") { selectAt(); return; }
    if (tool === "eraser") { eraseAt(); return; }
    if (tool === "text") { onTextPlace(ev); return; }
    if (ev && ev.cancelable && ev.preventDefault) { ev.preventDefault(); }
    if (tool === "x") { placeX(); return; }
    if (tool === "symbol") { if (aktSymbol) { placeSymbol(aktSymbol); } return; }

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
    } else if (tool === "rect" || tool === "area") {
      shape = new Konva.Rect({ x: p.x, y: p.y, width: 0, height: 0, stroke: color, strokeWidth: width });
      if (tool === "area" && aktFlaeche) { applyFlaeche(shape, aktFlaeche); }
    } else if (tool === "ellipse") {
      shape = new Konva.Ellipse({ x: p.x, y: p.y, radiusX: 0, radiusY: 0, stroke: color, strokeWidth: width });
    }
    if (shape) { annLayer.add(shape); }
  }

  function drawMove() {
    if (!drawing || !shape) { return; }
    var p = pos();
    if (tool === "pen") {
      var pts = shape.points(); pts.push(p.x, p.y); shape.points(pts);
    } else if (tool === "line" || tool === "arrow") {
      shape.points([startPt.x, startPt.y, p.x, p.y]);
    } else if (tool === "rect" || tool === "area") {
      shape.x(Math.min(startPt.x, p.x)); shape.y(Math.min(startPt.y, p.y));
      shape.width(Math.abs(p.x - startPt.x)); shape.height(Math.abs(p.y - startPt.y));
    } else if (tool === "ellipse") {
      shape.x((startPt.x + p.x) / 2); shape.y((startPt.y + p.y) / 2);
      shape.radiusX(Math.abs(p.x - startPt.x) / 2); shape.radiusY(Math.abs(p.y - startPt.y) / 2);
    }
    annLayer.batchDraw();
  }

  function drawUp() {
    if (!drawing) { return; }
    drawing = false;
    if (shape) {
      var tooSmall = false;
      if (tool === "pen") { tooSmall = shape.points().length <= 2; }
      else if (tool === "rect" || tool === "area") { tooSmall = Math.abs(shape.width()) < 3 && Math.abs(shape.height()) < 3; }
      else if (tool === "ellipse") { tooSmall = shape.radiusX() < 2 && shape.radiusY() < 2; }
      else if (tool === "line" || tool === "arrow") {
        var pt = shape.points(); tooSmall = Math.hypot(pt[2] - pt[0], pt[3] - pt[1]) < 5;
      }
      if (tooSmall) { shape.destroy(); undoStack.pop(); aktualisiereButtons(); }
      else { wireShape(shape); markDirty(); }
    }
    shape = null; annLayer.batchDraw();
  }

  function cancelDraw() {
    if (drawing && shape) { shape.destroy(); if (undoStack.length) { undoStack.pop(); } aktualisiereButtons(); }
    drawing = false; shape = null; annLayer.batchDraw();
  }

  // ── Pointer Events (Maus/Stift/Touch) + Palm-Rejection + Pinch/Pan ──────────
  function countType(t) {
    var n = 0; for (var id in pointers) { if (pointers[id].type === t) { n++; } } return n;
  }
  function touchPts() {
    var a = []; for (var id in pointers) { if (pointers[id].type === "touch") { a.push(pointers[id]); } } return a;
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function zoomAt(clientX, clientY, factor) {
    var rect = stage.container().getBoundingClientRect();
    var px = clientX - rect.left, py = clientY - rect.top;
    var old = stage.scaleX();
    var neu = clamp(old * factor, baseScale * 0.6, baseScale * 8);
    var ref = { x: (px - stage.x()) / old, y: (py - stage.y()) / old };
    stage.scale({ x: neu, y: neu });
    stage.position({ x: px - ref.x * neu, y: py - ref.y * neu });
    stage.batchDraw();
  }
  function panBy(dx, dy) {
    stage.position({ x: stage.x() + dx, y: stage.y() + dy });
    stage.batchDraw();
  }

  function onPointerDown(ev) {
    pointers[ev.pointerId] = { x: ev.clientX, y: ev.clientY, type: ev.pointerType };
    if (ev.pointerType === "pen") { penActive = true; if (penTimer) { clearTimeout(penTimer); penTimer = null; } }
    // Palm-Rejection: bei aktivem Stift werden Touch-Punkte ignoriert
    if (ev.pointerType === "touch" && penActive) { return; }
    // Zwei-Finger-Touch -> Pinch/Pan (kein Zeichnen)
    if (ev.pointerType === "touch" && countType("touch") === 2) { cancelDraw(); startPinch(); return; }
    if (ev.pointerType === "touch" && countType("touch") > 2) { return; }
    // Desktop-Pan: mittlere Maustaste oder Leertaste+links
    if (ev.pointerType === "mouse" && (ev.button === 1 || (spaceDown && ev.button === 0))) {
      panning = true; lastPan = { x: ev.clientX, y: ev.clientY };
      if (ev.cancelable) { ev.preventDefault(); } return;
    }
    if (ev.pointerType === "mouse" && ev.button !== 0) { return; }  // nur links zeichnet
    stage.setPointersPositions(ev);
    drawDown(ev);
  }

  function onPointerMove(ev) {
    if (pointers[ev.pointerId]) { pointers[ev.pointerId] = { x: ev.clientX, y: ev.clientY, type: ev.pointerType }; }
    if (pinch && countType("touch") >= 2) { doPinch(); return; }
    if (panning) {
      panBy(ev.clientX - lastPan.x, ev.clientY - lastPan.y);
      lastPan = { x: ev.clientX, y: ev.clientY }; return;
    }
    if (ev.pointerType === "touch" && penActive) { return; }
    if (!drawing) { return; }
    if (ev.cancelable && ev.preventDefault) { ev.preventDefault(); }
    // Coalesced Events -> glatte Freihandlinien bei hoher Abtastrate
    var evs = (ev.getCoalescedEvents && ev.getCoalescedEvents().length) ? ev.getCoalescedEvents() : [ev];
    for (var i = 0; i < evs.length; i++) { stage.setPointersPositions(evs[i]); drawMove(); }
  }

  function onPointerUp(ev) {
    delete pointers[ev.pointerId];
    if (ev.pointerType === "pen") {
      if (penTimer) { clearTimeout(penTimer); }
      penTimer = setTimeout(function () { penActive = false; }, 700);
    }
    if (pinch && countType("touch") < 2) { pinch = null; }
    if (panning && ev.pointerType === "mouse") { panning = false; }
    if (drawing) { drawUp(); }
  }

  function startPinch() {
    var t = touchPts(); if (t.length < 2) { return; }
    pinch = { d: Math.hypot(t[0].x - t[1].x, t[0].y - t[1].y),
              cx: (t[0].x + t[1].x) / 2, cy: (t[0].y + t[1].y) / 2 };
  }
  function doPinch() {
    var t = touchPts(); if (t.length < 2 || !pinch) { return; }
    var d = Math.hypot(t[0].x - t[1].x, t[0].y - t[1].y);
    var cx = (t[0].x + t[1].x) / 2, cy = (t[0].y + t[1].y) / 2;
    if (pinch.d > 0) { zoomAt(cx, cy, d / pinch.d); }
    panBy(cx - pinch.cx, cy - pinch.cy);
    pinch = { d: d, cx: cx, cy: cy };
  }
  function onWheel(ev) {
    ev.preventDefault();
    zoomAt(ev.clientX, ev.clientY, ev.deltaY < 0 ? 1.12 : 0.89);
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
  function resendBuffered() {
    var b;
    try { b = sessionStorage.getItem("anno_buffer_" + cfg.saveUrl); } catch (e) { return; }
    if (!b) { return; }
    fetch(cfg.saveUrl, {
      method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": cfg.csrf }, body: b,
    }).then(function (r) {
      if (r.ok) { try { sessionStorage.removeItem("anno_buffer_" + cfg.saveUrl); } catch (e) { /* ignore */ } }
    }).catch(function () { /* beim naechsten Mal erneut */ });
  }
  function setStatus(txt) { var el = byId("anno-status"); if (el) { el.textContent = txt; } }

  // ── Soft-Lock (Heartbeat) ───────────────────────────────────────────────────
  function lockRefresh() {
    if (!cfg.canWrite || !cfg.lockUrl) { return; }
    fetch(cfg.lockUrl, { method: "POST", headers: { "X-CSRF-Token": cfg.csrf } }).catch(function () {});
  }
  function lockRelease() {
    if (!cfg.canWrite || !cfg.lockUrl) { return; }
    try { fetch(cfg.lockUrl, { method: "DELETE", headers: { "X-CSRF-Token": cfg.csrf }, keepalive: true }); } catch (e) { /* ignore */ }
  }

  // ── Toolbar ────────────────────────────────────────────────────────────────
  function setActive(selector, aktiv) {
    document.querySelectorAll(selector).forEach(function (b) { b.classList.remove("is-active"); });
    if (aktiv) { aktiv.classList.add("is-active"); }
  }
  function bindToolbar() {
    document.querySelectorAll("[data-anno-tool]").forEach(function (b) {
      b.addEventListener("click", function () { setTool(b.getAttribute("data-anno-tool")); });
    });
    document.querySelectorAll("[data-anno-panel]").forEach(function (b) {
      b.addEventListener("click", function () { togglePanel(); });
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

  // ── Picker (taktische Zeichen + Flaechen) ───────────────────────────────────
  function markPickerAktiv() {
    var b = document.querySelector('[data-anno-panel="tz"]');
    if (b) { b.classList.toggle("is-active", tool === "symbol" || tool === "area"); }
  }
  function togglePanel(show) {
    var el = byId("anno-tz"); if (!el) { return; }
    var open = (show === undefined) ? (el.style.display === "none" || !el.style.display) : show;
    el.style.display = open ? "block" : "none";
  }
  function recentAdd(kind, id) {
    var r;
    try { r = JSON.parse(localStorage.getItem("anno_tz_recent") || "[]"); } catch (e) { r = []; }
    r = r.filter(function (x) { return !(x.kind === kind && x.id === id); });
    r.unshift({ kind: kind, id: id });
    try { localStorage.setItem("anno_tz_recent", JSON.stringify(r.slice(0, 8))); } catch (e) { /* ignore */ }
  }
  function flaecheSwatch(f) {
    var c = document.createElement("canvas"); c.width = 30; c.height = 24;
    var x = c.getContext("2d");
    if (f.hatch) { x.fillStyle = x.createPattern(hatchTile(f), "repeat"); x.fillRect(0, 0, 30, 24); }
    x.strokeStyle = f.stroke; x.lineWidth = 2;
    if (f.dash) { x.setLineDash([5, 3]); }
    x.strokeRect(1, 1, 28, 22);
    return c.toDataURL();
  }
  function pickSymbol(item) { aktSymbol = item; setTool("symbol"); recentAdd("symbole", item.id); togglePanel(false); }
  function pickFlaeche(item) { aktFlaeche = item; setTool("area"); recentAdd("flaechen", item.id); togglePanel(false); }
  function itemEl(kind, item) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "anno-tz__item"; b.title = item.name;
    b.setAttribute("data-such", item.such || "");
    var media = kind === "symbole" ? item.datei : flaecheSwatch(item);
    b.innerHTML = '<img src="' + media + '" alt=""><span>' + item.name + "</span>";
    b.addEventListener("click", function () { kind === "symbole" ? pickSymbol(item) : pickFlaeche(item); });
    return b;
  }
  function renderPicker(m) {
    var el = byId("anno-tz"); if (!el) { return; }
    el.innerHTML = '<input type="text" class="anno-tz__search" placeholder="Suchen …">';
    function section(titel, kind, items) {
      var box = document.createElement("div");
      box.innerHTML = '<div class="anno-tz__h">' + titel + "</div>";
      var grid = document.createElement("div"); grid.className = "anno-tz__grid";
      items.forEach(function (it) { grid.appendChild(itemEl(kind, it)); });
      box.appendChild(grid);
      return box;
    }
    el.appendChild(section("Symbole", "symbole", m.symbole || []));
    el.appendChild(section("Flächen", "flaechen", m.flaechen || []));
    var such = el.querySelector(".anno-tz__search");
    such.addEventListener("input", function () {
      var q = such.value.toLowerCase().trim();
      el.querySelectorAll(".anno-tz__item").forEach(function (b) {
        var txt = b.title.toLowerCase() + " " + (b.getAttribute("data-such") || "");
        b.style.display = (!q || txt.indexOf(q) !== -1) ? "" : "none";
      });
    });
  }
  function loadManifest() {
    var url = cfg.tzManifest || "/static/tz/tz-manifest.json";
    fetch(url).then(function (r) { return r.json(); }).then(function (m) {
      (m.symbole || []).forEach(function (s) { TZ.symbole[s.id] = s; var im = new Image(); im.src = s.datei; imgCache[s.datei] = im; });
      (m.flaechen || []).forEach(function (f) { TZ.flaechen[f.id] = f; });
      renderPicker(m);
    }).catch(function () { /* Picker optional */ });
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
    annLayer.getChildren().forEach(function (n) { if (n !== tr) { hydrate(n); wireListeners(n); n.draggable(false); } });

    fit();
    window.addEventListener("resize", fit);

    // Native Pointer-Events (ein Pfad fuer Maus/Stift/Touch). Pan/Zoom auch im
    // Nur-Lese-Modus; Zeichnen selbst ist in drawDown per canWrite gated.
    var container = stage.container();
    container.style.touchAction = "none";
    container.addEventListener("pointerdown", onPointerDown);
    container.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    container.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("keydown", function (e) {
      var inText = document.activeElement && document.activeElement.tagName === "TEXTAREA";
      if (e.code === "Space" && !inText) { spaceDown = true; }
      if (cfg.canWrite && (e.key === "Delete" || e.key === "Backspace") && tr && tr.nodes().length && !inText) {
        e.preventDefault(); pushUndo();
        tr.nodes().forEach(function (n) { n.destroy(); });
        detachTr(); markDirty(); annLayer.batchDraw();
      }
    });
    window.addEventListener("keyup", function (e) { if (e.code === "Space") { spaceDown = false; } });
    setTool(cfg.canWrite ? "pen" : "select");
    aktualisiereButtons();
    setStatus(cfg.lockOther
      ? ("⚠ wird gerade von " + (cfg.lockName || "jemand") + " bearbeitet")
      : (cfg.canWrite ? "bereit" : "schreibgeschützt"));
  }

  function boot() {
    bindToolbar();
    loadManifest();
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

    window.addEventListener("pagehide", function () { if (dirty) { save(true); } lockRelease(); });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && dirty) { save(true); }
    });

    // Soft-Lock: Heartbeat + Offline-Puffer nachsenden (Warnung setzt initStage)
    if (cfg.canWrite) {
      resendBuffered();
      lockHeartbeat = setInterval(lockRefresh, 60000);
    }
  }

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", boot); }
  else { boot(); }
})();
