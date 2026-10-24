import test from "node:test";
import assert from "node:assert/strict";
import { berechneReihenfolge, esc, hatKoordinaten, markerArt, markerDarstellung, navUrl } from "../../app/static/js/einheit_karte.js";

const auftrag = (id, extra = {}) => ({
  dispatch_id: id, rang: id, auftragsart: "abarbeitung", priority: "normal", lat: 47 + id / 100, lng: 13, ...extra,
});

test("berechneReihenfolge nutzt ohne Standort die Serverränge", () => {
  const reihe = berechneReihenfolge([auftrag(3), auftrag(1), auftrag(2)]);
  assert.deepEqual(reihe.map((item) => item.dispatch_id), [1, 2, 3]);
});

test("Entfernung ordnet nur gleiche Priorität ohne Reihenfolge um", () => {
  const reihe = berechneReihenfolge([
    auftrag(1, { reihenfolge: 1 }), auftrag(2, { lat: 48 }), auftrag(3, { lat: 47.01 }),
  ], { lat: 47, lng: 13 });
  assert.deepEqual(reihe.map((item) => item.dispatch_id), [1, 3, 2]);
  assert.equal(reihe[1].vorschlag_entfernung, true);
});

test("Entfernung verschiebt weder laufenden noch höhere Priorität", () => {
  const reihe = berechneReihenfolge([
    auftrag(1, { aktuell: true, einheit_status: "in_arbeit", lat: 49 }),
    auftrag(2, { priority: "sofort", lat: 49 }), auftrag(3, { priority: "normal", lat: 47.001 }),
  ], { lat: 47, lng: 13 });
  assert.deepEqual(reihe.map((item) => item.dispatch_id), [1, 2, 3]);
});

test("Aufträge mit Reihenfolge behalten ihre relative Reihenfolge", () => {
  const reihe = berechneReihenfolge([
    auftrag(1, { reihenfolge: 1 }), auftrag(2, { reihenfolge: 2 }), auftrag(3, { lat: 47.001 }),
  ], { lat: 47, lng: 13 });
  assert.deepEqual(reihe.map((item) => item.dispatch_id), [1, 2, 3]);
});

test("markerArt unterscheidet Kartenstatus", () => {
  assert.equal(markerArt(auftrag(1, { naechster: true })), "naechster");
  assert.equal(markerArt(auftrag(1, { einheit_status: "in_arbeit" })), "in_arbeit");
  assert.equal(markerArt(auftrag(1, { auftragsart: "erkundung" })), "offen");
  assert.equal(markerArt(auftrag(1, { einheit_status: "abgeschlossen" })), "fertig");
  assert.equal(markerArt(auftrag(1, { zurueckgezogen: true })), "zurueckgezogen");
});

test("markerDarstellung trennt Art, Status und Priorität bei mindestens 48px Touchfläche", () => {
  const naechster = markerDarstellung(auftrag(1, { naechster: true, adresse: "Hauptstraße 1" }), 1);
  const laufend = markerDarstellung(auftrag(2, { einheit_status: "in_arbeit" }), 2);
  const erkundung = markerDarstellung(auftrag(3, { auftragsart: "erkundung" }), 3);
  const offen = markerDarstellung(auftrag(4), 4);
  assert.equal(naechster.iconName, "tools");
  assert.equal(naechster.statusIconName, "player-play");
  assert.equal(laufend.iconName, "tools");
  assert.equal(laufend.statusIconName, "player-record");
  assert.equal(erkundung.iconName, "search");
  assert.equal(markerDarstellung(auftrag(5, { beendet_at: "2026-10-20" }), null).statusIconName, "check");
  assert.match(naechster.titel, /Nächster Auftrag/);
  assert.match(laufend.titel, /In Arbeit/);
  assert.ok(laufend.groesse >= 48);
  assert.deepEqual(laufend.anker, [laufend.groesse / 2, laufend.groesse / 2]);
  assert.match(erkundung.klasse, /--erkundung/);
  assert.equal(offen.inhalt, "4");
});

test("navUrl nutzt Koordinaten, Adresse oder null", () => {
  assert.match(navUrl(auftrag(1)), /destination=47.01,13/);
  assert.match(navUrl({ adresse: "Hauptstraße 1" }), /Hauptstra%C3%9Fe/);
  assert.equal(navUrl({}), null);
});

