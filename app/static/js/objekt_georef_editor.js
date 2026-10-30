(function () {
  "use strict";

  function planNormiert(latlng, breite, hoehe) {
    return { x: latlng.lng / breite, y: -latlng.lat / hoehe };
  }
  function polygonFlaeche(p) {
    return Math.abs(p.reduce(function (sum, a, i) { var b = p[(i + 1) % p.length]; return sum + a.x * b.y - b.x * a.y; }, 0)) / 2;
  }
  function polygonEntfernen(p) { return p.slice(0, -1); }
  function polygonFehler(p) {
    if (p.length < 3 || p.length > 100) { return "Polygon: 3 bis 100 Punkte erforderlich."; }
    function kreuz(a, b, c) { return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x); }
    function auf(a, b, c) { return Math.min(a.x, b.x) <= c.x && c.x <= Math.max(a.x, b.x) && Math.min(a.y, b.y) <= c.y && c.y <= Math.max(a.y, b.y); }
    function schnitt(a, b, c, d) {
      var x = kreuz(a, b, c), y = kreuz(a, b, d), z = kreuz(c, d, a), w = kreuz(c, d, b);
      return (x * y < 0 && z * w < 0) || (x === 0 && auf(a, b, c)) || (y === 0 && auf(a, b, d)) || (z === 0 && auf(c, d, a)) || (w === 0 && auf(c, d, b));
    }
    if (p.some(function (a, i) { var b = p[(i + 1) % p.length]; return a.x === b.x && a.y === b.y; })) { return "Polygon: Aufeinanderfolgende Punkte dürfen nicht gleich sein."; }
    for (var i = 0; i < p.length; i++) {
      var a = p[i], b = p[(i + 1) % p.length], c = p[(i + 2) % p.length];
      if (!Number.isFinite(a.x) || !Number.isFinite(a.y) || a.x < 0 || a.x > 1 || a.y < 0 || a.y > 1) { return "Polygon: Punkte müssen innerhalb der Seite liegen."; }
      if (kreuz(a, b, c) === 0 && (a.x - b.x) * (c.x - b.x) + (a.y - b.y) * (c.y - b.y) > 0) { return "Polygon: Kanten überschneiden oder berühren sich selbst."; }
      for (var j = i + 1; j < p.length; j++) {
        if (j === i + 1 || (i === 0 && j === p.length - 1)) { continue; }
        if (schnitt(a, b, p[j], p[(j + 1) % p.length])) { return "Polygon: Kanten überschneiden oder berühren sich selbst."; }
      }
    }
    var xs = p.map(function (a) { return a.x; }), ys = p.map(function (a) { return a.y; });
    if (Math.max.apply(null, xs) - Math.min.apply(null, xs) < 0.001 || Math.max.apply(null, ys) - Math.min.apply(null, ys) < 0.001 || polygonFlaeche(p) < 0.000001) { return "Polygon: Fläche oder umschließende Box zu klein."; }
    return "";
  }
  function affineMatrix(pxTL, pxTR, pxBL, breite, hoehe) {
    return { a: (pxTR.x - pxTL.x) / breite, b: (pxTR.y - pxTL.y) / breite,
      c: (pxBL.x - pxTL.x) / hoehe, d: (pxBL.y - pxTL.y) / hoehe, e: pxTL.x, f: pxTL.y };
  }
  function naechsterSchritt(paare, pending) {
    var n = paare.length;
    if (pending) { return { text: "Ecke " + (n + 1) + " von " + Math.max(4, n + 1) + " · jetzt auf der Karte klicken", aktiv: "karte" }; }
    if (n >= 4) { return { text: "Vier Ecken erfasst · jetzt Prüfen", aktiv: "" }; }
    return { text: "Ecke " + (n + 1) + " von 4 · jetzt im Plan klicken", aktiv: "plan" };
  }
  function affineOverlay(url, ecken, groesse, deckkraft) {
    var Overlay = L.Layer.extend({
      onAdd: function (map) {
        this.bild = document.createElement("img");
        this.bild.src = url; this.bild.alt = "";
        Object.assign(this.bild.style, { position: "absolute", width: groesse[0] + "px",
          height: groesse[1] + "px", maxWidth: "none", transformOrigin: "0 0", pointerEvents: "none", opacity: String(deckkraft) });
        map.getPane("overlayPane").appendChild(this.bild);
        map.on("zoom move viewreset", this.zeichnen, this);
        this.zeichnen();
      },
      zeichnen: function () {
        var map = this._map;
        function px(e) { return map.latLngToLayerPoint([e.lat, e.lng]); }
        var m = affineMatrix(px(ecken[0]), px(ecken[1]), px(ecken[3]), groesse[0], groesse[1]);
        this.bild.style.transform = "matrix(" + [m.a, m.b, m.c, m.d, m.e, m.f].join(",") + ")";
      },
      onRemove: function (map) {
        map.off("zoom move viewreset", this.zeichnen, this); this.bild.remove();
      }
    });
    return new Overlay();
  }
  // Nur Darstellung: Die serverseitige Freigabepruefung bleibt massgeblich.
  function bspLagegenauigkeit(q) {
    if (!q) { return "Lagegenauigkeit: nach dem Speichern verfügbar"; }
    var wert = q.rmse_m;
    var bewertung = q.status !== "gueltig" ? "neu prüfen" : wert == null ? "unbekannt" :
      wert <= 0.5 ? "gut" : wert <= 2 ? "prüfen" : "ungenau";
    return "Lagegenauigkeit: " + bewertung + " - Abweichung " +
      (wert == null ? "unbekannt" : wert < 0.01 ? "unter 1 cm" : wert.toLocaleString("de-AT", { maximumFractionDigits: 2 }) + " m") +
      ", " + q.punktzahl + " Referenzpunkte";
  }
  function bspStatus(status) {
    return ({ entwurf: ["Entwurf", "muted", "file-text"], freigegeben: ["Freigegeben", "success", "circle-check"],
      veraltet: ["Veraltet", "exercise", "alert-triangle"], zurueckgezogen: ["Zurückgezogen", "muted", "archive"] })[status];
  }
  function bspBereich(opts, plan, map, planDiv, meta, rasterVorschauStart) {
    var root = document.getElementById("bsp-overlay");
    if (!root || !opts.seiteId) { return null; }
    function el(tag, text, parent) {
      var n = document.createElement(tag);
      if (text != null) { n.textContent = text; }
      parent.appendChild(n); return n;
    }
    function knopf(text, parent, fn) {
      var b = el("button", text, parent); b.type = "button";
      b.className = "btn btn--ghost"; b.onclick = fn; return b;
    }
    var basis = opts.basisUrl.replace(/\/karte$/, "/bsp-overlays");
    var meldung = el("p", "", root); meldung.className = "bsp__meldung";
    meldung.setAttribute("role", "status"); meldung.setAttribute("aria-live", "polite");
    var voraussetzung = el("div", null, root); voraussetzung.className = "card bsp__voraussetzung";
    el("h3", "Zuerst Plan georeferenzieren", voraussetzung);
    el("p", "Speichere oben eine gültige Georeferenz. Auch das aktuelle Plandokument muss freigegeben sein.", voraussetzung);
    var nachOben = el("a", "Zur Georeferenzierung oben", voraussetzung); nachOben.href = "#georef-editor";
    var listenKopf = el("div", null, root); listenKopf.className = "bsp__kopf";
    el("h3", "Vorhandene Overlays", listenKopf);
    var neu = knopf("Neues Overlay", listenKopf, function () { if (!busy) { waehlen(null); } });
    var liste = el("div", null, root); liste.className = "bsp__liste";
    var formular = el("fieldset", null, root); formular.className = "bsp__formular";
    formular.disabled = true; formular.hidden = true;
    el("legend", "Overlay bearbeiten", formular);
    var gespeichert = el("p", "", formular); gespeichert.className = "badge badge--exercise bsp__ungespeichert";
    gespeichert.setAttribute("role", "status"); gespeichert.setAttribute("data-bsp-ungespeichert", "");
    var felder = {}, ausgewaehlt = null, eintraege = [], gueltig = false, busy = false;
    var ausschnitt = { art: "seite" }, rechteck = null, raster = null, zeichnen = false, start = null;
    var polygonOffen = false, griffe = [], griff = null, griffBewegt = false, overlayKlick = false;
    function schritt(n, titel, hilfe) {
      var karte = el("section", null, formular); karte.className = "card bsp__schritt"; karte.dataset.bspSchritt = String(n);
      el("h3", n + " " + titel, karte); el("p", hilfe, karte).className = "form-hint"; return karte;
    }
    var eins = schritt(1, "Ausschnitt wählen", "Wähle die ganze Seite oder nur den benötigten Teil des Plans.");
    var cropLeiste = el("div", null, eins); cropLeiste.className = "bsp__segment";
    cropLeiste.setAttribute("role", "group"); cropLeiste.setAttribute("aria-label", "Planausschnitt");
    var ausschnittText = el("p", "Ganze Seite", eins); ausschnittText.setAttribute("aria-live", "polite");
    el("p", "Rechteck aufziehen oder Polygonpunkte im Plan oben antippen. Der erste Punkt schließt das Polygon; danach lassen sich die Eckpunkte ziehen.", eins).className = "form-hint";
    var polygonAktionen = el("div", null, eins); polygonAktionen.className = "bsp__aktionen"; polygonAktionen.hidden = true;
    var polygonHinweis = el("p", "", eins); polygonHinweis.className = "bsp__warnung"; polygonHinweis.setAttribute("role", "status");
    var zwei = schritt(2, "Angaben", "Benenne den Plan so, dass Einsatzkräfte ihn rasch erkennen.");
    var feldGruppe = el("div", null, zwei); feldGruppe.className = "bsp__felder";
    function feld(name, text, typ, optionen, hilfe, parent) {
      var label = el("label", null, parent || feldGruppe); label.className = typ === "checkbox" ? "bsp__check" : "bsp__feld";
      var titel = el("span", text, label);
      var input = el(optionen ? "select" : "input", null, label);
      if (optionen) { optionen.forEach(function (o) { var n = el("option", o[1], input); n.value = o[0]; }); }
      else { input.type = typ || "text"; }
      input.name = name; input.id = "bsp-" + name;
      if (optionen || !typ || typ === "text") { input.className = optionen ? "form-select" : "form-input"; }
      if (typ === "range") { input.min = "0.1"; input.max = "1"; input.step = "0.1";
        var prozent = el("output", "", label); prozent.htmlFor = input.id;
        input.zeigeProzent = function () { prozent.textContent = Math.round(Number(input.value) * 100) + " %"; };
        input.addEventListener("input", input.zeigeProzent);
      }
      if (hilfe) { var hint = el("span", hilfe, label); hint.className = "form-hint"; hint.id = input.id + "-hilfe"; input.setAttribute("aria-describedby", hint.id); }
      titel.className = "bsp__label";
      felder[name] = input; return input;
    }
    feld("name", "Name (Pflichtfeld)", "text", null, "Wird bei der Planauswahl im Einsatz angezeigt.").required = true;
    felder.name.placeholder = "z. B. Hallenplan - Erdgeschoß";
    feld("plan_art", "Planart", null, [["lageplan", "Lageplan"], ["hallenbereich", "Hallenbereich"],
      ["gefahrenzone", "Gefahrenzone"], ["geschoss", "Geschoss"], ["sonstig", "Sonstiger Plan"]], "Lageplan: Überblick; Hallenbereich oder Geschoss: Innenräume; Gefahrenzone: besonderer Gefahrenbereich.");
    feld("geschoss", "Geschoss (optional)", "text", null, "Zum Beispiel EG oder 1. OG.");
    feld("bereich", "Bereich (optional)", "text", null, "Zum Beispiel Halle Nord oder Lager.");
    el("h4", "Anzeige im Einsatz", zwei);
    feld("standard_sichtbar", "Standardmäßig einblenden", "checkbox", null, "Der Plan erscheint beim Öffnen der Einsatzkarte. Er kann dort ausgeblendet werden.", zwei).setAttribute("role", "switch");
    feld("standard_deckkraft", "Deckkraft", "range", null, "Weniger Deckkraft lässt die Karte unter dem Plan besser erkennen.", zwei);
    feld("empfohlen", "Als empfohlenen Plan markieren", "checkbox", null, "Kennzeichnet diesen Plan als bevorzugte Auswahl für den Einsatz.", zwei);
    var druck = el("details", null, zwei); druck.className = "bsp__druck";
    el("summary", "Druck-Einstellungen", druck);
    feld("autodruck", "Bei Alarm mitdrucken", "checkbox", null, 'Wird nur gedruckt, wenn eine Druckregel das Element "BSP-Ausschnitt" enthält.', druck);
    feld("druck_deckkraft", "Druck-Deckkraft", "range", null, "Bestimmt die Deckkraft im Ausdruck, unabhängig von der Einsatzkarte.", druck);
    felder.name.maxLength = 200; felder.bereich.maxLength = 200; felder.geschoss.maxLength = 100;
    var drei = schritt(3, "Prüfen und freigeben", "Prüfe in der Karte oben, ob Plan und Umgebung richtig übereinanderliegen.");
    var qualitaet = el("p", "", drei); qualitaet.dataset.bspQualitaet = "";
    var qualDetails = el("details", null, drei); el("summary", "Technische Georeferenz-Daten", qualDetails);
    var rohwerte = el("p", "", qualDetails);
    var warnung = el("p", "", drei); warnung.className = "bsp__warnung";
    var deckLabel = el("label", "Vorschau-Deckkraft ", drei); deckLabel.className = "bsp__feld";
    var deck = el("input", null, deckLabel); deck.type = "range"; deck.min = "10"; deck.max = "100"; deck.value = "60";
    var deckProzent = el("output", "60 %", deckLabel);
    el("span", "Ändert nur die Vorschau auf der Karte oben.", deckLabel).className = "form-hint";
    deck.oninput = function () { deckProzent.textContent = deck.value + " %"; if (raster) { raster.setOpacity(Number(deck.value) / 100); } };
    var freigabeStand = el("p", "", drei); freigabeStand.setAttribute("aria-live", "polite");
    var aktionen = el("div", null, drei); aktionen.className = "bsp__aktionen";
    function api(pfad, daten) {
      var config = { headers: { "X-CSRF-Token": opts.csrfToken } };
      if (daten !== undefined) {
        config.method = "POST"; config.headers["Content-Type"] = "application/json"; config.body = JSON.stringify(daten);
      }
      return fetch(basis + pfad, config).then(function (r) { return r.json().then(function (d) {
        if (!r.ok) { throw new Error(typeof d.detail === "string" ? d.detail : "Ungültige Eingabe"); } return d;
      }); });
    }
    function fehler(err) { meldung.textContent = err.message; meldung.className = "bsp__meldung bsp__meldung--fehler"; meldung.setAttribute("role", "alert"); }
    function sperren() { formular.disabled = busy || !gueltig; formular.hidden = !gueltig; voraussetzung.hidden = gueltig; neu.disabled = busy || !gueltig; }
    function ungespeichert() {
      gespeichert.textContent = "Nicht gespeichert"; speichern.hidden = false; speichern.className = "btn btn--primary";
      polygonPruefen();
      if (!ausgewaehlt) { return; }
      rastern.disabled = freigeben.disabled = zurueckziehen.disabled = true;
      meldung.textContent = "Änderungen zuerst als Entwurf speichern. Eine bestehende Freigabe wird dabei aufgehoben.";
    }
    Object.keys(felder).forEach(function (k) { felder[k].addEventListener("input", ungespeichert); });
    var planHinweis = el("div", "Ausschnitt markieren: Ziehe ein Rechteck im Plan. Danach Auswahl beenden.", planDiv);
    planHinweis.className = "bsp__planhinweis"; planHinweis.hidden = true; planHinweis.setAttribute("role", "status");
    function cropModus(an) {
      zeichnen = an; start = null;
      cropKnopf.setAttribute("aria-pressed", String((an && ausschnitt.art !== "polygon") || ausschnitt.art === "rechteck"));
      ganzKnopf.setAttribute("aria-pressed", String(!an && ausschnitt.art === "seite"));
      planDiv.classList.toggle("bsp__markieren", an);
      polygonKnopf.setAttribute("aria-pressed", String(ausschnitt.art === "polygon"));
      planHinweis.textContent = ausschnitt.art === "polygon" ? "Polygon: Punkte antippen. Ersten Punkt antippen oder Polygon abschließen wählen." : "Ausschnitt markieren: Ziehe ein Rechteck im Plan. Danach Auswahl beenden.";
      planHinweis.hidden = !an; ende.hidden = !an || ausschnitt.art === "polygon";
      if (an) { planDiv.scrollIntoView({ block: "center", behavior: "smooth" }); }
      if (an) { plan.dragging.disable(); plan.touchZoom.disable(); plan.doubleClickZoom.disable(); }
      else { plan.dragging.enable(); plan.touchZoom.enable(); plan.doubleClickZoom.disable(); }
      planDiv.style.touchAction = an ? "none" : "";
    }
    function zeigeAusschnitt() {
      if (rechteck) { rechteck.remove(); rechteck = null; }
      griffe.forEach(function (g) { g.remove(); }); griffe = [];
      polygonAktionen.hidden = ausschnitt.art !== "polygon";
      if (ausschnitt.art === "polygon") {
        var punkte = ausschnitt.punkte;
        ausschnittText.textContent = "Polygon mit " + punkte.length + " Punkten, ca. " + Math.round(polygonFlaeche(punkte) * 100) + " % der Seite";
        var ll = punkte.map(function (p) { return [-p.y * meta.bildgroesse[1], p.x * meta.bildgroesse[0]]; });
        if (ll.length > 1) { rechteck = (polygonOffen ? L.polyline(ll, { interactive: false }) : L.polygon(ll, { interactive: false })).addTo(plan); }
        ll.forEach(function (p, i) {
          var g = L.marker(p, { icon: L.divIcon({ className: "bsp__polygon-griff", html: "", iconSize: [44, 44], iconAnchor: [22, 22] }), keyboard: false, bubblingMouseEvents: false }).addTo(plan);
          g.getElement().dataset.bspPunkt = String(i);
          g.getElement().setAttribute("aria-label", "Polygonpunkt " + (i + 1) + (i === 0 && polygonOffen ? " - Polygon schließen" : " - zum Korrigieren ziehen"));
          griffe.push(g);
        });
        polygonPruefen(); return;
      }
      polygonHinweis.textContent = ""; speichern.disabled = false;
      ausschnittText.textContent = ausschnitt.art === "seite" ? "Ganze Seite" : "Ausschnitt: " + Math.round((ausschnitt.x1 - ausschnitt.x0) * 100) + " % Breite × " + Math.round((ausschnitt.y1 - ausschnitt.y0) * 100) + " % Höhe der Seite";
      if (ausschnitt.art === "rechteck") {
        rechteck = L.rectangle([[-ausschnitt.y1 * meta.bildgroesse[1], ausschnitt.x0 * meta.bildgroesse[0]],
          [-ausschnitt.y0 * meta.bildgroesse[1], ausschnitt.x1 * meta.bildgroesse[0]]], { interactive: false }).addTo(plan);
      }
    }
    var ganzKnopf = knopf("Ganze Seite", cropLeiste, function () {
      ausschnitt = { art: "seite" }; cropModus(false); zeigeAusschnitt(); ungespeichert();
    });
    var cropKnopf = knopf("Rechteck", cropLeiste, function () {
      var warPolygon = ausschnitt.art === "polygon";
      if (warPolygon) { ausschnitt = { art: "seite" }; polygonOffen = false; zeigeAusschnitt(); ungespeichert(); }
      cropModus(warPolygon || !zeichnen);
      if (zeichnen) { meldung.textContent = "Im Seitenbild ein Rechteck aufziehen, danach Auswahl beenden. Mit Finger oder Maus möglich."; }
    });
    var polygonKnopf = knopf("Polygon", cropLeiste, function () { polygonNeu(); });
    function polygonNeu() {
      ausschnitt = { art: "polygon", punkte: [] }; polygonOffen = true;
      cropModus(true); zeigeAusschnitt(); ungespeichert();
    }
    function polygonPruefen() {
      var problem = ausschnitt.art === "polygon" ? polygonFehler(ausschnitt.punkte) : "";
      polygonHinweis.textContent = problem || (polygonOffen && ausschnitt.art === "polygon" ? "Ersten Punkt antippen oder Polygon abschließen wählen." : "");
      if (ausschnitt.art === "polygon") {
        planHinweis.textContent = problem || "Polygon: Punkte antippen. Ersten Punkt antippen oder Polygon abschließen wählen.";
        planHinweis.hidden = !problem && !zeichnen;
      }
      if (speichern) { speichern.disabled = !!problem || (ausschnitt.art === "polygon" && polygonOffen); }
      if (abschliessen) { abschliessen.disabled = !!problem || !polygonOffen; }
      if (entfernen) { entfernen.disabled = ausschnitt.art !== "polygon" || !ausschnitt.punkte.length; }
    }
    function polygonAbschluss() {
      if (polygonFehler(ausschnitt.punkte)) { polygonPruefen(); return; }
      polygonOffen = false; cropModus(false); zeigeAusschnitt(); ungespeichert();
    }
    var entfernen = knopf("Letzten Punkt entfernen", polygonAktionen, function () {
      ausschnitt.punkte = polygonEntfernen(ausschnitt.punkte); polygonOffen = true;
      cropModus(true); zeigeAusschnitt(); ungespeichert();
    });
    var abschliessen = knopf("Polygon abschließen", polygonAktionen, polygonAbschluss);
    knopf("Neu beginnen", polygonAktionen, polygonNeu);
    var ende = knopf("Auswahl beenden", eins, function () { cropModus(false); }); ende.hidden = true;
    knopf("Ausschnitt zurücksetzen", eins, function () { ausschnitt = { art: "seite" }; cropModus(false); zeigeAusschnitt(); ungespeichert(); });
    function xy(ev) {
      var p = planNormiert(plan.mouseEventToLatLng(ev), meta.bildgroesse[0], meta.bildgroesse[1]);
      return { x: Math.max(0, Math.min(1, p.x)), y: Math.max(0, Math.min(1, p.y)) };
    }
    planDiv.addEventListener("pointerdown", function (ev) {
      if (busy || !gueltig || ev.isPrimary === false || ev.target.closest(".leaflet-control")) { return; }
      var ziel = ev.target.closest("[data-bsp-punkt]");
      overlayKlick = zeichnen || (ausschnitt.art === "polygon" && !!ziel);
      if (ausschnitt.art === "polygon" && (zeichnen || ziel)) {
        ev.preventDefault(); ev.stopPropagation();
        if (ziel) {
          griff = Number(ziel.dataset.bspPunkt); griffBewegt = false;
          plan.dragging.disable(); plan.touchZoom.disable(); plan.doubleClickZoom.disable();
        } else if (polygonOffen && ausschnitt.punkte.length < 100) {
          ausschnitt.punkte.push(xy(ev)); zeigeAusschnitt(); ungespeichert();
        }
        planDiv.setPointerCapture(ev.pointerId); return;
      }
      if (!zeichnen) { return; }
      ev.preventDefault(); ev.stopPropagation(); start = xy(ev); planDiv.setPointerCapture(ev.pointerId);
    }, true);
    // Pointer-Capture kann den anschliessenden Klick auf den Plan umleiten.
    // Auch beim Schliessen darf dadurch kein Georef-Punkt entstehen.
    planDiv.addEventListener("click", function (ev) {
      if (overlayKlick || zeichnen || ev.target.closest("[data-bsp-punkt]")) {
        ev.preventDefault(); ev.stopPropagation(); overlayKlick = false;
      }
    }, true);
    function bewegen(ev) {
      if (griff !== null && ausschnitt.art === "polygon") {
        ev.preventDefault(); ev.stopPropagation();
        if (!polygonOffen) { ausschnitt.punkte[griff] = xy(ev); griffBewegt = true; zeigeAusschnitt(); ungespeichert(); }
        return;
      }
      if (!start) { return; }
      ev.preventDefault(); ev.stopPropagation(); var p = xy(ev);
      ausschnitt = { art: "rechteck", x0: Math.min(start.x, p.x), y0: Math.min(start.y, p.y),
        x1: Math.max(start.x, p.x), y1: Math.max(start.y, p.y) }; zeigeAusschnitt(); ungespeichert();
    }
    planDiv.addEventListener("pointermove", bewegen, true);
    planDiv.addEventListener("pointerup", function (ev) {
      if (ausschnitt.art === "polygon" && (zeichnen || griff !== null)) {
        ev.preventDefault(); ev.stopPropagation();
        var schliessen = polygonOffen && griff === 0 && !griffBewegt;
        griff = null; planDiv.releasePointerCapture(ev.pointerId);
        if (schliessen) { polygonAbschluss(); }
        if (!polygonOffen) { cropModus(false); }
        return;
      }
      if (!start) { return; } bewegen(ev); start = null;
      planDiv.releasePointerCapture(ev.pointerId);
    }, true);
    planDiv.addEventListener("pointercancel", function () { start = null; griff = null; cropModus(false); });
    function waehlen(o) {
      ausgewaehlt = o || null; cropModus(false);
      var werte = o || { name: "BSP-Overlay", plan_art: "lageplan", standard_sichtbar: true,
        standard_deckkraft: 0.6, druck_deckkraft: 0.6 };
      Object.keys(felder).forEach(function (k) {
        if (felder[k].type === "checkbox") { felder[k].checked = !!werte[k]; }
        else { felder[k].value = werte[k] == null ? "" : werte[k]; }
        if (felder[k].zeigeProzent) { felder[k].zeigeProzent(); }
      });
      polygonOffen = false; ausschnitt = o ? JSON.parse(JSON.stringify(o.ausschnitt)) : { art: "seite" }; cropModus(false); zeigeAusschnitt();
      if (raster) { raster.remove(); raster = null; }
      if (o && o.raster_url && o.bounds) {
        rasterVorschauStart();
        deck.value = String(o.standard_deckkraft * 100);
        raster = L.imageOverlay(o.raster_url, o.bounds, { opacity: Number(deck.value) / 100, interactive: false }).addTo(map);
        raster.on("error", function () { if (raster) { raster.remove(); raster = null; } fehler(new Error("Raster-Vorschau nicht verfügbar. Bitte erneut speichern und Vorschau prüfen.")); });
        map.fitBounds(o.bounds);
      }
      var q = o && o.qualitaet;
      qualitaet.textContent = bspLagegenauigkeit(q);
      rohwerte.textContent = q ? "RMSE: " + meter(q.rmse_m) + " · " + q.punktzahl + " Punkte · Modell: " + q.modell +
        " · Revision " + q.revision + " · Dokument-Version " + o.dokument_version + " · Freigabe-Nr. " + o.freigabe_nr : "Noch kein gespeicherter Entwurf.";
      qualitaet.title = rohwerte.textContent;
      warnung.textContent = o ? o.warnungen.map(warnText).join(" ") : "";
      gespeichert.textContent = "";
      deckProzent.textContent = deck.value + " %";
      freigabeStand.textContent = o && o.status === "freigegeben" ? "Im Einsatz sichtbar · Freigabe-Nr. " + o.freigabe_nr :
        o && o.status === "veraltet" ? "Plan oder Georeferenz wurde geändert - neu prüfen" : "Erst nach der Freigabe im Einsatz sichtbar.";
      speichern.hidden = !!o && (o.status === "freigegeben" || o.status === "veraltet");
      rastern.className = "btn btn--primary";
      speichern.className = "btn " + (o && o.raster_url && o.status === "entwurf" ? "btn--ghost" : "btn--primary");
      freigeben.hidden = !o || o.status !== "entwurf" || !o.raster_url;
      freigeben.className = "btn btn--primary";
      rastern.hidden = !o || o.status !== "veraltet";
      zurueckziehen.hidden = !o || o.status !== "freigegeben";
      loeschen.hidden = !o || o.status !== "entwurf";
      liste.querySelectorAll("[data-overlay-id]").forEach(function (b) { b.setAttribute("aria-pressed", String(!!o && Number(b.dataset.overlayId) === o.id)); });
      rastern.disabled = freigeben.disabled = zurueckziehen.disabled = loeschen.disabled = !o;
      if (o) { loeschen.disabled = o.status !== "entwurf"; }
    }
    function meter(n) { return n == null ? "unbekannt" : n.toFixed(2) + " m"; }
    function laden(id) {
      return api("?seite_id=" + opts.seiteId).then(function (d) {
        gueltig = d.georef_gueltig; eintraege = d.overlays; liste.replaceChildren();
        meldung.textContent = ""; meldung.className = "bsp__meldung"; meldung.setAttribute("role", "status");
        if (!eintraege.length) {
          var leer = el("div", null, liste); leer.className = "card bsp__leer"; leer.dataset.bspLeer = "";
          el("strong", "Noch kein Overlay vorhanden", leer);
          el("p", "Mit Neues Overlay bereitest du diesen Plan für die Einsatzkarte vor. Erst die Freigabe macht ihn im Einsatz sichtbar.", leer);
        }
        eintraege.forEach(function (o) {
          var row = knopf("", liste, function () { waehlen(o); }); row.className = "card bsp__overlay"; row.dataset.overlayId = o.id;
          var stand = bspStatus(o.status);
          var chip = el("span", null, row); chip.className = "badge badge--" + stand[1]; chip.dataset.bspStatus = o.status;
          var symbol = el("span", null, chip); symbol.insertAdjacentHTML("afterbegin", ecpIcon(stand[2], { size: "sm" }));
          el("span", stand[0], chip);
          el("strong", o.name, row);
          el("span", [felder.plan_art.options[Array.from(felder.plan_art.options).findIndex(function (v) { return v.value === o.plan_art; })].text, o.geschoss, o.bereich].filter(Boolean).join(" · "), row);
          el("span", o.freigabe_nr ? "Freigabestand: Nr. " + o.freigabe_nr : "Noch nicht freigegeben", row).className = "form-hint";
          if (o.status === "veraltet") { el("span", "Plan oder Georeferenz wurde geändert - neu prüfen", row).className = "bsp__warnung"; }
        });
        waehlen(eintraege.find(function (o) { return o.id === id; })); sperren();
      });
    }
    function ausfuehren(pfad, daten, vorschau) {
      if (busy) { return Promise.resolve(); }
      busy = true; cropModus(false); sperren();
      return api(pfad, daten).then(function (d) {
        return (vorschau ? api("/" + d.id + "/rastern", {}) : Promise.resolve(d)).then(function () { return laden(d.id); });
      }).catch(fehler)
        .finally(function () { busy = false; sperren(); });
    }
    function daten() {
      var d = { ausschnitt: ausschnitt, expected_rotation: meta.rotation };
      Object.keys(felder).forEach(function (k) {
        var input = felder[k]; d[k] = input.type === "checkbox" ? input.checked : (input.type === "number" || input.type === "range") ? Number(input.value) : input.value;
      }); return d;
    }
    var speichern = knopf("Speichern und Vorschau", aktionen, function () {
      if (ausschnitt.art === "polygon" && (polygonOffen || polygonFehler(ausschnitt.punkte))) { polygonPruefen(); return; }
      if (!formular.reportValidity()) { return; }
      var d = daten();
      if (!ausgewaehlt) { d.seite_id = opts.seiteId; }
      ausfuehren(ausgewaehlt ? "/" + ausgewaehlt.id + "/aendern" : "", d, true);
    });
    var rastern = knopf("Neu prüfen", aktionen, function () { speichern.onclick(); });
    var freigeben = knopf("Freigeben", aktionen, function () {
      // Unmittelbar vor der Bestaetigung Qualitaet und Warnungen erneut vom Server holen.
      api("?seite_id=" + opts.seiteId).then(function (d) {
        var o = d.overlays.find(function (item) { return item.id === ausgewaehlt.id; });
        if (!o || !o.georef_gueltig) { throw new Error("Gültige Georeferenz erforderlich"); }
        var dialog = el("dialog", null, root); dialog.className = "bsp__freigabe";
        var titel = el("h3", "Plan für den Einsatz freigeben", dialog); titel.id = "bsp-freigabe-titel";
        dialog.setAttribute("aria-labelledby", titel.id);
        el("p", o.name + " · " + o.plan_art + (o.geschoss ? " · " + o.geschoss : ""), dialog);
        el("p", bspLagegenauigkeit(o.qualitaet), dialog);
        el("p", (o.ausschnitt.art === "seite" ? "Ausschnitt: Ganze Seite. " : o.ausschnitt.art === "polygon" ? "Ausschnitt: Polygon mit " + o.ausschnitt.punkte.length + " Punkten. " : "Ausschnitt: markiertes Rechteck. ") + "Du gibst diesen Plan für die Einsatzkarte frei. " + (o.standard_sichtbar ? "Er wird standardmäßig eingeblendet." : "Er kann im Einsatz eingeblendet werden.") +
          " Deckkraft: " + Math.round(o.standard_deckkraft * 100) + " %.", dialog);
        var checks = o.warnungen.map(function (code) {
          var label = el("label", null, dialog); var input = el("input", null, label); input.type = "checkbox";
          el("span", warnText(code) + " Ich habe die Lage auf der Karte geprüft.", label);
          return { code: code, input: input };
        });
        var bestaetigen = knopf("Verbindlich freigeben", dialog, function () {
          dialog.close(); ausfuehren("/" + o.id + "/freigeben", { bestaetigte_warnungen: checks.map(function (c) { return c.code; }) });
        });
        bestaetigen.className = "btn btn--primary";
        function pruefen() { bestaetigen.disabled = checks.some(function (c) { return !c.input.checked; }); }
        checks.forEach(function (c) { c.input.onchange = pruefen; }); pruefen();
        knopf("Abbrechen", dialog, function () { dialog.close(); });
        dialog.addEventListener("close", function () { dialog.remove(); }); dialog.showModal();
      }).catch(fehler);
    });
    var zurueckziehen = knopf("Zurückziehen", aktionen, function () { ausfuehren("/" + ausgewaehlt.id + "/zurueckziehen", {}); });
    var loeschen = knopf("Entwurf löschen", aktionen, function () { ausfuehren("/" + ausgewaehlt.id + "/loeschen", {}); });
    loeschen.className = "btn btn--ghost btn--xs bsp__loeschen";
    function warnText(code) { return code === "wenige_punkte" ? "Nur drei Referenzpunkte: Die Lage ist weniger gut abgesichert." : "Alle Punkte wurden frei auf der Karte gesetzt. Es fehlen eingerastete Gebäudeecken."; }
    laden().catch(fehler);
    return { zeichnet: function () { return zeichnen || griff !== null; }, aktualisieren: function () {
      cropModus(false); return laden(ausgewaehlt && ausgewaehlt.id).catch(fehler);
    } };
  }
  function open(opts) {
    var seitenModus = opts.modus === "seite" || !!opts.container;
    var dialog = seitenModus ? opts.container : document.createElement("dialog");
    dialog.className = seitenModus ? "ogx" : "ogx oks-georef";
    dialog.setAttribute("aria-label", "Georeferenz der Planseite bearbeiten");
    if (!seitenModus) { (opts.root || document.body).appendChild(dialog); }
    function anfrage(pfad, daten) {
      var options = { headers: { "X-CSRF-Token": opts.csrfToken || "" } };
      if (daten !== undefined) {
        options.method = "POST"; options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(daten);
      }
      return fetch(opts.basisUrl + pfad, options).then(function (r) {
        return r.text().then(function (text) {
          var d;
          try { d = JSON.parse(text); } catch (_) { d = { detail: text }; }
          if (!r.ok) {
            var detail = d.detail || d;
            var meldung = typeof detail === "string" ? detail : Array.isArray(detail) ?
              detail.map(function (fehler) { return fehler.msg || fehler.meldung || "Ungültige Eingabe"; }).join(" · ") :
              detail.meldung || detail.message;
            throw new Error(meldung || "Anfrage fehlgeschlagen (" + r.status + ").");
          }
          return d;
        });
      });
    }
    var get = opts.get || function (pfad) { return anfrage(pfad); };
    var post = opts.post || anfrage;
    function el(tag, text, parent, klasse) {
      var n = document.createElement(tag);
      if (text != null) { n.textContent = text; }
      if (klasse) { n.className = klasse; }
      parent.appendChild(n); return n;
    }
    function button(text, fn) {
      var b = el("button", text, aktionen, "btn btn--ghost btn--xs");
      b.type = "button"; b.onclick = fn; return b;
    }
    if (!seitenModus) { el("h3", "Georeferenz · Seite " + opts.seite, dialog); }
    var status = el("p", "Plan wird geladen ...", dialog, "ogx__status");
    status.setAttribute("role", "status");
    var hinweise = el("ul", null, dialog, "ogx__hinweise");
    hinweise.setAttribute("role", "status");
    var quelleStatus = el("p", "", dialog, "ogx__meta");
    var netz = el("p", "", dialog, "ogx__meta");
    el("p", "Zuerst im Plan klicken, dann den Gegenpunkt auf der Karte wählen. Gebäudeecken rasten ein. Nummerierte Punkte können verschoben werden.", dialog, "ogx__hilfe");
    var anweisung = el("p", "Punkt im Plan wählen.", dialog, "ogx__anweisung");
    anweisung.setAttribute("aria-live", "polite");
    var leiste = el("div", null, dialog, "ogx__leiste");
    var modellLabel = el("label", "Transformationsmodell ", leiste, "ogx__feld");
    var modell = el("select", null, modellLabel, "form-select");
    [["aehnlichkeit", "Ähnlichkeit (ab 3 Paaren)"], ["affin", "Affin (ab 4 Paaren)"],
      ["homographie", "Homographie (ab 6 Paaren)"]].forEach(function (item) {
      var o = el("option", item[1], modell); o.value = item[0];
    });
    var layout = el("div", null, dialog, "ogx__layout");
    var planDiv = el("div", null, layout, "ogx__karte");
    planDiv.setAttribute("aria-label", "Planseite mit Zoom und Verschieben");
    var mapDiv = el("div", null, layout, "ogx__karte");
    mapDiv.setAttribute("aria-label", "Karte mit Referenzpunkten");
    var liste = el("div", null, dialog, "ogx__paare");
    var aktionen = el("div", null, dialog, "ogx__aktionen");
    var paare = [], pending = null, plan, map, meta, revision = 0, generation = 0, offen = true;
    var marker = [], vorgemerkt = null, saving = false, hatGeoref = false;
    var vorschau = null, ecken = null, erstmals = true, bsp = null;
    var overlayLabel = el("label", "Plan über Karte legen ", leiste, "ogx__feld ogx__feld--check");
    var overlayAn = el("input", null, overlayLabel); overlayAn.type = "checkbox";
    var deckLabel = el("label", "Deckkraft ", leiste, "ogx__feld");
    var deckkraft = el("input", null, deckLabel); deckkraft.type = "range";
    deckkraft.min = "0"; deckkraft.max = "100"; deckkraft.value = "50";
    overlayAn.onchange = zeigeVorschau; deckkraft.oninput = zeigeVorschau;
    button("Prüfen", pruefen);
    var weitere = button("Weitere Punkte hinzufügen", function () { zusatz = true; schritt(); });
    var zusatz = false;
    var zurueck = button("Letztes Paar entfernen", letztesEntfernen);
    document.addEventListener("keydown", tastatur);
    function abbrechen() {
      if (saving || !pending) { return; }
      pending = null;
      if (vorgemerkt) { vorgemerkt.remove(); vorgemerkt = null; }
      pruefen();
    }
    function letztesEntfernen() {
      if (saving || !paare.length) { return; }
      paare.pop(); pruefen();
    }
    function tastatur(ev) {
      if (ev.key === "Escape" && pending) { ev.preventDefault(); abbrechen(); }
      var ziel = ev.target;
      if (ziel && (/^(INPUT|SELECT|TEXTAREA)$/i.test(ziel.tagName) || ziel.isContentEditable)) { return; }
      if (ev.key === "Backspace" && paare.length) { ev.preventDefault(); letztesEntfernen(); }
    }
    function schritt() {
      var s = naechsterSchritt(paare, pending);
      anweisung.textContent = s.text;
      var aktiv = s.aktiv || (zusatz ? "plan" : "");
      if (zusatz && !pending && paare.length >= 4) { anweisung.textContent = "Weiteren Punkt im Plan wählen."; }
      // classList statt className: Leaflet setzt am Container eigene Klassen (leaflet-container).
      planDiv.classList.toggle("ogx__aktiv", aktiv === "plan");
      mapDiv.classList.toggle("ogx__aktiv", aktiv === "karte");
      weitere.hidden = paare.length < 4 || !!pending;
      zurueck.disabled = saving || !paare.length;
    }
    function entferneVorschau() { if (vorschau) { vorschau.remove(); vorschau = null; } }
    function zeigeVorschau() {
      entferneVorschau();
      if (ecken && overlayAn.checked) {
        vorschau = affineOverlay(meta.seitenbild_url, ecken, meta.bildgroesse, Number(deckkraft.value) / 100).addTo(map);
      }
    }
    var speichern = button("Speichern", function () {
      if (saving || speichern.disabled || !meta) { return; }
      speichern.disabled = true; saving = true; generation++;
      modell.disabled = true; verwerfen.disabled = true;
      post("/georef", payload(false)).then(function (antwort) {
        revision = antwort.georeferenz ? antwort.georeferenz.revision : revision + 1;
        hatGeoref = true;
        if (bsp) { bsp.aktualisieren(); }
        status.textContent = "Georeferenz gespeichert. Vorschläge wurden neu berechnet.";
        return opts.onGespeichert ? opts.onGespeichert() : null;
      }).then(function () {
        if (seitenModus) {
          status.textContent = "Georeferenz gespeichert";
          var zurueckLink = el("a", " · Zurück zur Dokumentenseite", status);
          zurueckLink.href = opts.zurueckUrl || opts.basisUrl.replace(/\/karte$/, "");
        } else { dialog.close(); }
      }).catch(fehler).finally(function () { saving = false; modell.disabled = false; verwerfen.disabled = !hatGeoref; schritt(); });
    });
    speichern.disabled = true;
    var verwerfen = button("Georeferenz verwerfen", function () {
      if (saving || !meta) { return; }
      speichern.disabled = true; saving = true; generation++;
      modell.disabled = true; verwerfen.disabled = true;
      post("/georef/verwerfen", payload(false)).then(function (antwort) {
        if (antwort.georeferenz) { revision = antwort.georeferenz.revision; }
        hatGeoref = false; if (bsp) { bsp.aktualisieren(); } ecken = null; entferneVorschau(); hinweise.replaceChildren();
        return opts.onGespeichert ? opts.onGespeichert() : null;
      }).then(function () {
        if (seitenModus) { paare = []; pending = null; if (vorgemerkt) { vorgemerkt.remove(); vorgemerkt = null; } verwerfen.disabled = true; render(); status.textContent = "Georeferenz verworfen"; } else { dialog.close(); }
      }).catch(fehler).finally(function () { saving = false; modell.disabled = false; verwerfen.disabled = !hatGeoref; schritt(); });
    });
    verwerfen.disabled = true;
    if (!seitenModus) { button("Schließen", schliessen); }
    function aufraeumen() {
      offen = false; generation++;
      document.removeEventListener("keydown", tastatur);
      if (plan) { plan.remove(); } if (map) { map.remove(); }
      if (!seitenModus) { dialog.remove(); }
      if (opts.onSchliessen) { opts.onSchliessen(); }
    }
    function schliessen() {
      if (saving) { return; }
      if (seitenModus) { aufraeumen(); dialog.replaceChildren(); } else { dialog.close(); }
    }
    if (!seitenModus) {
      dialog.addEventListener("close", aufraeumen);
      dialog.addEventListener("cancel", function (ev) {
        if (saving || pending) { ev.preventDefault(); abbrechen(); }
      });
    }
    function fehler(err) { if (offen) { status.textContent = err.message; speichern.disabled = true; } }
    function payload(pruefen) {
      return { dokument_id: opts.dokumentId, seite: opts.seite, punkte: paare,
        modell: modell.value, pruefen: pruefen, expected_revision: revision, expected_rotation: meta.rotation };
    }
    function guete(node, wert, grenze) {
      node.className = wert != null && wert <= grenze ? "ogx__gut" : "ogx__fehler";
    }
    function pruefen() {
      if (saving) { return; }
      if (!meta) { return; }
      ecken = null; entferneVorschau(); hinweise.replaceChildren();
      render();
      var lauf = ++generation;
      speichern.disabled = true;
      status.textContent = "Referenzpunkte werden geprüft ...";
      post("/georef", payload(true)).then(function (antwort) {
        if (!offen || lauf !== generation) { return; }
        var g = antwort.georeferenz;
        (g.hinweise || []).forEach(function (h) { el("li", h.text, hinweise); });
        if (g.status === "gueltig" && g.vorschau_ecken && g.vorschau_ecken.length === 4) {
          ecken = g.vorschau_ecken;
          if (erstmals) { overlayAn.checked = true; erstmals = false; }
          zeigeVorschau();
        }
        status.replaceChildren();
        el("span", g.status + " · ", status);
        var rmse = el("span", "RMSE: " + meter(g.rmse_m), status);
        guete(rmse, g.rmse_m, meta.schwellen.rmse_m);
        el("span", " · ", status);
        var maximum = el("span", "Maximum: " + meter(g.max_fehler_m), status);
        guete(maximum, g.max_fehler_m, meta.schwellen.max_fehler_m);
        if (g.ablehngrund) { el("span", " · " + g.ablehngrund, status); }
        guete(status, g.status === "gueltig" ? 0 : null, 0);
        (g.residuen || []).forEach(function (r) {
          var zeile = liste.querySelector('[data-punkt="' + String(r.id).replace(/[^0-9]/g, "") + '"]');
          if (zeile) {
            var rest = el("span", " Residuum: " + meter(r.fehler_m), zeile);
            guete(rest, r.fehler_m, meta.schwellen.max_fehler_m);
          }
        });
        speichern.disabled = g.status !== "gueltig" || pending != null || saving;
      }).catch(function (err) { if (lauf === generation) { fehler(err); } });
    }
    function meter(n) { return n == null ? "unbekannt" : n.toFixed(2) + " m"; }
    function nummer(n) {
      var span = document.createElement("span"); span.textContent = String(n);
      return L.divIcon({ html: span, className: "ogx__nummer", iconSize: [24, 24] });
    }
    function render() {
      schritt();
      marker.forEach(function (m) { m.remove(); }); marker = [];
      liste.replaceChildren();
      paare.forEach(function (p, i) {
        var a = L.marker([-p.y * meta.bildgroesse[1], p.x * meta.bildgroesse[0]],
          { draggable: true, icon: nummer(i + 1) }).addTo(plan);
        var b = L.marker([p.lat, p.lng], { draggable: true, icon: nummer(i + 1) }).addTo(map);
        marker.push(a, b);
        a.on("dragend", function () {
          if (saving) { render(); return; }
          var xy = planNormiert(a.getLatLng(), meta.bildgroesse[0], meta.bildgroesse[1]);
          if (xy.x >= 0 && xy.x <= 1 && xy.y >= 0 && xy.y <= 1) { p.x = xy.x; p.y = xy.y; }
          pruefen();
        });
        b.on("dragend", function () {
          if (saving) { render(); return; }
          var pos = b.getLatLng(); p.lat = pos.lat; p.lng = pos.lng;
          p.quelle = map.hasLayer(ortho) ? "orthofoto_klick" : "manuell";
          p.beschreibung = "Verschobener Kartenpunkt"; pruefen();
        });
        var row = el("p", (i + 1) + ": " + p.quelle + " · " + p.beschreibung + " ", liste, "ogx__paar");
        row.dataset.punkt = String(p.id);
        var del = el("button", "Paar löschen", row, "btn btn--ghost btn--xs");
        del.type = "button"; del.onclick = function () { if (saving) { return; } paare.splice(i, 1); pruefen(); };
      });
    }
    function gegenpunkt(pos, quelle, beschreibung) {
      if (!pending || saving) { return; }
      var id = paare.reduce(function (m, p) { return Math.max(m, Number(p.id) || 0); }, 0) + 1;
      paare.push({ id: id, x: pending.x, y: pending.y, lat: pos.lat, lng: pos.lng,
        quelle: quelle, beschreibung: beschreibung });
      pending = null; if (vorgemerkt) { vorgemerkt.remove(); vorgemerkt = null; }
      zusatz = false; schritt();
      pruefen();
    }
    var ortho;
    schritt();
    if (!seitenModus) { dialog.showModal(); }
    get("/georef.json?dokument_id=" + opts.dokumentId + "&seite=" + opts.seite).then(function (daten) {
      if (!offen) { return; }
      meta = daten;
      var g = daten.georeferenz;
      hatGeoref = !!g;
      revision = g ? g.revision : 0;
      modell.value = g ? g.modell : "aehnlichkeit";
      // Veraltete Koordinaten duerfen nach einer Drehung nicht wieder gespeichert werden.
      paare = g && g.status !== "veraltet" ? g.referenzpunkte.map(function (p, i) { return Object.assign({}, p, { id: i + 1 }); }) : [];
      quelleStatus.textContent = g ? "Gespeicherter Satz: " + g.status +
        (g.ablehngrund ? " · " + g.ablehngrund : "") : "Noch keine Georeferenz.";
      status.textContent = "Punktpaare erfassen und prüfen.";
      if (g && g.status === "veraltet") {
        quelleStatus.textContent += " · Bildkoordinaten neu erfassen.";
        quelleStatus.className = "ogx__fehler";
      }
      plan = L.map(planDiv, { crs: L.CRS.Simple, minZoom: -5 });
      var bounds = [[-meta.bildgroesse[1], 0], [0, meta.bildgroesse[0]]];
      L.imageOverlay(meta.seitenbild_url, bounds).addTo(plan); plan.fitBounds(bounds);
      map = L.map(mapDiv).setView(opts.startCenter || [47.5, 14.5], opts.startZoom || 18);
      var osm = window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
      ortho = L.tileLayer("https://mapsneu.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg",
        { maxZoom: 20, attribution: "Datenquelle: basemap.at" });
      if (osm) { osm.remove(); }
      ortho.addTo(map);
      L.control.layers({ OpenStreetMap: osm, Orthofoto: ortho }).addTo(map);
      if (seitenModus) {
        bsp = bspBereich(opts, plan, map, planDiv, meta, function () {
          overlayAn.checked = false; erstmals = false; entferneVorschau();
        });
      }
      plan.on("click", function (ev) {
        if (bsp && bsp.zeichnet()) { return; }
        if (saving) { return; }
        var xy = planNormiert(ev.latlng, meta.bildgroesse[0], meta.bildgroesse[1]);
        if (xy.x < 0 || xy.x > 1 || xy.y < 0 || xy.y > 1) { return; }
        pending = xy; generation++; speichern.disabled = true;
        ecken = null; entferneVorschau(); hinweise.replaceChildren();
        if (vorgemerkt) { vorgemerkt.remove(); }
        vorgemerkt = L.circleMarker(ev.latlng, { radius: 7 }).addTo(plan);
        schritt();
      });
      map.on("click", function (ev) {
        gegenpunkt(ev.latlng, map.hasLayer(ortho) ? "orthofoto_klick" : "manuell", "Freier Kartenpunkt");
      });
      modell.onchange = pruefen;
      render();
      verwerfen.disabled = !g;
      if (g && g.status !== "veraltet") { pruefen(); }
      get("/gebaeude.json").then(function (antwort) {
        if (!offen) { return; }
        netz.textContent = antwort.gebaeude.length ? "OSM-Gebäudeecken geladen." : "Keine OSM-Gebäudeecken gefunden. Freie Kartenklicks sind möglich.";
        antwort.gebaeude.forEach(function (haus) { haus.ecken.forEach(function (ecke) {
          var e = L.circleMarker([ecke.lat, ecke.lng], { radius: 6, color: "#2879b9", bubblingMouseEvents: false }).addTo(map);
          var text = document.createElement("span"); text.textContent = "OSM " + haus.osm_id + " · Ecke " + ecke.nummer;
          e.bindTooltip(text);
          e.on("click", function () { gegenpunkt(ecke, "osm_gebaeude", text.textContent); });
        }); });
      }).catch(function (err) { if (offen) { netz.textContent = err.message + " Freie Kartenklicks sind weiterhin möglich."; } });
    }).catch(fehler);
    return { schliessen: schliessen };
  }
  window.objektGeorefEditor = { open: open, planNormiert: planNormiert, affineMatrix: affineMatrix, naechsterSchritt: naechsterSchritt, bspLagegenauigkeit: bspLagegenauigkeit, bspStatus: bspStatus, polygonFlaeche: polygonFlaeche, polygonFehler: polygonFehler, polygonEntfernen: polygonEntfernen };
  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("DOMContentLoaded", function () {
      var container = document.getElementById("georef-editor");
      if (!container) { return; }
      var d = container.dataset;
      open({ modus: "seite", container: container, basisUrl: d.basisUrl, dokumentId: Number(d.dokumentId),
        seite: Number(d.seite), seiteId: Number(d.seiteId), csrfToken: d.csrf, zurueckUrl: d.zurueckUrl,
        startCenter: d.lat && d.lng ? [Number(d.lat), Number(d.lng)] : null });
    });
  }
})();
