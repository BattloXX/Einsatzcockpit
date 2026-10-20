import test from "node:test";
import assert from "node:assert/strict";
import { berechneReihenfolge, esc, markerArt, markerDarstellung, navUrl } from "../../app/static/js/einheit_karte.js";

const auftrag = (id, extra = {}) => ({
  dispatch_id: id, rang: id, priority: "normal", lat: 47 + id / 100, lng: 13, ...extra,
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
  assert.equal(markerArt(auftrag(1, { auftragsart: "erkundung" })), "erkundung");
  assert.equal(markerArt(auftrag(1, { einheit_status: "abgeschlossen" })), "fertig");
  assert.equal(markerArt(auftrag(1, { zurueckgezogen: true })), "zurueckgezogen");
});

test("markerDarstellung nutzt kompakte, zugängliche Statusmarker", () => {
  const naechster = markerDarstellung(auftrag(1, { naechster: true, adresse: "Hauptstraße 1" }), 1);
  const laufend = markerDarstellung(auftrag(2, { einheit_status: "in_arbeit" }), 2);
  const erkundung = markerDarstellung(auftrag(3, { auftragsart: "erkundung" }), 3);
  const offen = markerDarstellung(auftrag(4), 4);
  assert.equal(naechster.iconName, "player-play");
  assert.equal(laufend.iconName, "player-record");
  assert.equal(markerDarstellung(auftrag(5, { beendet_at: "2026-10-20" }), null).iconName, "check");
  assert.match(naechster.titel, /Nächster Auftrag/);
  assert.match(laufend.titel, /In Arbeit/);
  assert.ok(laufend.groesse <= 34);
  assert.deepEqual(laufend.anker, [laufend.groesse / 2, laufend.groesse / 2]);
  assert.match(erkundung.klasse, /--erkundung/);
  assert.equal(offen.inhalt, "4");
});

test("navUrl nutzt Koordinaten, Adresse oder null", () => {
  assert.match(navUrl(auftrag(1)), /destination=47.01,13/);
  assert.match(navUrl({ adresse: "Hauptstraße 1" }), /Hauptstra%C3%9Fe/);
  assert.equal(navUrl({}), null);
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

test("Marker-HTML rendert Statusicons und entschärft Titel weiterhin", async () => {
  const { EinheitKarte } = await import("../../app/static/js/einheit_karte.js");
  const oldWindow = globalThis.window;
  const oldIcon = globalThis.ecpIcon;
  globalThis.window = { L: { divIcon: options => options } };
  globalThis.ecpIcon = (name, {size}) => `<svg data-icon="${name}" data-size="${size}"></svg>`;
  try {
    const marker = EinheitKarte.prototype.icon(auftrag(1, { naechster: true, bezeichnung: '<img src="x">' }), 1);
    assert.match(marker.html, /<svg data-icon="player-play" data-size="xs">/);
    assert.ok(!marker.html.includes('<img'));
    assert.match(marker.html, /&lt;img src=&quot;x&quot;&gt;/);
    assert.deepEqual(marker.iconAnchor, [14, 14]);
    const nummer = EinheitKarte.prototype.icon(auftrag(2), 2);
    assert.match(nummer.html, />2<\/span>/);
  } finally {
    if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow;
    if (oldIcon === undefined) delete globalThis.ecpIcon; else globalThis.ecpIcon = oldIcon;
  }
});
