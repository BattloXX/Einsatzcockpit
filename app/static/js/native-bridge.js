/**
 * native-bridge.js – Capacitor ↔ Web Bridge für einsatzleiter.cloud
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
  // Capacitor Remote-Mode injiziert den Bridge-JS asynchron (addDocumentStartJavaScript)
  // und auf älteren Geräten / WebView-Versionen evtl. erst nach dem ersten Script-Lauf.
  function _isNative() {
    return !!(window.Capacitor && window.Capacitor.isNative);
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

  function startLocation() {
    if (!_isNative()) return;
    try {
      const { BackgroundGeolocation } = window.Capacitor.Plugins;
      if (!BackgroundGeolocation) return;
      BackgroundGeolocation.addWatcher(
        {
          backgroundMessage: 'Standort wird im Einsatz übermittelt.',
          backgroundTitle: 'einsatzleiter.cloud',
          requestPermissions: true,
          stale: false,
          distanceFilter: 20,
        },
        function callback(loc, err) {
          if (err) return;
          fetch('/api/v1/device/location', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            body: JSON.stringify({ lat: loc.latitude, lng: loc.longitude, accuracy: loc.accuracy }),
          }).catch(() => {});
        },
      ).then((id) => { _locationWatch = id; });
    } catch (e) {
      console.warn('[ELNative] BackgroundGeolocation Fehler:', e);
    }
  }

  function stopLocation() {
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
