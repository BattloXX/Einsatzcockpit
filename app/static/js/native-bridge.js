/**
 * native-bridge.js – Capacitor <-> Web Bridge für Einsatzcockpit
 *
 * Erkennt ob die App in Capacitor läuft und stellt window.ELNative bereit.
 * In der reinen PWA sind alle Funktionen No-Ops oder fallen auf Web-APIs zurück,
 * sodass die Web-App weiterhin voll funktionsfähig bleibt.
 *
 * Verfügbare Funktionen:
 *   ELNative.keepAwake(on)              – Bildschirm aktiv halten (oder freigeben)
 *   ELNative.startLocation()            – Hintergrund-GPS starten
 *   ELNative.stopLocation()             – Hintergrund-GPS stoppen
 *   ELNative.scanQr(onResult, onError)  – QR-Scanner öffnen
 *   ELNative.setBatterySaver(on)        – Energiesparmodus manuell setzen
 *   ELNative.batterySaverActive         – getter: aktueller Energiesparmodus-Status
 *   ELNative.isTracking                 – getter: GPS-Tracking läuft gerade
 *   ELNative.isNative                   – getter, jedes Mal frisch gegen window.Capacitor geprüft
 */
(function () {
  'use strict';

  // Lazy helper — wird bei jedem Aufruf frisch ausgewertet.
  // Capacitor v7 setzt window.Capacitor.isNativePlatform() (Funktion), NICHT isNative (Property).
  function _isNative() {
    return !!(
      window.Capacitor &&
      typeof window.Capacitor.isNativePlatform === 'function' &&
      window.Capacitor.isNativePlatform()
    );
  }

  // ─── GPS-Intervalle & Energiesparmodus ──────────────────────────────────────
  // Energiesparmodus reduziert nur die Frequenz – Tracking bleibt IMMER aktiv,
  // solange der Dienst-Status es erfordert. keepAwake bleibt beim Tracking
  // immer aktiv, damit Android den BackgroundGeolocation-Dienst nicht tötet.
  const _GPS_NORMAL_DISTANCE   = 20;              // Mindestbewegung (m) – Normalmodus
  const _GPS_BATTERY_DISTANCE  = 100;             // Mindestbewegung (m) – Energiesparmodus
  const _GPS_NORMAL_INTERVAL   = 3  * 60 * 1000; // Periodischer Fallback-Ping – Normal
  const _GPS_BATTERY_INTERVAL  = 10 * 60 * 1000; // Periodischer Fallback-Ping – Energiesparen
  const _DUTY_NORMAL_INTERVAL  = 60_000;          // Dienst-Status-Poll – Normal
  const _DUTY_BATTERY_INTERVAL = 120_000;         // Dienst-Status-Poll – Energiesparen

  let _batterySaver = false;
  let _isTracking   = false;  // true sobald startLocation() aufgerufen wurde

  function _effectiveDistanceFilter() { return _batterySaver ? _GPS_BATTERY_DISTANCE : _GPS_NORMAL_DISTANCE; }
  function _effectiveGpsInterval()    { return _batterySaver ? _GPS_BATTERY_INTERVAL  : _GPS_NORMAL_INTERVAL; }

  function _setBatterySaver(on) {
    const was = _batterySaver;
    _batterySaver = !!on;
    if (was === _batterySaver) return;

    if (_isTracking) {
      // GPS mit neuen Parametern neustarten – keepAwake(true) wird dabei gesetzt
      stopLocation();
      startLocation();
    } else if (_batterySaver && _isNative()) {
      // Kein Tracking aktiv: Bildschirmsperre im Sparmodus freigeben
      keepAwake(false);
    }

    // Duty-Poll-Intervall anpassen
    _startDutyPoll();
    console.log('[ELNative] Energiesparmodus:', _batterySaver ? 'aktiv' : 'inaktiv');
  }

  async function _initBattery() {
    if (!('getBattery' in navigator)) return;
    try {
      const bat = await navigator.getBattery();
      function _check() {
        _setBatterySaver(!bat.charging && bat.level < 0.20);
      }
      _check();
      bat.addEventListener('chargingchange', _check);
      bat.addEventListener('levelchange', _check);
    } catch (_) {}
  }

  // ─── FCM-Token registrieren ─────────────────────────────────────────────────
  async function _registerFcmToken() {
    if (!_isNative()) return;
    try {
      const { PushNotifications } = window.Capacitor.Plugins;
      if (!PushNotifications) return;

      const perm = await PushNotifications.requestPermissions();
      if (perm.receive !== 'granted') return;

      await PushNotifications.register();
      PushNotifications.addListener('registration', async (reg) => {
        try {
          await fetch('/api/v1/device/fcm-token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            body: JSON.stringify({ token: reg.value, platform: 'android' }),
          });
        } catch (e) {
          console.warn('[ELNative] FCM-Token-Registrierung fehlgeschlagen:', e);
        }
      });

      PushNotifications.addListener('pushNotificationActionPerformed', (action) => {
        const url = action?.notification?.data?.url;
        if (url) window.location.href = url;
      });
    } catch (e) {
      console.warn('[ELNative] PushNotifications Fehler:', e);
    }
  }

  // ─── Keep-Awake ─────────────────────────────────────────────────────────────
  function keepAwake(on) {
    if (!_isNative()) {
      if (on && 'wakeLock' in navigator) {
        navigator.wakeLock.request('screen').catch(() => {});
      }
      return;
    }
    try {
      const { KeepAwake } = window.Capacitor.Plugins;
      if (!KeepAwake) return;
      if (on) KeepAwake.keepAwake();
      else KeepAwake.allowSleep();
    } catch (e) {
      console.warn('[ELNative] KeepAwake Fehler:', e);
    }
  }

  // ─── Standort-Tracking ──────────────────────────────────────────────────────
  let _locationWatch = null;
  let _periodicGpsInterval = null;
  let _lastSentLat = null;
  let _lastSentLng = null;

  // Haversine-Distanz in Metern zwischen zwei GPS-Punkten
  function _gpsDistance(lat1, lng1, lat2, lng2) {
    const R = 6371000;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLng = (lng2 - lng1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) ** 2
      + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLng / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function _sendLocation(lat, lng, accuracy) {
    // Nur senden wenn >= 10 m Abstand zur letzten Übermittlung
    if (_lastSentLat !== null && _gpsDistance(lat, lng, _lastSentLat, _lastSentLng) < 10) return;
    _lastSentLat = lat;
    _lastSentLng = lng;
    fetch('/api/v1/device/location', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ lat, lng, accuracy }),
    }).catch(() => {});
  }

  function startLocation() {
    if (!_isNative()) return;
    if (_isTracking) return; // Schützt vor Doppelaufruf (auch bevor .then() resolvet)
    _isTracking = true;

    // keepAwake IMMER aktivieren während GPS-Tracking läuft – verhindert dass
    // Android den BackgroundGeolocation-Dienst im Hintergrund beendet.
    // Gilt auch im Energiesparmodus (GPS-Dienst hat Vorrang).
    keepAwake(true);

    try {
      const { BackgroundGeolocation } = window.Capacitor.Plugins;
      if (!BackgroundGeolocation) { _isTracking = false; return; }
      BackgroundGeolocation.addWatcher(
        {
          backgroundMessage: 'Standort wird im Einsatz übermittelt.',
          backgroundTitle: 'Einsatzcockpit',
          requestPermissions: true,
          stale: false,
          distanceFilter: _effectiveDistanceFilter(),
        },
        function callback(loc, err) {
          if (err) return;
          _sendLocation(loc.latitude, loc.longitude, loc.accuracy);
        },
      ).then((id) => { _locationWatch = id; });

      // Periodischer Fallback: aktuelle Position holen und senden wenn verändert
      if (!_periodicGpsInterval) {
        _periodicGpsInterval = setInterval(() => {
          if (!_locationWatch) return;
          navigator.geolocation.getCurrentPosition(
            (pos) => _sendLocation(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy),
            () => {},
            { timeout: 10000, maximumAge: 30000 },
          );
        }, _effectiveGpsInterval());
      }
    } catch (e) {
      _isTracking = false;
      console.warn('[ELNative] BackgroundGeolocation Fehler:', e);
    }
  }

  function stopLocation() {
    _isTracking = false;
    if (_periodicGpsInterval) {
      clearInterval(_periodicGpsInterval);
      _periodicGpsInterval = null;
    }
    _lastSentLat = null;
    _lastSentLng = null;

    // Bildschirmsperre im Sparmodus freigeben – kein Tracking mehr aktiv
    if (_isNative() && _batterySaver) keepAwake(false);

    if (!_isNative() || !_locationWatch) return;
    try {
      const { BackgroundGeolocation } = window.Capacitor.Plugins;
      if (BackgroundGeolocation && _locationWatch) {
        BackgroundGeolocation.removeWatcher({ id: _locationWatch });
        _locationWatch = null;
      }
    } catch (e) {
      console.warn('[ELNative] stopLocation Fehler:', e);
    }
  }

  // ─── QR-Scanner ─────────────────────────────────────────────────────────────
  // @capacitor-mlkit/barcode-scanning v7: scan() nutzt das Google Barcode Scanner
  // Module (Google Play Services). Vor dem ersten Aufruf muss das Modul geprüft
  // und ggf. installiert werden; ohne diese Prüfung schlägt scan() lautlos fehl.
  async function scanQr(onResult, onError) {
    function _err(msg) {
      console.warn('[ELNative] QR-Scanner Fehler:', msg);
      if (typeof onError === 'function') onError(msg);
    }

    if (!_isNative()) {
      _err('Nicht in nativer Capacitor-App (window.Capacitor fehlt oder isNative=false)');
      return;
    }

    const plugins = window.Capacitor && window.Capacitor.Plugins;
    const BarcodeScanner = plugins && plugins.BarcodeScanner;
    if (!BarcodeScanner) {
      _err('BarcodeScanner-Plugin nicht registriert');
      return;
    }

    try {
      // Google Barcode Scanner Module prüfen und ggf. installieren
      // COMPLETED=4, FAILED=5 (GoogleBarcodeScannerModuleInstallState enum)
      const { available } = await BarcodeScanner.isGoogleBarcodeScannerModuleAvailable();
      if (!available) {
        await new Promise(async (resolve, reject) => {
          const handle = await BarcodeScanner.addListener(
            'googleBarcodeScannerModuleInstallProgress',
            (event) => {
              if (event.state === 4) { handle.remove(); resolve(); }
              else if (event.state === 5) { handle.remove(); reject(new Error('Google-Modul-Installation fehlgeschlagen (state=5)')); }
            }
          );
          await BarcodeScanner.installGoogleBarcodeScannerModule();
        });
      }

      const { barcodes } = await BarcodeScanner.scan();
      if (barcodes && barcodes.length > 0 && typeof onResult === 'function') {
        onResult(barcodes[0].rawValue);
      }
    } catch (e) {
      _err(e && e.message ? e.message : String(e));
    }
  }

  // ─── Dienst-Status pollen & Tracking automatisch steuern ────────────────────
  async function _pollDutyState() {
    if (!_isNative()) return;
    try {
      const resp = await fetch('/api/v1/device/duty-state', {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      if (!resp.ok) return; // Netzwerkfehler: vorherigen Zustand behalten
      const data = await resp.json();
      if (data.should_track) startLocation();
      else stopLocation();
    } catch (_) {} // Netzwerkfehler: kein Stopp des aktiven Trackings
  }

  let _dutyPollId = null;
  function _startDutyPoll() {
    if (_dutyPollId) clearInterval(_dutyPollId);
    _dutyPollId = setInterval(() => {
      if (document.visibilityState === 'visible') _pollDutyState();
    }, _batterySaver ? _DUTY_BATTERY_INTERVAL : _DUTY_NORMAL_INTERVAL);
  }

  // ─── Initialisierung ─────────────────────────────────────────────────────────
  function _init() {
    if (_isNative()) {
      _registerFcmToken();
      _initBattery();
      _pollDutyState();
    }

    // Duty-Status-Poll starten (No-Op wenn nicht nativ)
    _startDutyPoll();

    // Sofort neu pollen wenn Netzwerk wiederkehrt (z. B. nach Tunnelausfahrt)
    window.addEventListener('online', () => { if (_isNative()) _pollDutyState(); });

    // Sofort neu pollen wenn App aus dem Hintergrund kommt
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && _isNative()) _pollDutyState();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

  // ─── Öffentliche API ─────────────────────────────────────────────────────────
  window.ELNative = {
    get isNative() { return _isNative(); },
    keepAwake,
    startLocation,
    stopLocation,
    scanQr,
    setBatterySaver(on) { _setBatterySaver(on); },
    get batterySaverActive() { return _batterySaver; },
    get isTracking() { return _isTracking; },
  };
})();
