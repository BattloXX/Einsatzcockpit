/* Offline-Precaching der Objektdaten (Objektverwaltung PR9).
 *
 * Laeuft NUR in der Android-App (Capacitor-WebView, window.Capacitor vorhanden):
 * laedt periodisch das Sync-Manifest (/api/objekte/sync) und legt Einsatz-
 * ansichten, Verwaltungsansichten, Thumbnails, Hi-Res-Seiten und Einzel-PDFs aller freigegebenen
 * Objekte in den Cache 'ec-objekt-v1'. Der Service Worker (sw.js) bedient
 * /objekt-medien/* und /objekte/<id>/einsatz offline daraus.
 *
 * Im Desktop-/Mobil-Browser laeuft KEIN Voll-Precaching (Datenvolumen!) —
 * dort cacht der SW nur besuchte Seiten (network-first, Bestandsverhalten).
 *
 * Sync-Zeitpunkte: direkt nach App-Start, danach alle 6 h; Delta ueber die
 * URL-Menge (Seiten-Dateien sind unveraenderlich, UUID-Pfade). Entfernte
 * Seiten/Objekte werden aus dem Cache geraeumt.
 */
(function () {
  "use strict";

  var CACHE_NAME = "ec-objekt-v1";
  var SYNC_INTERVALL_MS = 6 * 60 * 60 * 1000; // 6 h
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
    if (!("caches" in window)) { return false; }
    var antwort;
    try {
      antwort = await fetch("/api/objekte/sync", { credentials: "same-origin" });
    } catch (e) {
      return false; // offline — naechster Lauf versucht es erneut
    }
    if (!antwort.ok) { return false; } // nicht eingeloggt / Modul aus
    var manifest = await antwort.json();

    var soll = new Set();
    // Die Übersicht ist der Einstieg aus der Android-Navigation. Sie muss
    // genauso im Cache liegen wie die einzelnen Einsatzansichten, sonst
    // scheitert bereits /objekte/ bevor ein Objekt geöffnet werden kann.
    soll.add("/objekte/");
    (manifest.objekte || []).forEach(function (o) {
      // Die Listenansicht verlinkt auf die Verwaltungsansicht (/objekte/<id>),
      // die Einsatzansicht wird ebenfalls fuer die Einsatzvorbereitung gehalten.
      if (o.detail_url) { soll.add(o.detail_url); }
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
      var istObjektSeite = /^\/objekte\/\d+(\/einsatz)?$/.test(url);
      // HTML-Ansichten immer aktualisieren (Daten aendern sich), Dateien nur wenn fehlend.
      if (!istObjektSeite && vorhandenPfade.has(url)) { continue; }
      try {
        var res = await fetch(url, { credentials: "same-origin" });
        if (res.ok) { await cache.put(url, res); }
      } catch (e) { /* einzelner Fehler stoppt den Sync nicht */ }
    }

    try { localStorage.setItem(LS_KEY, String(Date.now())); } catch (e) { /* egal */ }
    return true;
  }

  function planen() {
    // Der erste erfolgreiche Login ist der einzige verlässliche Zeitpunkt,
    // bevor das Gerät ins Funkloch fährt. Die frühere 90-s-Wartezeit ließ
    // genau diesen Fall ohne Objektübersicht und Einsatzdaten zurück.
    synchronisieren().catch(function () {});
    setTimeout(function lauf() {
      synchronisieren().catch(function () {});
      setTimeout(lauf, SYNC_INTERVALL_MS);
    }, SYNC_INTERVALL_MS);
  }

  if (inAndroidApp()) { planen(); }

  // Manuell ausloesbar (z. B. aus den Einstellungen): window.objektOfflineSync()
  window.objektOfflineSync = synchronisieren;
})();
