(function () {
  "use strict";

  function planNormiert(latlng, breite, hoehe) {
    return { x: latlng.lng / breite, y: -latlng.lat / hoehe };
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
    var meldung = el("p", "", root); meldung.setAttribute("role", "status");
    var liste = el("div", null, root); liste.className = "bsp__liste";
    var formular = el("fieldset", null, root);
    formular.disabled = true;
    el("legend", "Overlay bearbeiten", formular);
    var felder = {}, ausgewaehlt = null, eintraege = [], gueltig = false, busy = false;
    var ausschnitt = { art: "seite" }, rechteck = null, raster = null, zeichnen = false, start = null;
    var cropLeiste = el("div", null, formular); cropLeiste.className = "bsp__aktionen";
    var ausschnittText = el("p", "Ganze Seite vorgeschlagen", formular);
    function feld(name, text, typ, optionen) {
      var label = el("label", text + " ", formular);
      var input = el(optionen ? "select" : "input", null, label);
      if (optionen) { optionen.forEach(function (o) { var n = el("option", o[1], input); n.value = o[0]; }); }
      else { input.type = typ || "text"; }
      input.name = name;
      if (typ === "number") { input.min = "0.1"; input.max = "1"; input.step = "0.1"; }
      felder[name] = input; return input;
    }
    feld("name", "Name").required = true;
    feld("plan_art", "Planart", null, [["lageplan", "Lageplan"], ["hallenbereich", "Hallenbereich"],
      ["gefahrenzone", "Gefahrenzone"], ["geschoss", "Geschoss"], ["sonstig", "Sonstiger Plan"]]);
    feld("bereich", "Bereich"); feld("geschoss", "Geschoss");
    feld("standard_sichtbar", "Standardmäßig sichtbar", "checkbox");
    feld("standard_deckkraft", "Standard-Deckkraft (0,1 bis 1)", "number");
    feld("empfohlen", "Empfohlen", "checkbox"); feld("autodruck", "Automatisch drucken", "checkbox");
    feld("druck_deckkraft", "Druck-Deckkraft (0,1 bis 1)", "number");
    felder.name.maxLength = 200; felder.bereich.maxLength = 200; felder.geschoss.maxLength = 100;
    var aktionen = el("div", null, formular); aktionen.className = "bsp__aktionen";
    var qualitaet = el("p", "", root);
    var deckLabel = el("label", "Raster-Vorschau: Deckkraft ", root);
    var deck = el("input", null, deckLabel); deck.type = "range"; deck.min = "10"; deck.max = "100"; deck.value = "60";
    deck.oninput = function () { if (raster) { raster.setOpacity(Number(deck.value) / 100); } };
    function api(pfad, daten) {
      var config = { headers: { "X-CSRF-Token": opts.csrfToken } };
      if (daten !== undefined) {
        config.method = "POST"; config.headers["Content-Type"] = "application/json"; config.body = JSON.stringify(daten);
      }
      return fetch(basis + pfad, config).then(function (r) { return r.json().then(function (d) {
        if (!r.ok) { throw new Error(typeof d.detail === "string" ? d.detail : "Ungültige Eingabe"); } return d;
      }); });
    }
    function fehler(err) { meldung.textContent = err.message; }
    function sperren() { formular.disabled = busy || !gueltig; }
    function ungespeichert() {
      if (!ausgewaehlt) { return; }
      rastern.disabled = freigeben.disabled = zurueckziehen.disabled = true;
      meldung.textContent = "Änderungen zuerst als Entwurf speichern. Eine bestehende Freigabe wird dabei aufgehoben.";
    }
    Object.keys(felder).forEach(function (k) { felder[k].addEventListener("input", ungespeichert); });
    function cropModus(an) {
      zeichnen = an; start = null;
      cropKnopf.textContent = an ? "Ausschnitt-Auswahl beenden" : "Rechteck aufziehen";
      if (an) { plan.dragging.disable(); plan.touchZoom.disable(); plan.doubleClickZoom.disable(); }
      else { plan.dragging.enable(); plan.touchZoom.enable(); plan.doubleClickZoom.enable(); }
      planDiv.style.touchAction = an ? "none" : "";
    }
    function zeigeAusschnitt() {
      if (rechteck) { rechteck.remove(); rechteck = null; }
      ausschnittText.textContent = ausschnitt.art === "seite" ? "Ganze Seite vorgeschlagen" : "Rechteck auf der gedrehten Seite gewählt";
      if (ausschnitt.art === "rechteck") {
        rechteck = L.rectangle([[-ausschnitt.y1 * meta.bildgroesse[1], ausschnitt.x0 * meta.bildgroesse[0]],
          [-ausschnitt.y0 * meta.bildgroesse[1], ausschnitt.x1 * meta.bildgroesse[0]]], { interactive: false }).addTo(plan);
      }
    }
    knopf("Ganze Seite", cropLeiste, function () {
      cropModus(false); ausschnitt = { art: "seite" }; zeigeAusschnitt(); ungespeichert();
    });
    var cropKnopf = knopf("Rechteck aufziehen", cropLeiste, function () {
      cropModus(!zeichnen);
      if (zeichnen) { meldung.textContent = "Im Seitenbild ein Rechteck aufziehen, danach Auswahl beenden. Mit Finger oder Maus möglich."; }
    });
    function xy(ev) {
      var p = planNormiert(plan.mouseEventToLatLng(ev), meta.bildgroesse[0], meta.bildgroesse[1]);
      return { x: Math.max(0, Math.min(1, p.x)), y: Math.max(0, Math.min(1, p.y)) };
    }
    planDiv.addEventListener("pointerdown", function (ev) {
      if (!zeichnen || busy || !gueltig || ev.isPrimary === false) { return; }
      ev.preventDefault(); ev.stopPropagation(); start = xy(ev); planDiv.setPointerCapture(ev.pointerId);
    }, true);
    function bewegen(ev) {
      if (!start) { return; }
      ev.preventDefault(); ev.stopPropagation(); var p = xy(ev);
      ausschnitt = { art: "rechteck", x0: Math.min(start.x, p.x), y0: Math.min(start.y, p.y),
        x1: Math.max(start.x, p.x), y1: Math.max(start.y, p.y) }; zeigeAusschnitt(); ungespeichert();
    }
    planDiv.addEventListener("pointermove", bewegen, true);
    planDiv.addEventListener("pointerup", function (ev) {
      if (!start) { return; } bewegen(ev); start = null;
      planDiv.releasePointerCapture(ev.pointerId);
    }, true);
    planDiv.addEventListener("pointercancel", function () { start = null; cropModus(false); });
    function waehlen(o) {
      ausgewaehlt = o || null; cropModus(false);
      var werte = o || { name: "BSP-Overlay", plan_art: "lageplan", standard_sichtbar: true,
        standard_deckkraft: 0.6, druck_deckkraft: 0.6 };
      Object.keys(felder).forEach(function (k) {
        if (felder[k].type === "checkbox") { felder[k].checked = !!werte[k]; }
        else { felder[k].value = werte[k] == null ? "" : werte[k]; }
      });
      ausschnitt = o ? o.ausschnitt : { art: "seite" }; zeigeAusschnitt();
      if (raster) { raster.remove(); raster = null; }
      if (o && o.raster_url && o.bounds) {
        rasterVorschauStart();
        deck.value = String(o.standard_deckkraft * 100);
        raster = L.imageOverlay(o.raster_url, o.bounds, { opacity: Number(deck.value) / 100, interactive: false }).addTo(map);
        raster.on("error", function () { if (raster) { raster.remove(); raster = null; } meldung.textContent = "Raster-Vorschau nicht verfügbar. Bitte neu rastern."; });
        map.fitBounds(o.bounds);
      }
      var q = o && o.qualitaet;
      qualitaet.textContent = q ? "Georef: " + q.status + " · RMSE: " + meter(q.rmse_m) + " · " +
        q.punktzahl + " Punkte · Modell: " + q.modell + " · Revision " + q.revision +
        " · Dokument-Version " + o.dokument_version + " · Freigabe-Nr. " + o.freigabe_nr : "";
      rastern.disabled = freigeben.disabled = zurueckziehen.disabled = loeschen.disabled = !o;
      if (o) { loeschen.disabled = o.status !== "entwurf"; }
    }
    function meter(n) { return n == null ? "unbekannt" : n.toFixed(2) + " m"; }
    function laden(id) {
      return api("?seite_id=" + opts.seiteId).then(function (d) {
        gueltig = d.georef_gueltig; eintraege = d.overlays; liste.replaceChildren();
        meldung.textContent = gueltig ? "Gültige Georeferenz vorhanden. Ganzer Plan oder Rechteck möglich." :
          "BSP-Overlay deaktiviert: Eine gültige gespeicherte Georeferenz und ein aktuelles freigegebenes Dokument sind erforderlich.";
        eintraege.forEach(function (o) {
          var row = el("div", null, liste);
          knopf(o.name + " · " + o.status, row, function () { waehlen(o); });
          if (o.hinweis) { el("p", o.hinweis, row); }
        });
        waehlen(eintraege.find(function (o) { return o.id === id; })); sperren();
      });
    }
    function ausfuehren(pfad, daten) {
      if (busy) { return Promise.resolve(); }
      busy = true; cropModus(false); sperren();
      return api(pfad, daten).then(function (d) { return laden(d.id); }).catch(fehler)
        .finally(function () { busy = false; sperren(); });
    }
    function daten() {
      var d = { ausschnitt: ausschnitt, expected_rotation: meta.rotation };
      Object.keys(felder).forEach(function (k) {
        var input = felder[k]; d[k] = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value;
      }); return d;
    }
    knopf("Neuer Entwurf", root, function () { if (!busy) { waehlen(null); } });
    knopf("Entwurf speichern", aktionen, function () {
      if (!formular.reportValidity()) { return; }
      var d = daten();
      if (!ausgewaehlt) { d.seite_id = opts.seiteId; }
      ausfuehren(ausgewaehlt ? "/" + ausgewaehlt.id + "/aendern" : "", d);
    });
    var rastern = knopf("Neu rastern", aktionen, function () { ausfuehren("/" + ausgewaehlt.id + "/rastern", {}); });
    var freigeben = knopf("Freigeben", aktionen, function () {
      // Unmittelbar vor der Bestaetigung Qualitaet und Warnungen erneut vom Server holen.
      api("?seite_id=" + opts.seiteId).then(function (d) {
        var o = d.overlays.find(function (item) { return item.id === ausgewaehlt.id; });
        if (!o || !o.georef_gueltig) { throw new Error("Gültige Georeferenz erforderlich"); }
        var dialog = el("dialog", null, root); dialog.className = "bsp__freigabe";
        el("h3", "BSP-Overlay freigeben", dialog);
        el("p", o.name + " · Georef: " + o.qualitaet.status + " · RMSE: " + meter(o.qualitaet.rmse_m) + " · " +
          o.qualitaet.punktzahl + " Punkte · Modell: " + o.qualitaet.modell, dialog);
        var checks = o.warnungen.map(function (code) {
          var label = el("label", null, dialog); var input = el("input", null, label); input.type = "checkbox";
          el("span", code === "wenige_punkte" ? "Nur drei Referenzpunkte: Ich habe die Lage geprüft." :
            "Nur freie Kartenklicks: Ich habe die Lage geprüft.", label);
          return { code: code, input: input };
        });
        var bestaetigen = knopf("Verbindlich freigeben", dialog, function () {
          dialog.close(); ausfuehren("/" + o.id + "/freigeben", { bestaetigte_warnungen: checks.map(function (c) { return c.code; }) });
        });
        function pruefen() { bestaetigen.disabled = checks.some(function (c) { return !c.input.checked; }); }
        checks.forEach(function (c) { c.input.onchange = pruefen; }); pruefen();
        knopf("Abbrechen", dialog, function () { dialog.close(); });
        dialog.addEventListener("close", function () { dialog.remove(); }); dialog.showModal();
      }).catch(fehler);
    });
    var zurueckziehen = knopf("Zurückziehen", aktionen, function () { ausfuehren("/" + ausgewaehlt.id + "/zurueckziehen", {}); });
    var loeschen = knopf("Entwurf löschen", aktionen, function () { ausfuehren("/" + ausgewaehlt.id + "/loeschen", {}); });
    laden().catch(fehler);
    return { zeichnet: function () { return zeichnen; }, aktualisieren: function () {
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
  window.objektGeorefEditor = { open: open, planNormiert: planNormiert, affineMatrix: affineMatrix, naechsterSchritt: naechsterSchritt };
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
