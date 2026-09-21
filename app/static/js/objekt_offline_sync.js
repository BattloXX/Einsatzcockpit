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

  function cacheStatus(cached, total, activity) {
    // Der WorkManager-Sync läuft in einer rohen Android-WebView ohne
    // Capacitor-Bridge. Er stellt diese Schnittstelle direkt bereit, damit die
    // Diagnose auch dann in "Über die App" ankommt.
    var nativeSync = window.ObjektSyncNative || window.ObjektCacheClearNative;
    if (nativeSync && typeof nativeSync.reportStatus === "function") {
      nativeSync.reportStatus(cached, total, activity);
      return;
    }
    var plugin = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.DeviceKeepalive;
    if (!plugin || typeof plugin.reportObjectCacheStatus !== "function") { return; }
    plugin.reportObjectCacheStatus({ cached: cached, total: total, activity: activity }).catch(function () {});
  }

  async function syncIstAktiv() {
    var plugin = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.DeviceKeepalive;
    // Ältere App-Versionen kennen die Einstellung noch nicht und behalten ihr
    // bisheriges Verhalten. Die neue Android-App liefert die Präferenz nativ.
    if (!plugin || typeof plugin.getObjectSyncSettings !== "function") { return true; }
    try {
      var settings = await plugin.getObjectSyncSettings();
      return settings.enabled === true && settings.clearing !== true;
    } catch (e) {
      return false;
    }
  }

  async function kontaktUrls() {
    try {
      var antwort = await fetch("/kontakte/offline-sync", { credentials: "same-origin" });
      if (!antwort.ok) { return []; }
      var manifest = await antwort.json();
      return Array.isArray(manifest.urls) ? manifest.urls : [];
    } catch (e) {
      // Das Kontakte-Modul kann deaktiviert sein; der Objekt-Sync bleibt davon unabhaengig.
      return [];
    }
  }

  async function synchronisieren() {
    if (!await syncIstAktiv()) {
      cacheStatus(0, 0, "Objekt-Sync ist deaktiviert");
      return true;
    }
    if (!("caches" in window)) { return false; }
    cacheStatus(0, 0, "Objektcache wird auf Aktualisierungen geprüft …");
    var antwort;
    try {
      antwort = await fetch("/api/objekte/sync", { credentials: "same-origin" });
    } catch (e) {
      cacheStatus(0, 0, "Objekt-Sync fehlgeschlagen: Netzwerk nicht erreichbar");
      return false; // offline — naechster Lauf versucht es erneut
    }
    if (!antwort.ok) {
      cacheStatus(0, 0, "Objekt-Sync fehlgeschlagen: Server antwortet mit HTTP " + antwort.status);
      return false; // nicht eingeloggt / Modul aus
    }
    var manifest;
    try {
      manifest = await antwort.json();
    } catch (e) {
      cacheStatus(0, 0, "Objekt-Sync fehlgeschlagen: Sync-Antwort ist ungültig");
      return false;
    }
    var objektListe = Array.isArray(manifest.objekte) ? manifest.objekte : [];
    var diagnose = manifest.diagnostics || {};
    var statusZaehler = diagnose.productive_by_status || {};
    var statusInfo = Object.keys(statusZaehler).sort().map(function (status) {
      return status + ": " + statusZaehler[status];
    }).join(" · ");
    var auswahlInfo = diagnose.included_statuses
      ? " (Status: " + diagnose.included_statuses.join(", ") + ")"
      : "";
    var orgInfo = diagnose.org_id ? " · Org: " + diagnose.org_id : "";
    var loginInfo = diagnose.login_type ? " · Login: " + diagnose.login_type : "";
    var manifestInfo = objektListe.length
      ? "Objektmanifest: " + objektListe.length + " Objekte ausgewählt" + auswahlInfo + orgInfo + loginInfo
      : "Objektmanifest enthält keine auswählbaren Objekte" + orgInfo + loginInfo
        + (statusInfo ? " · Vorhanden: " + statusInfo : "");
    cacheStatus(0, objektListe.length, manifestInfo);
    cacheStatus(0, objektListe.length, "Objektdaten werden für die Offline-Nutzung vorbereitet … · " + manifestInfo);
    var kontaktPfade = await kontaktUrls();

    var soll = new Set();
    // Die Übersicht ist der Einstieg aus der Android-Navigation. Sie muss
    // genauso im Cache liegen wie die einzelnen Einsatzansichten, sonst
    // scheitert bereits /objekte/ bevor ein Objekt geöffnet werden kann.
    soll.add("/objekte/");
    objektListe.forEach(function (o) {
      // Die Listenansicht verlinkt auf die Verwaltungsansicht (/objekte/<id>),
      // die Einsatzansicht wird ebenfalls fuer die Einsatzvorbereitung gehalten.
      if (o.detail_url) { soll.add(o.detail_url); }
      soll.add(o.einsatz_url);
      (o.seiten || []).forEach(function (s) {
        (s.urls || []).forEach(function (u) { soll.add(u); });
      });
    });
    kontaktPfade.forEach(function (pfad) { soll.add(pfad); });

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
    var failedDownloads = 0;
    for (var j = 0; j < urls.length; j++) {
      var url = urls[j];
      var istDynamischeSeite = /^\/objekte\/\d+(\/einsatz)?$/.test(url)
        || url === "/kontakte" || /^\/kontakte\/\d+(\/profilbild)?$/.test(url);
      // HTML-Ansichten und Kontaktbilder immer aktualisieren, Dateien nur wenn fehlend.
      if (!istDynamischeSeite && vorhandenPfade.has(url)) { continue; }
      try {
        var res = await fetch(url, { credentials: "same-origin" });
        if (res.ok) { await cache.put(url, res); }
        else { failedDownloads++; }
      } catch (e) { failedDownloads++; /* einzelner Fehler stoppt den Sync nicht */ }
    }

    var cachedObjects = 0;
    for (var k = 0; k < objektListe.length; k++) {
      var objekt = objektListe[k];
      var detailReady = !objekt.detail_url || await cache.match(objekt.detail_url);
      var einsatzReady = await cache.match(objekt.einsatz_url);
      if (detailReady && einsatzReady) { cachedObjects++; }
    }
    cacheStatus(
      cachedObjects,
      objektListe.length,
      "Objekte aktualisiert: " + cachedObjects + "/" + objektListe.length + " offline verfügbar"
        + (failedDownloads ? " · " + failedDownloads + " Dateien konnten nicht geladen werden" : ""),
    );

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
  window.objektOfflineCacheLeeren = async function () {
    if (!("caches" in window)) { return false; }
    try {
      await caches.delete(CACHE_NAME);
      try { localStorage.removeItem(LS_KEY); } catch (e) { /* egal */ }
      cacheStatus(0, 0, "Objektcache wurde gelöscht");
      return true;
    } catch (e) {
      return false;
    }
  };
})();