test("Koordinatenprüfung akzeptiert Nullmeridian und verwirft ungültige Alt-/Offline-Daten", () => {
  for (const ort of [{ lat: 0, lng: 0 }, { lat: -90, lng: 180 }, { lat: "47.5", lng: "9" }]) {
    assert.equal(hatKoordinaten(ort), true);
  }
  for (const ort of [null, {}, { lat: null, lng: 0 }, { lat: "", lng: 0 }, { lat: " ", lng: 0 },
    { lat: false, lng: 0 }, { lat: [], lng: 0 }, { lat: NaN, lng: 0 }, { lat: Infinity, lng: 0 },
    { lat: 91, lng: 0 }, { lat: 0, lng: -181 }]) {
    assert.equal(hatKoordinaten(ort), false);
  }
  assert.equal(navUrl({ lat: 91, lng: 12, adresse: "A & B", nav_url: "javascript:alert(1)" }),
    "https://www.google.com/maps/dir/?api=1&destination=A%20%26%20B");
  assert.equal(navUrl({ nav_url: "javascript:alert(1)" }), null);
  assert.equal(navUrl({ adresse: " " }), null);
});

test("nächste oder laufende Erkundung behält ihre Hervorhebung", () => {
  assert.equal(markerArt(auftrag(1, { auftragsart: "erkundung", naechster: true })), "naechster");
  assert.equal(markerArt(auftrag(1, { auftragsart: "erkundung", aktuell: true })), "in_arbeit");
  assert.equal(markerArt(auftrag(1, { einheit_status: "nicht_durchfuehrbar" })), "fertig");
});

test("esc entschärft HTML aus Einsatzdaten (Popup)", () => {
  assert.equal(esc('<img src=x onerror="alert(1)">'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  assert.equal(esc(null), "");
});

test("Entfernungsvorschlag verschiebt nie den nächsten Auftrag", () => {
  const liste = [
    auftrag(1, { rang: 1, naechster: true, lat: 47.0, lng: 9.0 }),
    auftrag(2, { rang: 2, lat: 47.5, lng: 9.5 }),
    auftrag(3, { rang: 3, lat: 47.49, lng: 9.49 }),
  ];
  const ergebnis = berechneReihenfolge(liste, { lat: 47.5, lng: 9.5 });
  assert.equal(ergebnis[0].dispatch_id, 1);
});

test("Marker-HTML rendert Art und Status separat und entschärft Titel weiterhin", async () => {
  const { EinheitKarte } = await import("../../app/static/js/einheit_karte.js");
  const oldWindow = globalThis.window;
  const oldIcon = globalThis.ecpIcon;
  globalThis.ecpIcon = (name, {size}) => `<svg data-icon="${name}" data-size="${size}"></svg>`;
  globalThis.window = { L: { divIcon: options => options }, ecpIcon: globalThis.ecpIcon };
  try {
    const marker = EinheitKarte.prototype.icon(auftrag(1, { naechster: true, bezeichnung: '<img src="x">' }), 1);
    assert.match(marker.html, /<svg data-icon="player-play" data-size="xs">/);
    assert.match(marker.html, /<svg data-icon="tools" data-size="sm">/);
    assert.match(marker.html, />AUFTRAG<\/span>/);
    assert.ok(!marker.html.includes('<img'));
    assert.match(marker.html, /&lt;img src=&quot;x&quot;&gt;/);
    assert.deepEqual(marker.iconAnchor, [24, 24]);
    const nummer = EinheitKarte.prototype.icon(auftrag(2), 2);
    assert.match(nummer.html, />2 · Offen<\/span>/);
    const popup = EinheitKarte.prototype.popup(auftrag(3, {
      auftragsart: "erkundung", auftragsart_label: "Falsches Altlabel", priority: "sofort",
      bezeichnung: '<img src="x">', adresse: '<script>alert(1)</script>',
    }));
    assert.match(popup, /ERKUNDUNG/);
    assert.match(popup, /data-icon="search"/);
    assert.match(popup, /Priorität: Sofort/);
    assert.ok(!popup.includes("Falsches Altlabel"));
    assert.ok(!popup.includes("<img") && !popup.includes("<script"));
  } finally {
    if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow;
    if (oldIcon === undefined) delete globalThis.ecpIcon; else globalThis.ecpIcon = oldIcon;
  }
});

test("alle Art-/Prioritätskombinationen und Alt-Payloads behalten ihre unabhängige Markierung", () => {
  for (const auftragsart of ["erkundung", "abarbeitung"]) {
    for (const priority of ["sofort", "dringend", "normal", "aufschiebbar"]) {
      for (const status of [{}, { naechster: true }, { aktuell: true }, { beendet_at: "2026-10-24" }]) {
        const daten = markerDarstellung(auftrag(1, { auftragsart, priority, ...status }), 1);
        assert.match(daten.klasse, new RegExp(`einheit-prio--${priority}`));
        assert.match(daten.klasse, new RegExp(`einheit-art--${auftragsart}`));
        assert.equal(daten.iconName, auftragsart === "erkundung" ? "search" : "tools");
      }
    }
  }
  const alt = markerDarstellung({ priority: "sofort", auftragsart_label: "Erkundung", einsatzgrund: "Erkunden" }, 1);
  assert.equal(alt.label, "ART UNBEKANNT");
  assert.equal(alt.iconName, "help-circle");
  assert.match(alt.klasse, /einheit-prio--sofort/);
});
