/* Objekt-Lagekarte: Leaflet + Geoman-Editor mit Symbolpalette.
 *
 * Symbol-Rendering analog taktSymbolHtml der GSL-Karte (incident_major/karte.html):
 * rot umrandete Text-Boxen (FSD/BMZ/FBF/...), gelbe Gefahren-Dreiecke, Pfeile
 * fuer Zugaenge, Hydranten-Symbole. Kein Server-Symbolkatalog.
 *
 * initObjektKarte({ elementId, objektId, csrfToken, editierbar })
 */
(function () {
  "use strict";

  var SYMBOLE = {
    fsd:               { text: "FSD",  stil: "box" },
    schluesselbox:     { text: "BOX",  stil: "box" },
    bsp:               { text: "BSP",  stil: "box" },
    bmz:               { text: "BMZ",  stil: "box" },
    fbf:               { text: "FBF",  stil: "box" },
    dlk_stellplatz:    { text: "DLK",  stil: "box" },
    objektfunk:        { text: "FUNK", stil: "box" },
    sammelplatz:       { text: "SP",   stil: "gruen" },
    feuerloescher:     { text: "FL",   stil: "rot" },
    hauptzugang:       { text: "➜",    stil: "pfeil-voll" },
    nebenzugang:       { text: "➜",    stil: "pfeil-leer" },
    stiege:            { text: "ST",   stil: "gruen" },
    aufzug:            { text: "AZ",   stil: "box" },
    gefahr_ex:         { text: "EX",   stil: "dreieck" },
    gefahr_gas:        { text: "GAS",  stil: "dreieck" },
    gefahr_chemie:     { text: "CHE",  stil: "dreieck" },
    gefahr_strom:      { text: "kV",   stil: "dreieck" },
    gefahr_pv:         { text: "PV",   stil: "dreieck" },
    hydrant_ueberflur: { text: "H",    stil: "hydrant" },
    hydrant_unterflur: { text: "UH",   stil: "hydrant" }
  };

  // Org-Symbolkatalog (aus /objekte/karten-symbole.json) in die SYMBOLE-Map mergen.
  // Eingebaute Defaults bleiben als Fallback erhalten.
  function setObjektSymbole(liste) {
    (liste || []).forEach(function (s) {
      if (!s || !s.code) { return; }
      SYMBOLE[s.code] = { text: s.text || "", stil: s.stil || "box", bild: s.bild || null };
    });
  }
  window.setObjektSymbole = setObjektSymbole;

  var _katalogGeladen = null;
  function ladeObjektSymbole(url) {
    if (_katalogGeladen) { return _katalogGeladen; }
    _katalogGeladen = fetch(url || "/objekte/karten-symbole.json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.symbole) { setObjektSymbole(d.symbole); } })
      .catch(function () { /* Fallback: eingebaute SYMBOLE */ });
    return _katalogGeladen;
  }
  window.ladeObjektSymbole = ladeObjektSymbole;

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function objektSymbolHtml(typ, label, rotation) {
    // Unbekannte Typen (z. B. aus EUS-Import): Typ-Kuerzel statt "?" anzeigen
    var s = SYMBOLE[typ] || { text: String(typ || "?").substring(0, 4).toUpperCase(), stil: "box" };
    var inner;
    if (s.stil === "bild" && s.bild) {
      inner = '<div class="oks oks--bild"><img src="' + escapeHtml(s.bild) + '" alt="" draggable="false"></div>';
    } else if (s.stil === "dreieck") {
      inner = '<div class="oks oks--dreieck"><span>' + escapeHtml(s.text) + "</span></div>";
    } else if (s.stil === "hydrant") {
      inner = '<div class="oks oks--hydrant">' + escapeHtml(s.text) + "</div>";
    } else if (s.stil === "pfeil-voll") {
      inner = '<div class="oks oks--pfeil oks--pfeil-voll">' + escapeHtml(s.text) + "</div>";
    } else if (s.stil === "pfeil-leer") {
      inner = '<div class="oks oks--pfeil oks--pfeil-leer">' + escapeHtml(s.text) + "</div>";
    } else if (s.stil === "gruen") {
      inner = '<div class="oks oks--box oks--gruen">' + escapeHtml(s.text) + "</div>";
    } else if (s.stil === "rot") {
      inner = '<div class="oks oks--box oks--rot">' + escapeHtml(s.text) + "</div>";
    } else {
      inner = '<div class="oks oks--box">' + escapeHtml(s.text) + "</div>";
    }
    // Eigener innerer Wrapper: Leaflets Positions-Transform bleibt unberuehrt.
    var winkel = ((Number(rotation) || 0) % 360 + 360) % 360;
    inner = '<div style="transform: rotate(' + winkel + 'deg)">' + inner + "</div>";
    if (label) {
      inner += '<div class="oks__label">' + escapeHtml(label) + "</div>";
    }
    return '<div class="oks-wrap">' + inner + "</div>";
  }

  // Auch global verfuegbar (Alarm-Infoscreen rendert Symbole ohne initObjektKarte)
  window.objektSymbolHtml = objektSymbolHtml;

  var HYDRANT_TEXT = { ueberflur: "H", unterflur: "UH", loeschwasser: "≈" };
  var HYDRANT_LABEL = {
    ueberflur: "Überflurhydrant", unterflur: "Unterflurhydrant", loeschwasser: "Löschwasserstelle"
  };
  function hydrantIcon(typ) {
    return L.divIcon({
      html: '<div class="hydrant-icon hydrant-icon--' + (typ || "hydrant") + '">' +
        (HYDRANT_TEXT[typ] || "H") + "</div>",
      className: "hydrant-divicon", iconSize: null, iconAnchor: [11, 11]
    });
  }

  function symbolIcon(typ, label, rotation) {
    return L.divIcon({
      html: objektSymbolHtml(typ, label, rotation),
      className: "oks-divicon",
      iconSize: null,
      iconAnchor: [16, 16]
    });
  }

  var WARNUNGEN = {
    weit_vom_objekt: "Position liegt mehr als 2 km vom Objekt entfernt.",
    einsatzkritisch_freigabe_erforderlich: "Einsatzkritisches Symbol: Position und Bedeutung ausdrücklich geprüft.",
    moegliche_dublette: "Mögliche Dublette: mit bestehendem Marker oder anderem Vorschlag abgleichen.",
    position_manuell: "Position wurde manuell korrigiert.",
    georef_ungenau: "Georeferenz ist ungenau.",
    georef_veraltet: "Georeferenz ist veraltet; Position erneut prüfen.",
    georef_ungueltig: "Georeferenz ist ungültig.",
    georef_ungeprueft: "Georeferenz ist noch ungeprüft.",
    georef_entwurf: "Georeferenz ist noch ein Entwurf.",
    georef_abgelehnt: "Georeferenz wurde abgelehnt; Position erneut bestimmen."
  };
  function warntext(w) {
    return (WARNUNGEN[w.code] || "Unbekannte Warnung: " + w.code) +
      (w.marker_id ? " (Marker #" + w.marker_id + ")" : "") +
      (w.vorschlag_id ? " (Vorschlag #" + w.vorschlag_id + ")" : "");
  }
  function geometrie(v) {
    return typeof v.geometry_json === "string" ? JSON.parse(v.geometry_json) : v.geometry_json || v.geometry;
  }
  function position(v) {
    if (v.lat != null && v.lng != null) { return [v.lat, v.lng]; }
    var g = geometrie(v);
    return g && g.type === "Point" ? [g.coordinates[1], g.coordinates[0]] : null;
  }
  function prueffaehig(v) {
    return v.freigabe_status === "offen" && v.position_status === "vorgeschlagen" &&
      !!(position(v) || geometrie(v));
  }
  function warnungenFuerFreigabe(v) {
    var warnungen = v.warnungen.slice();
    function kritisch(code) {
      return ["fsd", "bmz", "fbf", "hauptzugang", "nebenzugang", "dlk_stellplatz"].includes(code) || /^(gefahr_|hydrant_)/.test(code);
    }
    if ((kritisch(v.symbol_code) || (v.basis_marker && kritisch(v.basis_marker.typ))) &&
        !warnungen.some(function (w) { return w.code === "einsatzkritisch_freigabe_erforderlich"; })) {
      warnungen.push({ code: "einsatzkritisch_freigabe_erforderlich" });
    }
    return warnungen;
  }
  function unkritisch(v) {
    return prueffaehig(v) && warnungenFuerFreigabe(v).length === 0;
  }
  function filterPasst(v, filter) {
    return filter === "alle" || (filter === "neu" && v.operation === "hinzufuegen") ||
      (filter === "aenderungen" && v.operation !== "hinzufuegen") ||
      (filter === "unklar" && v.position_status === "pruefung_erforderlich") ||
      (filter === "veraltet" && v.position_status === "veraltet") ||
      (filter === "dubletten" && v.warnungen.some(function (w) { return w.code === "moegliche_dublette"; }));
  }
  // Kleine reine Helfer auch fuer die bestehenden Node-Tests.
  window.objektKarteReview = { prueffaehig: prueffaehig, filterPasst: filterPasst, warntext: warntext, unkritisch: unkritisch };

  window.objektKarteGeoref = { planNormiert: function (latlng, breite, hoehe) {
    return window.objektGeorefEditor.planNormiert(latlng, breite, hoehe);
  } };

  function georefEditor(root, v, karte, get, post, aktualisieren) {
    return window.objektGeorefEditor.open({ modus: "dialog", root: root,
      dokumentId: v.quelle_dokument_id, seite: v.quelle_seite,
      startCenter: karte.getCenter(), startZoom: Math.max(karte.getZoom(), 18),
      get: get, post: post, onGespeichert: aktualisieren });
  }

  function initReview(root, karte, get, post, markerErsetzen, modusWechsel, karteGeladen) {
    var modus = root.querySelector("[data-review-modus]");
    var panel = root.querySelector("[data-review-panel]");
    var liste = root.querySelector("[data-review-liste]");
    var detail = root.querySelector("[data-review-detail]");
    var filter = root.querySelector("[data-review-filter]");
    var freigabe = root.querySelector("[data-review-freigeben]");
    var dialog = root.querySelector("[data-review-dialog]");
    var fehler = root.querySelector("[data-review-fehler]");
    var sperre = root.querySelector("[data-review-sperre]");
    var layout = root.nextElementSibling;
    // Panel neben der Karte; Schalter und Dialog bleiben oberhalb erreichbar.
    if (layout) { layout.insertBefore(panel, layout.firstChild); }
    var gesperrt = root.dataset.gesperrt === "1";
    var daten = [], layers = [], aktiv = null, busy = false, sichtbar = false, manuellVersteckt = false;
    var operationen = { hinzufuegen: "Neu", aendern: "Änderung", entfernen: "Entfernen" };

    function el(tag, text, parent, klasse) {
      var node = document.createElement(tag);
      if (text != null) { node.textContent = text; }
      if (klasse) { node.className = klasse; }
      if (parent) { parent.appendChild(node); }
      return node;
    }
    function button(text, parent, aktion) {
      var b = el("button", text, parent, "btn btn--ghost btn--xs");
      b.type = "button";
      b.disabled = busy;
      b.onclick = aktion;
      return b;
    }
    function meldung(err) {
      fehler.hidden = false;
      fehler.textContent = err.message || "Vorschläge konnten nicht geladen werden.";
      var app = document.querySelector('[x-data="appState()"]');
      if (app && window.Alpine) { window.Alpine.$data(app).addToast(fehler.textContent, "error"); }
    }
    function leeren() {
      layers.forEach(function (layer) { karte.removeLayer(layer); });
      layers = [];
    }
    function layerDazu(layer) { layer.addTo(karte); layers.push(layer); return layer; }
    function waehlen(v, zentrieren) {
      aktiv = v.id;
      if (zentrieren) {
        if (position(v)) { karte.setView(position(v), Math.max(karte.getZoom(), 19)); }
        else if (geometrie(v)) { karte.fitBounds(L.geoJSON(geometrie(v)).getBounds()); }
      }
      details(v);
      if (detail.scrollIntoView) { detail.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    }
    function korrigieren(v, pos, rotation, label) {
      return mutation("/vorschlaege/" + v.id + "/korrigieren", {
        lat: pos[0], lng: pos[1], rotation: rotation, label: label
      });
    }
    function zeichnen() {
      leeren();
      if (!sichtbar) { return; }
      daten.filter(function (v) { return filterPasst(v, filter.value); }).forEach(function (v) {
        var pos = position(v), g = geometrie(v), layer;
        if (!pos && v.position_status === "pruefung_erforderlich") { return; }
        var stil = { color: "#7654be", dashArray: "6 5", weight: 3, fillOpacity: 0.12, pmIgnore: true };
        if (pos) {
          var icon = symbolIcon(v.symbol_code, v.label, v.rotation);
          icon.options ? icon.options.className += " oks-review__marker" : icon.className += " oks-review__marker";
          layer = L.marker(pos, { icon: icon, draggable: !busy, pmIgnore: true });
          layer.on("dragend", function (ev) {
            var p = ev.target.getLatLng();
            korrigieren(v, [p.lat, p.lng], v.rotation, v.label);
          });
          if (v.genauigkeit_m != null) {
            layerDazu(L.circle(pos, Object.assign({ radius: v.genauigkeit_m, interactive: false }, stil)));
          }
        } else if (g && v.position_status !== "pruefung_erforderlich") {
          layer = L.geoJSON(g, { style: stil, pmIgnore: true });
        }
        if (layer) { layer.on("click", function () { waehlen(v, false); }); layerDazu(layer); }
        var alt = v.basis_marker;
        if (alt && v.operation !== "hinzufuegen") {
          var altpos = position(alt);
          if (altpos) {
            var geist = symbolIcon(alt.typ, alt.label, alt.rotation);
            geist.options ? geist.options.className += " oks-review__geist" : geist.className += " oks-review__geist";
            layerDazu(L.marker(altpos, { icon: geist, interactive: false, pmIgnore: true }));
            if (pos) { layerDazu(L.polyline([altpos, pos], Object.assign({ interactive: false }, stil))); }
          } else if (alt.geometry) {
            layerDazu(L.geoJSON(alt.geometry, { style: Object.assign({ opacity: 0.4 }, stil), pmIgnore: true }));
          }
        }
      });
    }
    function render() {
      sperre.hidden = !gesperrt;
      freigabe.disabled = gesperrt || busy || !daten.some(unkritisch);
      root.querySelector("[data-review-count]").textContent = daten.length;
      liste.replaceChildren();
      daten.filter(function (v) { return filterPasst(v, filter.value); }).forEach(function (v) {
        var row = el("div", null, liste, "oks-review__eintrag");
        button("#" + v.id + " · " + operationen[v.operation] + " · " + (v.label || v.symbol_code) +
          (v.position_status === "veraltet" ? " · Veraltet" : ""), row, function () { waehlen(v, true); });
      });
      if (!liste.childNodes.length) { el("p", "Keine offenen Vorschläge für diesen Filter.", liste); }
      var v = daten.find(function (v) { return v.id === aktiv; });
      if (v) { details(v); } else { detail.textContent = "Vorschlag aus der Liste oder auf der Karte auswählen."; }
      zeichnen();
    }
    function feld(text, wert, typ, parent) {
      var gruppe = el("div", null, parent, "form-group");
      var label = el("label", text, gruppe, "form-label");
      var input = el("input", null, gruppe, "form-input");
      input.type = typ;
      input.value = wert == null ? "" : wert;
      input.id = "oks-review-feld-" + text.replace(/[^a-z]/gi, "");
      label.htmlFor = input.id;
      input.disabled = busy;
      return input;
    }
    function details(v) {
      detail.replaceChildren();
      el("h4", "#" + v.id + " · " + operationen[v.operation] + " · Symbol: " + v.symbol_code, detail);
      var quelle = el("div", null, detail, "oks-review__quelle");
      var texte = el("div", null, quelle);
      el("p", "Positionsherkunft: " + v.position_methode, texte);
      el("p", "Genauigkeit: " + (v.genauigkeit_m == null ? "unbekannt" : v.genauigkeit_m + " m"), texte);
      el("p", "Erkennungskonfidenz: " + (v.erkennungs_konfidenz == null ? "unbekannt" :
        Math.round(v.erkennungs_konfidenz * 100) + " %"), texte);
      el("p", "Begründung: " + (v.begruendung || "Keine Angabe"), texte);
      el("p", "Quelle: " + (v.quelle_dokument_name || (v.quelle_dokument_id ? "Dokument #" + v.quelle_dokument_id : "Keine Angabe")) +
        (v.quelle_seite ? " · Seite " + v.quelle_seite : ""), texte);
      if (Number.isInteger(v.quelle_seite_id)) {
        var link = el("a", "Dokumentseite öffnen", texte);
        link.href = "/objekt-medien/seite/" + v.quelle_seite_id + "/pdf";
        link.target = "_blank"; link.rel = "noopener";
        var bildLink = el("a", null, quelle);
        bildLink.href = link.href; bildLink.target = "_blank"; bildLink.rel = "noopener";
        var bild = el("img", null, bildLink);
        bild.src = "/objekt-medien/seite/" + v.quelle_seite_id + "/thumb";
        bild.alt = "Vorschau Quelldokument, Seite " + v.quelle_seite;
        bild.loading = "lazy";
      }
      if (v.quelle_dokument_id && v.quelle_seite) {
        button("Georeferenz", texte, function () { georefEditor(root, v, karte, get, post, laden); });
      }
      if (v.altposition && v.altposition.lat != null) {
        el("p", "Altposition (Georeferenz ungültig): " + v.altposition.lat + ", " + v.altposition.lng, texte);
      }
      v.warnungen.forEach(function (w) { el("p", warntext(w), detail); });
      var aktionen = el("div", null, detail, "oks-review__aktionen");
      var b = button("Freigeben", aktionen, function () { freigeben([v]); });
      b.disabled = busy || gesperrt || !prueffaehig(v);
      var ablehnen = button("Ablehnen", aktionen, function () {
        if (!grund.value.trim()) { grund.focus(); meldung(new Error("Bitte einen Ablehngrund eingeben.")); return; }
        mutation("/vorschlaege/" + v.id + "/ablehnen", { grund: grund.value });
      });
      ablehnen.disabled = busy;
      if (v.position_status === "veraltet") {
        el("p", "Quelle veraltet: Position und aktuelle Quelle prüfen, danach Speichern & freigeben wählen.", detail, "form-hint");
      }
      var form = el("form", null, detail);
      var pos = position(v);
      el("p", "Position per Marker verschieben oder Koordinaten eingeben. Korrektur wird als manuell geprüft gespeichert.", form, "form-hint");
      var lat = feld("Breitengrad", pos && pos[0], "number", form);
      var lng = feld("Längengrad", pos && pos[1], "number", form);
      lat.step = lng.step = "any"; lat.required = lng.required = true;
      lat.min = "-90"; lat.max = "90"; lng.min = "-180"; lng.max = "180";
      var rotation = feld("Drehung in Grad", v.rotation, "number", form);
      rotation.step = "1"; rotation.required = true;
      var label = feld("Beschriftung", v.label, "text", form); label.maxLength = 100;
      // Geometrien duerfen durch eine Textkorrektur nicht versehentlich zu Punkten werden.
      var speichern = button("Korrektur speichern", form, null);
      var nurGeometrie = !!(geometrie(v) && !pos);
      speichern.type = "submit"; speichern.disabled = busy || nurGeometrie;
      if (nurGeometrie) {
        [lat, lng, rotation, label].forEach(function (input) { input.disabled = true; });
        el("p", "Die Positionskorrektur ist hier nur für Punktsymbole verfügbar. Linien und Flächen können geprüft, freigegeben oder abgelehnt werden.", form, "form-hint");
      }
      var gemeinsam = button("Speichern & freigeben", form, null);
      gemeinsam.type = "submit"; gemeinsam.disabled = busy || gesperrt || nurGeometrie;
      el("p", "Mit Speichern & freigeben bestätigen Sie die manuell geprüfte Position.", form, "form-hint");
      form.onsubmit = function (ev) {
        ev.preventDefault();
        if (ev.submitter === gemeinsam) {
          freigeben([v], { lat: Number(lat.value), lng: Number(lng.value), rotation: Number(rotation.value), label: label.value });
        } else {
          korrigieren(v, [Number(lat.value), Number(lng.value)], Number(rotation.value), label.value);
        }
      };
      var grund = feld("Ablehngrund", "", "text", detail); grund.maxLength = 10000;
    }

    function laden() {
      // Review-GET markiert veraltete Quellen; produktive Marker danach lesen.
      return get("/vorschlaege.json").then(function (review) {
        return get("/objekte.json").then(function (karteDaten) {
          daten = review.vorschlaege.filter(function (v) { return v.freigabe_status === "offen"; });
          gesperrt = review.freigabe_gesperrt;
          ansicht(daten.length > 0 && !manuellVersteckt);
          markerErsetzen(karteDaten.eintraege);
          render();
        });
      });
    }
    function toast(text) {
      var app = document.querySelector('[x-data="appState()"]');
      if (app && window.Alpine) { window.Alpine.$data(app).addToast(text, "success"); }
    }
    function mutation(pfad, payload) {
      if (busy) { return Promise.resolve(false); }
      busy = true; fehler.hidden = true; render();
      dialog.querySelectorAll("button, input").forEach(function (e) { e.disabled = true; });
      return post(pfad, payload).then(function () {
        if (dialog.open) { dialog.close(); }
        return laden().then(function () { return true; });
      }).catch(function (err) {
        meldung(err);
        // Auch Drag-Vorschau zuruecksetzen und Konflikte neu einlesen.
        return laden().catch(meldung).then(function () { return false; });
      }).finally(function () {
        busy = false;
        render();
        // Ein alter Dialog darf nach Konflikten keine veraltete Auswahl bestaetigen.
        if (dialog.open) { dialog.close(); }
      });
    }
    function freigeben(vorschlaege, korrektur) {
      if (busy || gesperrt || !vorschlaege.length) { return; }
      var bestaetigungen = {}, blocker = [];
      vorschlaege.forEach(function (v) {
        var warnungen = warnungenFuerFreigabe(v);
        if (korrektur) {
          // Veraltete Quellen bleiben vor der gemeinsamen Aktion sichtbar und
          // bestaetigungspflichtig; die Korrektur ersetzt sie erst im Backend.
          if (v.position_status === "veraltet" && !warnungen.some(function (w) { return w.code === "georef_veraltet"; })) {
            warnungen.push({ code: "georef_veraltet" });
          }
          warnungen.push({ code: "position_manuell" });
        }
        bestaetigungen[v.id] = Array.from(new Set(warnungen.map(function (w) { return w.code; })));
        warnungen.forEach(function (w) {
          if (w.code !== "position_manuell") { blocker.push({ v: v, w: w }); }
        });
      });
      function senden() {
        var pfad = korrektur ? "/vorschlaege/" + vorschlaege[0].id + "/korrigieren" : "/vorschlaege/freigeben";
        var payload = korrektur ? Object.assign({}, korrektur, { freigeben: true, bestaetigte_warnungen: bestaetigungen }) :
          { ids: vorschlaege.map(function (v) { return v.id; }), bestaetigte_warnungen: bestaetigungen };
        return mutation(pfad, payload);
      }
      if (!blocker.length) { return senden(); }
      dialog.replaceChildren();
      el("h3", "Freigabe bestätigen", dialog);
      blocker.forEach(function (eintrag) {
        el("p", "#" + eintrag.v.id + " · " + warntext(eintrag.w), dialog);
      });
      var label = el("label", null, dialog);
      var check = el("input", null, label); check.type = "checkbox";
      el("span", " Position und Quelle geprüft", label);
      var aktionen = el("div", null, dialog, "oks-review__aktionen");
      button("Abbrechen", aktionen, function () { dialog.close(); });
      var bestaetigen = button("Jetzt freigeben", aktionen, function () {
        if (check.checked && !busy && !gesperrt) { senden(); }
      });
      bestaetigen.disabled = true;
      check.onchange = function () { bestaetigen.disabled = !check.checked || busy; };
      dialog.showModal();
    }
    freigabe.onclick = async function () {
      if (busy || gesperrt) { return; }
      // Jede Anfrage bleibt atomar und unter dem Service-Limit. Nach jedem Block
      // frisch laden: Konkurrenzvorschlaege koennen inzwischen ersetzt worden sein.
      var ids = daten.filter(unkritisch).map(function (v) { return v.id; });
      var anzahl = 0;
      while (ids.length) {
        var block = ids.splice(0, 30).filter(function (id) { return daten.some(function (v) { return v.id === id && unkritisch(v); }); });
        if (!block.length) { continue; }
        var ok = await mutation("/vorschlaege/freigeben", { ids: block, bestaetigte_warnungen: {} });
        if (!ok) {
          if (anzahl) { toast(anzahl + " Vorschläge bereits freigegeben. Weitere Freigabe gestoppt."); }
          return;
        }
        anzahl += block.length;
      }
      if (anzahl) { toast(anzahl + " unkritische Vorschläge freigegeben."); }
    };
    filter.onchange = render;
    function ansicht(aktiviert) {
      sichtbar = aktiviert;
      panel.hidden = !sichtbar;
      modus.textContent = sichtbar ? "Ausblenden" : "Vorschläge anzeigen";
      modus.setAttribute("aria-expanded", String(sichtbar));
      if (layout) { layout.classList.toggle("oks-review__layout", sichtbar); }
      modusWechsel(sichtbar);
      karte.invalidateSize();
      if (!sichtbar) { leeren(); }
    }
    modus.onclick = function () {
      manuellVersteckt = sichtbar;
      ansicht(!sichtbar);
      if (sichtbar) { laden().catch(meldung); }
    };
    // Erst die Grundkarte laden, damit eine spaete Antwort keine Marker doppelt eintraegt.
    karteGeladen.then(laden).catch(meldung);
  }

  window.initObjektKarte = function (opts) {
    var karte = L.map(opts.elementId, { zoomControl: true });
    window.EinsatzcockpitMapConfig.addOsmTileLayer(karte);

    var layerById = {};
    var aktiverTyp = null;

    function apiUrl(pfad) {
      return "/objekte/" + opts.objektId + "/karte" + pfad;
    }

    function apiGet(pfad) {
      return fetch(apiUrl(pfad)).then(antwortLesen);
    }
    function antwortLesen(r) {
      return r.json().then(function (daten) {
        if (!r.ok) {
          var detail = daten.detail;
          var text = typeof detail === "string" ? detail : (detail && detail.meldung) || "Ungültige Eingabe";
          if (detail && detail.ergebnisse) {
            text += ": " + detail.ergebnisse.map(function (e) { return "#" + e.id + " " + e.ursache; }).join("; ");
          }
          throw new Error((r.status === 409 ? "Konflikt (Revision oder Arbeitskopie). " : "") + text +
            (r.status === 409 || r.status === 422 ? " Es wurde nichts übernommen. Bitte Daten neu laden und prüfen." : ""));
        }
        return daten;
      });
    }
    function apiPost(pfad, daten) {
      return fetch(apiUrl(pfad), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": opts.csrfToken
        },
        body: JSON.stringify(daten)
      }).then(antwortLesen);
    }

    function eintragEntfernen(id) {
      apiPost("/objekte/" + id + "/loeschen", {}).then(function () {
        if (layerById[id]) { karte.removeLayer(layerById[id]); delete layerById[id]; }
      }).catch(function () { alert("Löschen fehlgeschlagen"); });
    }

    function popupHtml(e) {
      var html = "<strong>" + (e.label || e.typ).replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</strong>";
      if (opts.editierbar) {
        var status = { bestaetigt: "Bestätigt", veraltet: "Veraltet" }[e.position_status] || "Legacy / ungeprüft";
        html += '<span class="oks-review__status">' + status + '</span>';
        if (!e.geometry || e.geometry.type === "Point") {
          html += '<div>Drehung: <span data-winkel>' + (e.rotation || 0) + '</span>°</div>' +
            '<div><button type="button" class="btn btn--xs" data-drehung="-15">-15°</button> ' +
            '<button type="button" class="btn btn--xs" data-drehung="15">+15°</button> ' +
            '<button type="button" class="btn btn--xs" data-drehung="90">+90°</button> ' +
            '<button type="button" class="btn btn--xs" data-reset>Zurücksetzen</button></div>' +
            '<input type="range" min="0" max="359" step="1" aria-label="Drehwinkel" value="' +
            (e.rotation || 0) + '" data-drehwinkel>';
        }
        html += '<br><button type="button" class="btn btn--danger btn--xs" ' +
          'onclick="window._oksDelete(' + e.id + ')">Entfernen</button>';
      }
      return html;
    }
    window._oksDelete = eintragEntfernen;

    function eintragAnzeigen(e) {
      var layer;
      var punktGeometrie = e.geometry && e.geometry.type === "Point";
      if (e.geometry && !punktGeometrie) {
        layer = L.geoJSON(e.geometry, {
          style: { color: "#d42225", weight: 3, fillOpacity: 0.12 }
        });
        if (opts.editierbar) {
          layer.eachLayer(function (geometrieLayer) {
            geometrieLayer.on("pm:edit pm:rotateend", function () {
              var gj = geometrieLayer.toGeoJSON();
              apiPost("/objekte/" + e.id, { geometry: gj.geometry })
                .catch(function () { alert("Geometrie speichern fehlgeschlagen"); });
            });
          });
        }
      } else {
        // Punkte immer als Symbol-Marker rendern — auch wenn sie (z. B. aus dem
        // EUS-Import) als GeoJSON-Point statt lat/lng gespeichert sind. L.geoJSON
        // wuerde sonst Leaflets Default-Icon nutzen, dessen marker-icon.png es
        // unter /static nicht gibt (kaputtes-Bild-Symbol).
        var lat = punktGeometrie ? e.geometry.coordinates[1] : e.lat;
        var lng = punktGeometrie ? e.geometry.coordinates[0] : e.lng;
        layer = L.marker([lat, lng], {
          icon: symbolIcon(e.typ, e.label, e.rotation),
          pmIgnore: true,
          draggable: !!opts.editierbar
        });
        if (opts.editierbar) {
          layer.on("dragend", function (ev) {
            var pos = ev.target.getLatLng();
            var daten = punktGeometrie
              ? { geometry: { type: "Point", coordinates: [pos.lng, pos.lat] } }
              : { lat: pos.lat, lng: pos.lng };
            apiPost("/objekte/" + e.id, daten).catch(function () {});
          });
        }
      }
      layer.bindPopup(function () { return popupHtml(e); });
      if (opts.editierbar && (!e.geometry || punktGeometrie)) {
        layer.on("popupopen", function (ev) {
          var popup = ev.popup.getElement();
          var slider = popup.querySelector("[data-drehwinkel]");
          var anzeige = popup.querySelector("[data-winkel]");
          var controls = popup.querySelectorAll("button, input");
          function vorschau(winkel) {
            winkel = (winkel % 360 + 360) % 360;
            layer.setIcon(symbolIcon(e.typ, e.label, winkel));
            anzeige.textContent = winkel;
            slider.value = winkel;
            return winkel;
          }
          function speichern(winkel) {
            winkel = vorschau(winkel);
            controls.forEach(function (control) { control.disabled = true; });
            apiPost("/objekte/" + e.id, { rotation: winkel }).then(function (gespeichert) {
              e.rotation = gespeichert.rotation;
              vorschau(e.rotation);
            }).catch(function () {
              vorschau(e.rotation || 0);
              alert("Drehung speichern fehlgeschlagen");
            }).finally(function () {
              controls.forEach(function (control) { control.disabled = false; });
            });
          }
          popup.querySelectorAll("[data-drehung]").forEach(function (button) {
            button.onclick = function () { speichern((e.rotation || 0) + Number(button.dataset.drehung)); };
          });
          popup.querySelector("[data-reset]").onclick = function () { speichern(0); };
          slider.oninput = function () { vorschau(Number(slider.value)); };
          slider.onchange = function () { speichern(Number(slider.value)); };
        });
      }
      layer.addTo(karte);
      layerById[e.id] = layer;
    }

    // Erst den Org-Symbolkatalog laden (auch Bildsymbole), dann Marker rendern.
    var karteGeladen = ladeObjektSymbole(opts.symbolKatalogUrl)
      .then(function () { return fetch(apiUrl("/objekte.json")); })
      .then(function (r) { return r.json(); })
      .then(function (daten) {
        var o = daten.objekt;
        if (o.lat != null && o.lng != null) {
          karte.setView([o.lat, o.lng], 18);
        } else {
          karte.setView([47.4652, 9.7503], 15); /* Fallback Wolfurt */
        }
        daten.eintraege.forEach(eintragAnzeigen);
        var punkte = daten.eintraege.filter(function (e) { return e.lat != null; });
        if (o.lat == null && punkte.length) {
          karte.setView([punkte[0].lat, punkte[0].lng], 18);
        }
      });

    /* ── Hydranten-/Löschwasser-Layer (OSM/OSMHydrant) ── */
    fetch("/objekte/" + opts.objektId + "/hydranten.json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { return; }
        (d.hydranten || []).forEach(function (h) {
          // Manuelle Objekt-Hydranten sind bereits Symbole auf der Karte — nur OSM zeichnen.
          if (h.quelle === "objekt" || h.lat == null || h.lng == null) { return; }
          L.marker([h.lat, h.lng], { icon: hydrantIcon(h.typ) }).addTo(karte)
            .bindPopup("<strong>" + (HYDRANT_LABEL[h.typ] || "Hydrant") + "</strong>" +
              (h.ref ? "<br>" + h.ref : "") +
              (h.entfernung_m != null ? "<br>" + h.entfernung_m + " m" : ""));
        });
      })
      .catch(function () {});

    if (!opts.editierbar) { return karte; }
    var review = document.getElementById("oks-review");
    if (review) {
      initReview(review, karte, apiGet, apiPost, function (eintraege) {
        Object.keys(layerById).forEach(function (id) { karte.removeLayer(layerById[id]); });
        layerById = {};
        eintraege.forEach(eintragAnzeigen);
      }, function (aktiv) {
        aktiverTyp = null;
        if (karte.pm) { karte.pm.disableGlobalEditMode(); karte.pm.disableDraw(); karte.pm.disableGlobalRotateMode(); }
        var paletteElement = document.getElementById("oks-palette");
        if (paletteElement) {
          paletteElement.closest("aside").hidden = aktiv;
          paletteElement.querySelectorAll(".oks-palette__eintrag--aktiv").forEach(function (b) {
            b.classList.remove("oks-palette__eintrag--aktiv");
          });
        }
        if (karte.pm) {
          if (aktiv) { karte.pm.removeControls(); } else { karte.pm.addControls(geomanOptionen); }
        }
      }, karteGeladen);
    }

    /* ── Editor: Palette (Klick waehlt Symbol, Kartenklick platziert) ── */
    var palette = document.getElementById("oks-palette");
    if (palette) {
      palette.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-typ]");
        if (!btn) { return; }
        var vorher = palette.querySelector(".oks-palette__eintrag--aktiv");
        if (vorher) { vorher.classList.remove("oks-palette__eintrag--aktiv"); }
        if (aktiverTyp === btn.dataset.typ) {
          aktiverTyp = null;
          return;
        }
        aktiverTyp = btn.dataset.typ;
        btn.classList.add("oks-palette__eintrag--aktiv");
      });
    }

    karte.on("click", function (ev) {
      if (!aktiverTyp) { return; }
      var typ = aktiverTyp;
      var label = "";
      var labelFeld = document.getElementById("oks-label");
      if (labelFeld) { label = labelFeld.value.trim(); }
      apiPost("/objekte", { typ: typ, lat: ev.latlng.lat, lng: ev.latlng.lng, label: label })
        .then(function (e) {
          eintragAnzeigen(e);
          if (labelFeld) { labelFeld.value = ""; }
        })
        .catch(function () { alert("Speichern fehlgeschlagen"); });
    });

    /* ── Geoman: Linien/Flaechen ── */
    if (karte.pm) {
      var geomanOptionen = {
        position: "topleft",
        drawMarker: false,
        drawCircleMarker: false,
        drawCircle: false,
        drawText: false,
        drawPolyline: true,
        drawRectangle: true,
        drawPolygon: true,
        editMode: true,
        dragMode: false,
        cutPolygon: false,
        removalMode: false,
        rotateMode: true
      };
      karte.pm.addControls(geomanOptionen);
      karte.on("pm:create", function (ev) {
        var gj = ev.layer.toGeoJSON();
        var label = "";
        var labelFeld = document.getElementById("oks-label");
        if (labelFeld) { label = labelFeld.value.trim(); }
        apiPost("/objekte", { typ: "geometrie", geometry: gj.geometry, label: label })
          .then(function (e) {
            karte.removeLayer(ev.layer);
            eintragAnzeigen(e);
            if (labelFeld) { labelFeld.value = ""; }
          })
          .catch(function () {
            karte.removeLayer(ev.layer);
            alert("Speichern fehlgeschlagen");
          });
      });
    }

    return karte;
  };
})();
