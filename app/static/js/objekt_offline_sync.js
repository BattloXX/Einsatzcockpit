/* Offline-Precaching der Objektdaten (Objektverwaltung PR9).
 *
 * Laeuft NUR in der Android-App (Capacitor-WebView, window.Capacitor vorhanden):
 * laedt periodisch das Sync-Manifest (/api/objekte/sync) und legt Einsatz-
 * ansichten, Thumbnails, Hi-Res-Seiten und Einzel-PDFs aller freigegebenen
 * Objekte in den Cache 'ec-objekt-v1'. Der Service Worker (sw.js) bedient
 * /objekt-medien/* und /objekte/<id>/einsatz offline daraus.
 *
 * Im Desktop-/Mobil-Browser laeuft KEIN Voll-Precaching (Datenvolumen!) —
 * dort cacht der SW nur besuchte Seiten (network-first, Bestandsverhalten).
 *
 * Sync-Zeitpunkte: 90 s nach App-Start, danach alle 6 h; Delta ueber die
 * URL-Menge (Seiten-Dateien sind unveraenderlich, UUID-Pfade). Entfernte
 * Seiten/Objekte werden aus dem Cache geraeumt.
 */
(function () {
  "use strict";

  var CACHE_NAME = "ec-objekt-v1";
  var SYNC_INTERVALL_MS = 6 * 60 * 60 * 1000; // 6 h
  var START_VERZOEGERUNG_MS = 90 * 1000;
  var LS_KEY = "ec_objekt_sync_zuletzt";

  function inAndroidApp() {
    try {
      return !!(window.Capacitor && window.Capacitor.getPlatform &&
                window.Capacitor.getPlatform() === "android");
    } catch (e) {
      return false;
    }
  }

  async function synchronisieren() {
    if (!("caches" in window)) { return; }
    var antwort;
    try {
      antwort = await fetch("/api/objekte/sync", { credentials: "same-origin" });
    } catch (e) {
      return; // offline — naechster Lauf versucht es erneut
    }
    if (!antwort.ok) { return; } // nicht eingeloggt / Modul aus
    var manifest = await antwort.json();

    var soll = new Set();
    (manifest.objekte || []).forEach(function (o) {
      soll.add(o.einsatz_url);
      (o.seiten || []).forEach(function (s) {
        (s.urls || []).forEach(function (u) { soll.add(u); });
      });
    });

    var cache = await caches.open(CACHE_NAME);

    // Veraltete Eintraege raeumen (geloeschte Seiten/Objekte, zurueckgezogene Objekte)
    var vorhanden = await cache.keys();
    for (var i = 0; i < vorhanden.length; i++) {
      var pfad = new URL(vorhanden[i].url).pathname;
      if (!soll.has(pfad)) { await cache.delete(vorhanden[i]); }
    }
    var vorhandenPfade = new Set(vorhanden.map(function (r) { return new URL(r.url).pathname; }));

    // Fehlende Dateien nachladen (sequentiell, um Netz/Server zu schonen)
    var urls = Array.from(soll);
    for (var j = 0; j < urls.length; j++) {
      var url = urls[j];
      var istEinsatzSeite = /^\/objekte\/\d+\/einsatz$/.test(url);
      // Einsatzansichten immer aktualisieren (HTML aendert sich), Dateien nur wenn fehlend
      if (!istEinsatzSeite && vorhandenPfade.has(url)) { continue; }
      try {
        var res = await fetch(url, { credentials: "same-origin" });
        if (res.ok) { await cache.put(url, res); }
      } catch (e) { /* einzelner Fehler stoppt den Sync nicht */ }
    }

    try { localStorage.setItem(LS_KEY, String(Date.now())); } catch (e) { /* egal */ }
  }

  function planen() {
    setTimeout(function lauf() {
      synchronisieren().catch(function () {});
      setTimeout(lauf, SYNC_INTERVALL_MS);
    }, START_VERZOEGERUNG_MS);
  }

  if (inAndroidApp()) { planen(); }

  // Manuell ausloesbar (z. B. aus den Einstellungen): window.objektOfflineSync()
  window.objektOfflineSync = synchronisieren;
})();
