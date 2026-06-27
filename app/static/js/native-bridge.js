/**
 * native-bridge.js – Capacitor ↔ Web Bridge für Einsatzcockpit
 *
 * Erkennt ob die App in Capacitor läuft und stellt window.ELNative bereit.
 * In der reinen PWA sind alle Funktionen No-Ops oder fallen auf Web-APIs zurück,
 * sodass die Web-App weiterhin voll funktionsfähig bleibt.
 *
 * Verfügbare Funktionen:
 *   ELNative.keepAwake(on)              – Bildschirm aktiv halten (oder freigeben)
 *   ELNative.startLocation()            – Hintergrund-GPS starten
 *   ELNative.stopLocation()             – Hintergrund-GPS stoppen
 *   ELNative.scanQr(onResult, onError)  – QR-Scanner öffnen; onResult(url) bei Erfolg,
 *                                         onError(msg) bei Fehler
 *   ELNative.isNative                   – getter, jedes Mal frisch gegen window.Capacitor geprüft
 */
(function () {
  'use strict';

  // Lazy helper — wird bei jedem Aufruf frisch ausgewertet.
  // Capacitor v7 setzt window.Capacitor.isNativePlatform() (Funktion), NICHT isNative (Property).
  // isNative existiert in v7 nicht und ist immer undefined/falsy.
  function _isNative() {
    return !!(
      window.Capacitor &&
      typeof window.Capacitor.isNativePlatform === 'function' &&
      window.Capacitor.isNativePlatform()
    );
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
    try {
      const { BackgroundGeolocation } = window.Capacitor.Plugins;
      if (!BackgroundGeolocation) return;
      BackgroundGeolocation.addWatcher(
        {
          backgroundMessage: 'Standort wird im Einsatz übermittelt.',
          backgroundTitle: 'Einsatzcockpit',
          requestPermissions: true,
          stale: false,
          distanceFilter: 20,
        },
        function callback(loc, err) {
          if (err) return;
          _sendLocation(loc.latitude, loc.longitude, loc.accuracy);
        },
      ).then((id) => { _locationWatch = id; });

      // Periodischer Fallback alle 3 Minuten: aktuelle Position holen und senden wenn verändert
      if (!_periodicGpsInterval) {
        _periodicGpsInterval = setInterval(() => {
          if (!_locationWatch) return;
          navigator.geolocation.getCurrentPosition(
            (pos) => _sendLocation(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy),
            () => {},
            { timeout: 10000, maximumAge: 30000 },
          );
        }, 3 * 60 * 1000);
      }
    } catch (e) {
      console.warn('[ELNative] BackgroundGeolocation Fehler:', e);
    }
  }

  function stopLocation() {
    if (_periodicGpsInterval) {
      clearInterval(_periodicGpsInterval);
      _periodicGpsInterval = null;
    }
    _lastSentLat = null;
    _lastSentLng = null;
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
  // onResult(url)  – wird mit der gescannten URL aufgerufen
  // onError(msg)   – wird bei jedem Fehler aufgerufen (optional)
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
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.should_track) startLocation();
      else stopLocation();
    } catch (_) {}
  }

  // Alle 60 Sekunden prüfen (nur wenn Tab sichtbar)
  setInterval(() => {
    if (document.visibilityState === 'visible') _pollDutyState();
  }, 60_000);

  // ─── Initialisierung ─────────────────────────────────────────────────────────
  // Warten bis DOM bereit – Capacitor-Bridge ist dann sicher injiziert.
  function _init() {
    if (_isNative()) {
      _registerFcmToken();
      _pollDutyState();
    }
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
  };
})();
