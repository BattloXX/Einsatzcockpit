/* Offline-Precaching der Objektdaten (Objektverwaltung PR9).
 *
 * Laeuft NUR in der Android-App (Capacitor-WebView, window.Capacitor vorhanden):
 * laedt periodisch das Sync-Manifest (/api/objekte/sync) und legt Einsatz-
 * ansichten, Verwaltungsansichten, Thumbnails, Hi-Res-Seiten und Einzel-PDFs aller freigegebenen
 * Objekte in den Cache 'ec-objekt-v2'. Der Service Worker (sw.js) bedient
 * /objekt-medien/* und /objekte/<id>/einsatz offline daraus.
 *
 * Im Desktop-/Mobil-Browser laeuft KEIN Voll-Precaching (Datenvolumen!) —
 * dort cacht der SW nur besuchte Seiten (network-first, Bestandsverhalten).
 *
 * Sync-Zeitpunkte: direkt nach App-Start, danach alle 6 h; Delta ueber die
 * Medienversionen (mit URL-Menge als Fallback fuer alte Manifeste). Entfernte
 * Seiten/Objekte werden aus dem Cache geraeumt.
 */
(function () {
  "use strict";

  var CACHE_NAME = "ec-objekt-v2";
  var SYNC_INTERVALL_MS = 6 * 60 * 60 * 1000; // 6 h
  var LS_KEY = "ec_objekt_sync_zuletzt";
  var MEDIEN_KEY = "ec_objekt_medien_stand";
  var MIN_INTERVALL_MS = 30 * 60 * 1000;
  var laufenderSync = null;

  function pathname(url) { return new URL(url, window.location.origin).pathname; }

  function medienStandLesen() {
    try {
      var stand = JSON.parse(localStorage.getItem(MEDIEN_KEY) || "{}");
      return stand && typeof stand === "object" && !Array.isArray(stand) ? stand : {};
    } catch (e) { return {}; }
  }

  function medienStandSpeichern(stand) {
    try { localStorage.setItem(MEDIEN_KEY, JSON.stringify(stand)); } catch (e) { /* optional */ }
  }

  function synchronisieren(optionen) {
    if (laufenderSync) { return laufenderSync; }
    laufenderSync = syncAusfuehren(optionen || {}).finally(function () { laufenderSync = null; });
    return laufenderSync;
  }
  var DETAIL_FRAGMENTE = [
    "stammdaten", "gefahren", "bma", "merkmale", "kontakte",
    "benachrichtigung", "wohnanlage", "zusatzadressen", "einsaetze", "protokoll",
  ];

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

  async function objektIstImCache(cache, objekt) {
    var detailReady = !objekt.detail_url || await cache.match(objekt.detail_url);
    var einsatzReady = await cache.match(objekt.einsatz_url);
    return detailReady && einsatzReady;
  }

  async function syncAusfuehren(optionen) {
    // Nur der Aufruf beim Seitenaufruf (planen) wird entprellt: jeder Seitenaufruf in
    // der App wuerde sonst alle Objektansichten erneut laden. WorkManager (6 h,
    // Login, manuelles Aktualisieren) ruft ohne Option auf und synchronisiert immer;
    // dank Versionsstand ist das ein guenstiges Delta.
    try {
      var zuletzt = Number(localStorage.getItem(LS_KEY));
      var alter = Date.now() - zuletzt;
      if (optionen.entprellen && !optionen.force && zuletzt > 0 && alter >= 0 && alter < MIN_INTERVALL_MS) {
        cacheStatus(0, 0, "Objekt-Sync übersprungen: letzter erfolgreicher Lauf vor weniger als 30 Minuten");
        return true;
      }
    } catch (e) { /* Ohne localStorage weiterhin synchronisieren. */ }
    if (!await syncIstAktiv()) {
      cacheStatus(0, 0, "Objekt-Sync ist deaktiviert");
      return true;
    }
    if (!("caches" in window)) { return false; }
    if (window.ecKontaktCacheReady) { await window.ecKontaktCacheReady; }
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
    var objektPfade = [];
    var medienVersionen = new Map();
    var fetchUrls = new Map();
    objektListe.forEach(function (o) {
      var pfade = [];
      // Die Listenansicht verlinkt auf die Verwaltungsansicht (/objekte/<id>),
      // die Einsatzansicht wird ebenfalls fuer die Einsatzvorbereitung gehalten.
      if (o.detail_url) {
        soll.add(o.detail_url);
        pfade.push(o.detail_url);
        DETAIL_FRAGMENTE.forEach(function (fragment) {
          if (manifest.kontaktverzeichnis_erlaubt === false
              && ["kontakte", "protokoll"].includes(fragment)) { return; }
          var fragmentPfad = o.detail_url + "/" + fragment;
          soll.add(fragmentPfad);
          pfade.push(fragmentPfad);
        });
      }
      soll.add(o.einsatz_url);
      pfade.push(o.einsatz_url);
      (o.seiten || []).forEach(function (s) {
        (s.urls || []).forEach(function (u) {
          var pfad = pathname(u);
          soll.add(pfad);
          pfade.push(pfad);
          // Beide additiven Felder muessen vorhanden sein, sonst alte Logik.
          var versionierteUrl = (s.versioned_urls || []).find(function (v) { return pathname(v) === pfad; });
          if (s.version && versionierteUrl) {
            medienVersionen.set(pfad, s.version);
            fetchUrls.set(pfad, versionierteUrl);
          } else { fetchUrls.set(pfad, u); }
        });
      });
      objektPfade.push(pfade);
    });
    kontaktPfade.forEach(function (pfad) { soll.add(pfad); });

    // Cache-Schluessel und Mengenvergleiche verwenden stets denselben Pfad,
    // auch bei Query-Parametern in aelteren Manifesten/Kontakt-URLs.
    soll = new Set(Array.from(soll).map(pathname));
    objektPfade = objektPfade.map(function (pfade) { return pfade.map(pathname); });
    kontaktPfade.forEach(function (url) { fetchUrls.set(pathname(url), url); });
    var medienStand = medienStandLesen();
    Object.keys(medienStand).forEach(function (pfad) {
      if (!soll.has(pfad)) { delete medienStand[pfad]; }
    });
    medienStandSpeichern(medienStand);
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
    var objektEnden = {};
    objektPfade.forEach(function (pfade, objektIndex) {
      var urlIndex = -1;
      pfade.forEach(function (pfad) {
        urlIndex = Math.max(urlIndex, urls.indexOf(pfad));
      });
      if (urlIndex < 0) { return; }
      if (!objektEnden[urlIndex]) { objektEnden[urlIndex] = []; }
      objektEnden[urlIndex].push(objektIndex);
    });
    var objektImCache = [];
    var cachedObjects = 0;
    for (var k = 0; k < objektListe.length; k++) {
      objektImCache[k] = await objektIstImCache(cache, objektListe[k]);
      if (objektImCache[k]) { cachedObjects++; }
    }
    cacheStatus(
      cachedObjects,
      objektListe.length,
      "Objekte werden synchronisiert … " + cachedObjects + "/" + objektListe.length + " offline verfügbar",
    );
    var failedDownloads = 0;
    var geladen = 0;
    var uebersprungen = 0;
    var bytesGeladen = 0;
    var fehlertexte = [];
    function statistik() {
      return " · Geladen: " + geladen + " · Übersprungen: " + uebersprungen
        + " · Bytes geladen: " + bytesGeladen + " · Fehler: " + failedDownloads
        + (fehlertexte.length ? " · " + fehlertexte.join("; ") : "");
    }
    for (var j = 0; j < urls.length; j++) {
      var url = urls[j];
      var istDynamischeSeite = /^\/objekte\/\d+(\/(einsatz|stammdaten|gefahren|bma|merkmale|kontakte|benachrichtigung|wohnanlage|zusatzadressen|einsaetze|protokoll))?$/.test(url)
        || url === "/kontakte" || /^\/kontakte\/\d+(\/profilbild)?$/.test(url);
      // HTML-Ansichten und Kontaktbilder immer aktualisieren, Dateien nur wenn fehlend.
      var version = medienVersionen.get(url);
      var hatEintrag = version ? !!await cache.match(url) : vorhandenPfade.has(url);
      if (istDynamischeSeite || !hatEintrag
          || (version && medienStand[url] !== version)) {
        try {
          // Kein cache:"reload"/"no-store": der kurzlebige WebView darf den
          // persistenten HTTP-Cache mit privaten immutable-Antworten verwenden.
          var res = await fetch(fetchUrls.get(url) || url, { credentials: "same-origin" });
          if (!res.ok) {
            await cache.delete(url);
            throw new Error("HTTP " + res.status + " " + url);
          }
          var groesse = 0;
          // Sequentiell: jeweils nur ein zusaetzliches Blob fuer die Diagnose.
          try { groesse = (await res.clone().blob()).size; } catch (e) { /* optional */ }
          await cache.put(url, res);
          geladen++;
          bytesGeladen += groesse;
          if (version) {
            medienStand[url] = version;
            medienStandSpeichern(medienStand);
          }
        } catch (e) {
          failedDownloads++;
          var fehler = (e && e.name ? e.name + ": " : "") + (e && e.message ? e.message : String(e));
          if (fehlertexte.length < 3 && !fehlertexte.includes(fehler)) { fehlertexte.push(fehler); }
          cacheStatus(cachedObjects, objektListe.length, "Objekt-Sync: " + url + statistik());
        }
      } else { uebersprungen++; }

      // Nach jedem Objekt den echten Cache-Stand melden. Wird die kurzlebige
      // WorkManager-WebView beendet, bleibt so der letzte Zwischenstand sichtbar.
      var fertigeObjekte = objektEnden[j];
      if (fertigeObjekte) {
        for (var l = 0; l < fertigeObjekte.length; l++) {
          var objektIndex = fertigeObjekte[l];
          if (!objektImCache[objektIndex]
              && await objektIstImCache(cache, objektListe[objektIndex])) {
            objektImCache[objektIndex] = true;
            cachedObjects++;
          }
        }
        cacheStatus(
          cachedObjects,
          objektListe.length,
          "Objekte werden synchronisiert … " + cachedObjects + "/" + objektListe.length + " offline verfügbar" + statistik(),
        );
      }
    }

    cachedObjects = 0;
    for (var m = 0; m < objektListe.length; m++) {
      if (await objektIstImCache(cache, objektListe[m])) { cachedObjects++; }
    }
    cacheStatus(
      cachedObjects,
      objektListe.length,
      "Objekte aktualisiert: " + cachedObjects + "/" + objektListe.length + " offline verfügbar"
        + statistik(),
    );

    if (!failedDownloads) {
      try { localStorage.setItem(LS_KEY, String(Date.now())); } catch (e) { /* optional */ }
    }
    // Wie bisher true: ein einzelner Fehler soll den WorkManager nicht in eine Wiederholungsschleife schicken.
    return true;
  }

  function planen() {
    // Der erste erfolgreiche Login ist der einzige verlässliche Zeitpunkt,
    // bevor das Gerät ins Funkloch fährt. Die frühere 90-s-Wartezeit ließ
    // genau diesen Fall ohne Objektübersicht und Einsatzdaten zurück.
    synchronisieren({ entprellen: true }).catch(function () {});
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
    if (window.ecKontaktCacheReady) { await window.ecKontaktCacheReady; }
    try {
      await caches.delete(CACHE_NAME);
      try {
        localStorage.removeItem(LS_KEY);
        localStorage.removeItem(MEDIEN_KEY);
      } catch (e) { /* optional */ }
      cacheStatus(0, 0, "Objektcache wurde gelöscht");
      return true;
    } catch (e) {
      return false;
    }
  };
})();
