import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const editorQuelle = readFileSync(new URL("../../app/static/js/objekt_georef_editor.js", import.meta.url), "utf8");
const quelle = readFileSync(new URL("../../app/static/js/objekt_karte.js", import.meta.url), "utf8");
const fertig = () => new Promise((resolve) => setImmediate(resolve));

class Node {
  constructor(tag = "div", text = "") {
    this.tagName = tag; this.text = text; this.childNodes = []; this.dataset = {};
    this.classList = { toggle() {}, remove() {} };
  }
  set textContent(text) { this.text = String(text); this.childNodes = []; }
  get textContent() { return this.text + this.childNodes.map((n) => n.textContent).join(""); }
  set innerHTML(_) { throw new Error("Review darf kein innerHTML verwenden"); }
  appendChild(node) { this.childNodes.push(node); return node; }
  replaceChildren() { this.childNodes = []; this.text = ""; }
  insertBefore(node) { this.childNodes.unshift(node); }
  setAttribute(name, wert) { this[name] = wert; }
  focus() { this.fokussiert = true; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelectorAll(selector) {
    const tags = selector.split(", ");
    return this.childNodes.flatMap((n) => [...(tags.includes(n.tagName) ? [n] : []), ...n.querySelectorAll(selector)]);
  }
}

function vorschlag(id, extra = {}) {
  return { id, freigabe_status: "offen", position_status: "vorgeschlagen", symbol_code: "tor",
    lat: 47, lng: 9, rotation: 0, operation: "hinzufuegen", label: "Tor", position_methode: "plan_georeferenzierung",
    genauigkeit_m: 5, erkennungs_konfidenz: 0.8, warnungen: [], ...extra };
}

function umgebung(vorschlaege, gesperrt = false) {
  const nodes = Object.fromEntries(["modus", "panel", "liste", "detail", "filter", "freigeben", "dialog", "fehler", "sperre", "count"]
    .map((name) => [name, new Node(name === "dialog" ? "dialog" : "div")]));
  const root = new Node();
  root.dataset.gesperrt = gesperrt ? "1" : "0";
  root.querySelector = (selector) => nodes[selector.match(/data-review-(.*)\]/)[1]];
  root.nextElementSibling = new Node();
  nodes.filter.value = "alle";
  const anfragen = [], markers = [], kreise = [], linien = [], layers = new Set(), toasts = [];
  let fehlercode = null;
  function layer() {
    return {
      events: {}, on(name, fn) { this.events[name] = fn; return this; },
      addTo() { layers.add(this); return this; }, bindPopup() { return this; }
    };
  }
  const map = layer();
  map.setView = (position) => { map.position = position; };
  map.getZoom = () => 18;
  map.invalidateSize = () => {};
  map.removeLayer = (l) => layers.delete(l);
  const context = {
    window: { EinsatzcockpitMapConfig: { addOsmTileLayer() {} }, Alpine: { $data: () => ({ addToast: (m) => toasts.push(m) }) } },
    document: { getElementById: (id) => id === "oks-review" ? root : null,
      createElement: (tag) => new Node(tag), querySelector: () => ({}) },
    L: { map: () => map, divIcon: (options) => ({ options }),
      marker: (pos, options) => { const l = Object.assign(layer(), { pos, options }); markers.push(l); return l; },
      circle: (pos, options) => { const l = layer(); kreise.push({ pos, options }); return l; },
      polyline: (pos, options) => { const l = layer(); linien.push({ pos, options }); return l; } },
    fetch: async (url, opts) => {
      if (opts) {
        const daten = JSON.parse(opts.body);
        anfragen.push({ url, daten, headers: opts.headers });
        if (fehlercode) return { ok: false, status: fehlercode, json: async () => ({ detail: {
          meldung: "Freigabe vollständig abgewiesen", ergebnisse: [{ id: 1, ursache: "Revisionskonflikt" }] } }) };
        if (url.endsWith("freigeben")) vorschlaege = vorschlaege.filter((v) => !daten.ids.includes(v.id));
        if (url.endsWith("korrigieren")) Object.assign(vorschlaege[0], daten);
        return { ok: true, json: async () => ({}) };
      }
      const daten = url.endsWith("karten-symbole.json") ? { symbole: [] }
        : url.endsWith("hydranten.json") ? { hydranten: [] }
        : url.endsWith("vorschlaege.json") ? { vorschlaege, freigabe_gesperrt: gesperrt }
        : { objekt: { lat: 47, lng: 9 }, eintraege: [] };
      return { ok: true, json: async () => daten };
    },
  };
  vm.runInNewContext(editorQuelle + "\n" + quelle, context);
  context.window.initObjektKarte({ elementId: "karte", objektId: 7, csrfToken: "csrf", editierbar: true });
  const starten = async () => { await fertig(); nodes.modus.checked = true; nodes.modus.onchange(); await fertig(); };
  return { nodes, anfragen, markers, kreise, linien, map, layers, toasts, starten, fehler: (code) => { fehlercode = code; } };
}
function button(node, text) {
  return node.querySelectorAll("button").find((b) => b.textContent === text);
}

test("Review rendert KI-Texte als Text, Radius und Altposition; Unklare bleiben in der Liste", async () => {
  const env = umgebung([
    vorschlag(1, { operation: "aendern", label: '<script>Label</script>', begruendung: '<script>Grund</script>',
      quelle_seite_id: 4, quelle_seite: 2, quelle_dokument_name: '<script>Quelle</script>',
      warnungen: [{ code: "unbekannt<script>" }], basis_marker: { lat: 46, lng: 8, typ: "tor" } }),
    vorschlag(2, { lat: null, lng: null, position_status: "pruefung_erforderlich", basis_marker: { lat: 46, lng: 8 } }),
  ]);
  await env.starten();
  assert.equal(env.markers.length, 2); // Vorschlag und Geist, kein unklarer Marker.
  assert.equal(env.kreise[0].options.radius, 5);
  assert.equal(env.linien.length, 1);
  assert.match(env.markers[0].options.icon.options.className, /oks-review__marker/);
  env.nodes.liste.querySelectorAll("button")[0].onclick();
  assert.match(env.nodes.detail.textContent, /<script>Grund<\/script>/);
  assert.match(env.nodes.detail.textContent, /Erkennungskonfidenz: 80 %/);
  assert.match(env.nodes.detail.textContent, /Genauigkeit: 5 m/);
  assert.match(env.nodes.detail.textContent, /unbekannt<script>/);
  assert.equal(env.nodes.detail.querySelectorAll("img")[0].src, "/objekt-medien/seite/4/thumb");
  assert.equal(env.nodes.liste.querySelectorAll("input").length, 1);
  const m = env.markers.find((m) => m.options.draggable);
  m.events.dragend({ target: { getLatLng: () => ({ lat: 47.1, lng: 9.1 }) } });
  await fertig();
  assert.match(env.anfragen[0].url, /\/1\/korrigieren$/);
  assert.equal(env.anfragen[0].daten.lat, 47.1);
  assert.equal(env.anfragen[0].daten.label, '<script>Label</script>');
  assert.equal(env.anfragen[0].headers["X-CSRF-Token"], "csrf");
});

test("Sammelfreigabe verlangt jede Warnung und eigene Bestätigung für kritischen Altmarker", async () => {
  const env = umgebung([vorschlag(1, { operation: "entfernen", basis_marker: { typ: "bmz" },
    warnungen: [{ code: "moegliche_dublette", marker_id: 3 }, { code: "position_manuell" }] })]);
  await env.starten();
  const check = env.nodes.liste.querySelectorAll("input")[0];
  check.checked = true; check.onchange();
  env.nodes.freigeben.onclick();
  const checks = env.nodes.dialog.querySelectorAll("input");
  const bestaetigen = button(env.nodes.dialog, "Jetzt freigeben");
  assert.equal(checks.length, 3);
  assert.equal(bestaetigen.disabled, true);
  checks[0].checked = true; checks[0].onchange();
  assert.equal(bestaetigen.disabled, true);
  for (const c of checks) { c.checked = true; c.onchange(); }
  assert.equal(bestaetigen.disabled, false);
  bestaetigen.onclick();
  await fertig();
  assert.equal(env.anfragen.length, 1);
  assert.deepEqual(env.anfragen[0].daten.bestaetigte_warnungen[1], [
    "moegliche_dublette", "position_manuell", "einsatzkritisch_freigabe_erforderlich"]);
  assert.equal(env.nodes.count.textContent, "0");
  assert.equal(env.layers.size, 0);
});

for (const code of [409, 422]) {
  test(`Fehler ${code}: verständlicher Toast und neu geladene Liste ohne Teilübernahme`, async () => {
    const env = umgebung([vorschlag(1)]);
    await env.starten();
    env.fehler(code);
    env.nodes.liste.querySelectorAll("button")[0].onclick();
    button(env.nodes.detail, "Freigeben").onclick();
    button(env.nodes.dialog, "Jetzt freigeben").onclick();
    await fertig();
    assert.match(env.toasts[0], /nichts übernommen/);
    assert.match(env.toasts[0], /Revisionskonflikt/);
    assert.equal(env.nodes.count.textContent, "1");
    assert.equal(env.nodes.dialog.open, false);
  });
}

test("Offene Arbeitskopie sperrt Einzel- und Sammelfreigabe mit Hinweis", async () => {
  const env = umgebung([vorschlag(1)], true);
  await env.starten();
  assert.equal(env.nodes.sperre.hidden, false);
  assert.equal(env.nodes.freigeben.disabled, true);
  assert.equal(env.nodes.liste.querySelectorAll("input")[0].disabled, true);
  env.nodes.liste.querySelectorAll("button")[0].onclick();
  assert.equal(button(env.nodes.detail, "Freigeben").disabled, true);
});

test("Drehung und Label speichern gemeinsam; Ablehnen verlangt einen Grund", async () => {
  const env = umgebung([vorschlag(1)]);
  await env.starten();
  env.nodes.liste.querySelectorAll("button")[0].onclick();
  const inputs = env.nodes.detail.querySelectorAll("input");
  inputs.find((i) => i.id === "oks-review-feld-DrehunginGrad").value = "90";
  inputs.find((i) => i.id === "oks-review-feld-Beschriftung").value = "Zufahrt West";
  env.nodes.detail.querySelectorAll("form")[0].onsubmit({ preventDefault() {} });
  await fertig();
  assert.equal(env.anfragen[0].daten.rotation, 90);
  assert.equal(env.anfragen[0].daten.label, "Zufahrt West");
  button(env.nodes.detail, "Ablehnen").onclick();
  assert.equal(env.anfragen.length, 1);
  const grund = env.nodes.detail.querySelectorAll("input").find((i) => i.id === "oks-review-feld-Ablehngrund");
  grund.value = "Falsches Symbol";
  button(env.nodes.detail, "Ablehnen").onclick();
  await fertig();
  assert.match(env.anfragen[1].url, /\/1\/ablehnen$/);
  assert.equal(env.anfragen[1].daten.grund, "Falsches Symbol");
});
