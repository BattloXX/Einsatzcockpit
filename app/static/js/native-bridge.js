/**
 * native-bridge.js – Capacitor <-> Web Bridge für Einsatzcockpit
 *
 * Erkennt ob die App in Capacitor läuft und stellt window.ELNative bereit.
 * In der reinen PWA sind alle Funktionen No-Ops oder fallen auf Web-APIs zurück,
 * sodass die Web-App weiterhin voll funktionsfähig bleibt.
 *
 * Verfügbare Funktionen:
 *   ELNative.downloadFile(url, filename?) – Datei nativ nach Downloads laden
 *   ELNative.openFile(url)               – Datei mit externer App öffnen
 *   ELNative.keepAwake(on)              – Bildschirm aktiv halten (oder freigeben)
 *   ELNative.startLocation()            – Hintergrund-GPS starten
 *   ELNative.stopLocation()             – Hintergrund-GPS stoppen
 *   ELNative.scanQr(onResult, onError)  – QR-Scanner öffnen
 *   ELNative.setBatterySaver(on)        – Energiesparmodus manuell setzen
 *   ELNative.batterySaverActive         – getter: aktueller Energiesparmodus-Status
 *   ELNative.isTracking                 – getter: GPS-Tracking läuft gerade
 *   ELNative.isNative                   – getter, jedes Mal frisch gegen window.Capacitor geprüft
 *   ELNative.accessInvalid              – getter: Fehlercode, wenn der Gerätezugang endgültig ungültig ist
 *   ELNative.refresh()                  – Pull-to-Refresh der App: Resync der Seite (ec:resync) statt Reload
 *   ELNative.ptrBlocked(target)         – true, wenn Pull-to-Refresh gerade nicht erlaubt ist
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

  // ─── Ungültiger Gerätezugang (SEC-02) ───────────────────────────────────────
  // Der Server antwortet mit 401 und Header X-ECP-Auth-Error, wenn der Gerätezugang
  // endgültig ungültig ist (widerrufen, unbekannt/ersetzt, Benutzer gesperrt). Dann
  // stoppen alle automatischen Anfragen; Netzwerkfehler und 5xx bleiben temporär.
  const _ACCESS_INVALID_CODES = ['device_revoked', 'device_unknown', 'user_inactive'];
  let _accessInvalid = null;

  function _authErrorCode(resp) {
    const code = resp && resp.headers ? resp.headers.get('X-ECP-Auth-Error') : null;
    return resp && resp.status === 401 && _ACCESS_INVALID_CODES.indexOf(code) !== -1 ? code : null;
  }

  function _handleAccessInvalid(code) {
    if (_accessInvalid) return;
    _accessInvalid = code;
    if (_dutyPollId) { clearInterval(_dutyPollId); _dutyPollId = null; }
    try { stopLocation(); } catch (_) {}
    window.dispatchEvent(new CustomEvent('ec:device-access-invalid', { detail: { code } }));
    _showAccessInvalidBanner();
  }

  function _showAccessInvalidBanner() {
    if (document.getElementById('ec-device-access-invalid')) return;
    const box = document.createElement('div');
    box.id = 'ec-device-access-invalid';
    box.setAttribute('role', 'alertdialog');
    box.setAttribute('aria-live', 'assertive');
    box.style.cssText = 'position:fixed;inset:0;z-index:100000;display:flex;align-items:center;'
      + 'justify-content:center;padding:24px;background:rgba(0,0,0,.72);';
    const card = document.createElement('div');
    card.style.cssText = 'max-width:420px;width:100%;background:var(--surface,#fff);color:var(--text,#111);'
      + 'border-radius:12px;padding:24px;text-align:center;';
    const p = document.createElement('p');
    p.style.cssText = 'margin:0 0 16px;font-size:1.05rem;line-height:1.4;';
    p.textContent = 'Der Gerätezugang ist abgelaufen oder wurde deaktiviert. Bitte das Gerät erneut verbinden.';
    const btn = document.createElement('a');
    btn.href = '/geraet-login-pin';
    btn.className = 'btn btn--primary';
    btn.style.cssText = 'display:inline-block;min-height:44px;padding:10px 20px;';
    btn.textContent = 'Erneut verbinden';
    card.appendChild(p);
    card.appendChild(btn);
    box.appendChild(card);
    document.body.appendChild(box);
  }

  // Geräte-Merkmale für Diagnose und Admin-Übersicht (nur was tatsächlich bekannt ist)
  function _deviceHeaders() {
    const h = { 'X-Requested-With': 'XMLHttpRequest' };
    try {
      if (window.Capacitor && typeof window.Capacitor.getPlatform === 'function') {
        h['X-ECP-Platform'] = window.Capacitor.getPlatform();
      }
    } catch (_) {}
    return h;
  }

  const { refresh, ptrBlocked } = window.ECPullToRefresh;

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
  // Wird nicht nur beim App-Start, sondern auch bei jedem Resume/Online-Event
  // erneut versucht (siehe _init unten) - falls die Benachrichtigungs-Berechtigung
  // beim ersten Versuch noch nicht erteilt war (z.B. verweigert, dann nachtraeglich
  // in den Android-Einstellungen erlaubt), gab es bisher KEINEN Retry und das Geraet
  // blieb dauerhaft "Inaktiv", bis die App komplett neu gestartet wurde.
  let _fcmRegistered = false;
  let _fcmRegisterPromise = null;

  function _registerFcmToken() {
    if (!_isNative()) return Promise.resolve({ ok: false, error: 'Native App nicht erkannt' });
    if (_fcmRegistered) return Promise.resolve({ ok: true });
    if (_fcmRegisterPromise) return _fcmRegisterPromise;

    let registerPromise;
    registerPromise = (async () => {
      try {
        const { PushNotifications } = window.Capacitor.Plugins;
        if (!PushNotifications) {
          return { ok: false, error: 'PushNotifications-Plugin nicht verfügbar' };
        }

        const perm = await PushNotifications.requestPermissions();
        if (perm.receive !== 'granted') {
          return { ok: false, error: 'Benachrichtigungs-Berechtigung wurde nicht erteilt' };
        }

        const registrationResult = new Promise((resolve) => {
          PushNotifications.addListener('registration', async (reg) => {
            try {
              if (_accessInvalid) { resolve({ ok: false, error: 'Gerätezugang ungültig' }); return; }
              const res = await fetch('/api/v1/device/fcm-token', {
                method: 'POST',
                headers: Object.assign({ 'Content-Type': 'application/json' }, _deviceHeaders()),
                body: JSON.stringify({ token: reg.value, platform: 'android' }),
              });
              const invalid = _authErrorCode(res);
              if (invalid) {
                _handleAccessInvalid(invalid);
                resolve({ ok: false, error: 'Gerätezugang ungültig' });
                return;
              }
              if (res.ok) {
                _fcmRegistered = true;
                resolve({ ok: true });
              } else {
                console.warn('[ELNative] FCM-Token-Registrierung: HTTP', res.status);
                resolve({ ok: false, error: 'FCM-Token konnte nicht gespeichert werden (HTTP ' + res.status + ')' });
              }
            } catch (e) {
              console.warn('[ELNative] FCM-Token-Registrierung fehlgeschlagen:', e);
              resolve({ ok: false, error: 'FCM-Token-Registrierung fehlgeschlagen: ' + e.message });
            }
          });
          PushNotifications.addListener('registrationError', (err) => {
            console.warn('[ELNative] PushNotifications registrationError:', err);
            resolve({ ok: false, error: 'Native Push-Registrierung fehlgeschlagen' });
          });
        });
        PushNotifications.addListener('pushNotificationActionPerformed', (action) => {
          const url = action?.notification?.data?.url;
          if (url) window.location.href = url;
        });

        await PushNotifications.register();
        // Timeout: ohne diesen bleibt registrationResult ewig offen, wenn weder
        // 'registration' noch 'registrationError' feuern (z.B. Capacitor-Bridge
        // auf dieser Remote-Seite unzuverlaessig, siehe main.py:413-421) - das
        // wuerde _fcmRegisterPromise dauerhaft belegt lassen und damit auch
        // JEDEN kuenftigen automatischen Retry (online/visibilitychange) blockieren.
        return await Promise.race([
          registrationResult,
          new Promise((resolve) => setTimeout(
            () => resolve({ ok: false, error: 'Zeitüberschreitung bei der Registrierung' }), 8000)),
        ]);
      } catch (e) {
        console.warn('[ELNative] PushNotifications Fehler:', e);
        return { ok: false, error: 'PushNotifications-Fehler: ' + e.message };
      } finally {
        if (_fcmRegisterPromise === registerPromise) _fcmRegisterPromise = null;
      }
    })();
    _fcmRegisterPromise = registerPromise;
    return _fcmRegisterPromise;
  }

  async function registerPush() {
    _fcmRegistered = false;
    _fcmRegisterPromise = null;
    return _registerFcmToken();
  }

  // ─── Dateien und URLs öffnen ──────────────────────────────────────────────
  function _fileUrl(url) {
    const parsed = new URL(url, window.location.href);
    if (parsed.origin !== window.location.origin || !/^https?:$/.test(parsed.protocol)) {
      throw new Error('Dateien können nur vom eigenen Server geöffnet werden.');
    }
    // NativeFiles übernimmt die WebView-Cookies; alte Handoff-Tokens nie weitergeben.
    parsed.searchParams.delete('nt');
    return parsed;
  }

  function _isDocumentUrl(parsed) {
    return parsed.origin === window.location.origin &&
      /(?:\.pdf$|\/pdf(?:\/|$)|\/original$|\/sammel-pdf$)/i.test(parsed.pathname);
  }

  function _fileNotice(message) {
    const appEl = document.querySelector('[x-data="appState()"]');
    if (appEl && window.Alpine) {
      window.Alpine.$data(appEl).addToast(message, 'warn');
    } else {
      window.alert(message);
    }
  }

  // Nur diese Serverrouten garantieren mit download=1 Content-Disposition: attachment.
  function _attachmentDownload(parsed) {
    return /^\/objekt-medien\/(?:seite\/\d+\/pdf|dokument\/\d+\/original)$/.test(parsed.pathname) ||
      /^\/objekte\/\d+\/dokumente\/sammel-pdf$/.test(parsed.pathname) ||
      parsed.pathname === '/druck/viewer/pdf' ||
      /^\/medien\/(?:(?:meldung|person)\/)?datei\/\d+$/.test(parsed.pathname);
  }

  async function _openBrowser(parsed) {
    const Browser = window.Capacitor?.Plugins?.Browser;
    if (!Browser) return false;
    let target = parsed.href;
    // Custom Tabs teilen die WebView-Cookies nicht: nur hier ein Handoff anfordern.
    if (parsed.origin === window.location.origin && /^https?:$/.test(parsed.protocol)) {
      try {
        const r = await fetch('/api/v1/device/native-link', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
          body: JSON.stringify({ path: parsed.pathname + parsed.search }),
        });
        if (r.ok) { const d = await r.json(); target = d.url + parsed.hash; }
      } catch (e) { console.warn('[ELNative] native-link Fehler:', e); }
    }
    await Browser.open({ url: target });
    return true;
  }

  async function _fileAction(url, filename, download) {
    try {
      const parsed = _fileUrl(url);
      if (!_isNative()) {
        if (download) {
          const a = document.createElement('a');
          a.href = parsed.href; a.download = filename || '';
          document.body.appendChild(a); a.click(); a.remove();
        } else { window.open(parsed.href, '_blank', 'noopener'); }
        return;
      }
      const plugin = window.Capacitor?.Plugins?.NativeFiles;
      if (plugin) {
        if (download) {
          return await plugin.download({ url: parsed.href, ...(filename ? { filename } : {}) });
        }
        try { return await plugin.openFile({ url: parsed.href }); }
        catch (e) {
          if (/keine app/i.test(e?.message || String(e))) {
            return await plugin.download({ url: parsed.href });
          }
          throw e;
        }
      }
      if (download) parsed.searchParams.set('download', '1');
      if (await _openBrowser(parsed)) return;
      _fileNotice('Bitte die App aktualisieren, um Dateien herunterzuladen.');
      if (download && _attachmentDownload(parsed)) window.location.assign(parsed.href);
    } catch (e) {
      console.warn('[ELNative] Dateiaktion fehlgeschlagen:', e);
      _fileNotice('Die Datei konnte nicht geöffnet oder heruntergeladen werden. ' + (e?.message || 'Bitte erneut versuchen.'));
    }
  }

  function downloadFile(url, filename) { return _fileAction(url, filename, true); }
  function openFile(url) { return _fileAction(url, undefined, false); }

  async function openUrl(url) {
    if (!_isNative()) { window.open(url, '_blank', 'noopener'); return; }
    try {
      const parsed = new URL(url, window.location.href);
      if (parsed.origin === window.location.origin && parsed.searchParams.get('download') === '1') {
        return await downloadFile(url);
      }
      if (_isDocumentUrl(parsed)) return await openFile(url);
      if (await _openBrowser(parsed)) return;
      _fileNotice('Bitte die App aktualisieren, um Links zu öffnen.');
    } catch (e) {
      console.warn('[ELNative] openUrl Fehler:', e);
      _fileNotice('Der Link konnte nicht geöffnet werden. Bitte erneut versuchen.');
    }
  }

  // Capture vor dem Viewer: dessen defaultPrevented-Prüfung verhindert Doppelaktionen.
  document.addEventListener('click', async function (ev) {
    if (!_isNative() || ev.defaultPrevented) return;
    const a = ev.target.closest && ev.target.closest('a[target="_blank"], a[download], a[data-dv-download]');
    if (!a || !a.href) return;
    ev.preventDefault();
    const parsed = new URL(a.href, window.location.href);
    // Fremde Links bleiben Browser-Links und erhalten niemals WebView-Cookies.
    if (parsed.origin !== window.location.origin) { await openUrl(a.href); return; }
    if (a.hasAttribute('download') || a.hasAttribute('data-dv-download') || parsed.searchParams.get('download') === '1') {
      await downloadFile(a.href, a.getAttribute('download') || undefined);
    } else if (a.hasAttribute('data-dv-original') || a.hasAttribute('data-dv-excerpt') || _isDocumentUrl(parsed)) {
      await openFile(a.href);
    } else { await openUrl(a.href); }
  }, true);

  // Die native Kontaktansicht verwendet die synchronisierte Room-Datenbank.
  // Im Flugmodus darf der Menüpunkt deshalb nicht in die Remote-WebView
  // navigieren: dort kann Chromium keine Seite laden, obwohl die Kontakte
  // lokal vorhanden sind. Online bleibt die vollständige Webverwaltung die
  // gewohnte Zielseite.
  document.addEventListener('click', async function (ev) {
    if (!_isNative() || navigator.onLine !== false) return;
    const a = ev.target.closest && ev.target.closest('a[href]');
    if (!a) return;
    let path;
    try { path = new URL(a.href, window.location.href).pathname; } catch (_) { return; }
    if (path !== '/kontakte') return;
    const plugin = window.Capacitor?.Plugins?.DeviceKeepalive;
    if (!plugin || typeof plugin.openOfflineKontakte !== 'function') return;
    ev.preventDefault();
    try { await plugin.openOfflineKontakte(); } catch (_) { window.location.href = a.href; }
  }, true);

  // Beim expliziten Abmelden darf weder der alte Geräte-Token noch der
  // Live-Foreground-Service beim nächsten App-Start automatisch weiterlaufen.
  document.addEventListener('click', async function (ev) {
    if (!_isNative()) return;
    const a = ev.target.closest && ev.target.closest('a[href]');
    if (!a) return;
    let path;
    try { path = new URL(a.href, window.location.href).pathname; } catch (_) { return; }
    if (path !== '/logout') return;
    ev.preventDefault();
    try {
      const { DeviceKeepalive, Preferences } = window.Capacitor.Plugins;
      if (DeviceKeepalive) await DeviceKeepalive.stopKeepalive().catch(() => {});
      if (Preferences) {
        await Promise.all([
          Preferences.remove({ key: 'el_device_token' }),
          Preferences.set({ key: 'el_live_enabled', value: '0' }),
          Preferences.remove({ key: 'el_used_account_login' }),
        ]).catch(() => {});
      }
    } catch (_) {}
    window.location.href = a.href;
  }, true);

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
  const _LOCATION_PERMISSION_PRIMED_KEY = 'el_location_permission_primed';

  function _locationWatcherOptions() {
    return {
      backgroundMessage: 'Standort wird im Einsatz übermittelt.',
      backgroundTitle: 'Einsatzcockpit',
      requestPermissions: true,
      stale: false,
      distanceFilter: _effectiveDistanceFilter(),
    };
  }

  function _showLocationPermissionToast(message) {
    const appEl = document.querySelector('[x-data="appState()"]');
    if (appEl && window.Alpine) Alpine.$data(appEl).addToast(message, 'warn');
  }

  function _handleLocationPermissionError(err, activeIncident) {
    if (!err || err.code !== 'NOT_AUTHORIZED') return;
    _showLocationPermissionToast(activeIncident
      ? 'Einsatz aktiv, aber der Standort kann ohne Standort-Berechtigung nicht übermittelt werden.'
      : 'Standort-Berechtigung fehlt. Standort-Tracking im Einsatz funktioniert ohne sie nicht.');
  }

  function _primeLocationPermission() {
    if (localStorage.getItem(_LOCATION_PERMISSION_PRIMED_KEY) || _isTracking) return;

    try {
      const { BackgroundGeolocation } = window.Capacitor.Plugins;
      if (!BackgroundGeolocation) {
        localStorage.setItem(_LOCATION_PERMISSION_PRIMED_KEY, '1');
        return;
      }

      let watcherId = null;
      let firstCallbackReceived = false;
      let watcherRemoved = false;

      function removeWatcherAndMarkPrimed() {
        if (watcherRemoved || watcherId === null) return;
        watcherRemoved = true;
        try {
          Promise.resolve(BackgroundGeolocation.removeWatcher({ id: watcherId }))
            .catch(() => {})
            .finally(() => localStorage.setItem(_LOCATION_PERMISSION_PRIMED_KEY, '1'));
        } catch (_) {
          localStorage.setItem(_LOCATION_PERMISSION_PRIMED_KEY, '1');
        }
      }

      BackgroundGeolocation.addWatcher(
        _locationWatcherOptions(),
        function callback(_loc, err) {
          if (firstCallbackReceived) return;
          firstCallbackReceived = true;
          _handleLocationPermissionError(err, false);
          removeWatcherAndMarkPrimed();
        },
      ).then((id) => {
        watcherId = id;
        if (firstCallbackReceived) removeWatcherAndMarkPrimed();
      }).catch((err) => {
        if (firstCallbackReceived) return;
        firstCallbackReceived = true;
        _handleLocationPermissionError(err, false);
        localStorage.setItem(_LOCATION_PERMISSION_PRIMED_KEY, '1');
      });
    } catch (err) {
      _handleLocationPermissionError(err, false);
      localStorage.setItem(_LOCATION_PERMISSION_PRIMED_KEY, '1');
    }
  }

  // Haversine-Distanz in Metern zwischen zwei GPS-Punkten
  function _gpsDistance(lat1, lng1, lat2, lng2) {
    const R = 6371000;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLng = (lng2 - lng1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) ** 2
      + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLng / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  // Nicht zugestellte Position: bleibt erhalten und wird beim naechsten Tick bzw. bei
  // wiederkehrendem Netz erneut gesendet (nur die neueste, Messzeit bleibt erhalten).
  let _pendingLocation = null;

  function _postLocation(p) {
    return fetch('/api/v1/device/location', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, _deviceHeaders()),
      body: JSON.stringify({
        lat: p.lat, lng: p.lng, accuracy: p.accuracy, source: 'live',
        measured_at: new Date(p.time).toISOString(),
      }),
    }).then((resp) => {
      const invalid = _authErrorCode(resp);
      if (invalid) { _handleAccessInvalid(invalid); _pendingLocation = null; return; }
      // 5xx/429: spaeter erneut; 4xx (ausser 429) ist endgueltig, kein Dauer-Retry
      if (resp.ok || (resp.status < 500 && resp.status !== 429)) {
        if (_pendingLocation === p) _pendingLocation = null;
        if (resp.ok) { _lastSentLat = p.lat; _lastSentLng = p.lng; }
      }
    }).catch(() => {});
  }

  function _retryPendingLocation() {
    if (_pendingLocation && !_accessInvalid) _postLocation(_pendingLocation);
  }

  function _sendLocation(lat, lng, accuracy, time) {
    // Nur senden wenn >= 10 m Abstand zur letzten erfolgreichen Uebermittlung
    if (_lastSentLat !== null && !_pendingLocation
        && _gpsDistance(lat, lng, _lastSentLat, _lastSentLng) < 10) return;
    if (_accessInvalid) return;
    // Messzeit des GPS-Fixes; ohne Angabe oder aus der Zukunft gilt "jetzt"
    const now = Date.now();
    const t = (typeof time === 'number' && time > 0 && time <= now + 60000) ? time : now;
    _pendingLocation = { lat, lng, accuracy, time: t };
    _postLocation(_pendingLocation);
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
      if (!BackgroundGeolocation) { _isTracking = false; keepAwake(false); return; }
      BackgroundGeolocation.addWatcher(
        _locationWatcherOptions(),
        function callback(loc, err) {
          if (err) return;
          _sendLocation(loc.latitude, loc.longitude, loc.accuracy, loc.time);
        },
      ).then((id) => { _locationWatch = id; }).catch((err) => {
        _isTracking = false;
        keepAwake(false);
        _handleLocationPermissionError(err, true);
      });

      // Periodischer Fallback: aktuelle Position holen und senden wenn verändert
      if (!_periodicGpsInterval) {
        _periodicGpsInterval = setInterval(() => {
          if (!_locationWatch) return;
          navigator.geolocation.getCurrentPosition(
            (pos) => _sendLocation(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy, pos.timestamp),
            () => {},
            { timeout: 10000, maximumAge: 30000 },
          );
          _retryPendingLocation();
        }, _effectiveGpsInterval());
      }
    } catch (e) {
      _isTracking = false;
      keepAwake(false);
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
    _pendingLocation = null;

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
    if (!_isNative() || _accessInvalid) return;
    try {
      const resp = await fetch('/api/v1/device/duty-state', { headers: _deviceHeaders() });
      const invalid = _authErrorCode(resp);
      if (invalid) { _handleAccessInvalid(invalid); return; }
      if (!resp.ok) return; // temporärer Fehler (Netz, 5xx): vorherigen Zustand behalten
      const data = await resp.json();
      if (data.should_track) startLocation();
      else stopLocation();
    } catch (_) {} // Netzwerkfehler: kein Stopp des aktiven Trackings
  }

  let _dutyPollId = null;
  function _startDutyPoll() {
    if (_accessInvalid) return;
    if (_dutyPollId) clearInterval(_dutyPollId);
    _dutyPollId = setInterval(() => {
      if (document.visibilityState === 'visible') _pollDutyState();
    }, _batterySaver ? _DUTY_BATTERY_INTERVAL : _DUTY_NORMAL_INTERVAL);
  }

  // ─── Initialisierung ─────────────────────────────────────────────────────────
  // Hinweis: _isNative() ist auf dieser Seite mit Vorsicht zu genießen — Capacitor
  // injiziert seine JS-Bruecke nicht zuverlaessig in Seiten, die per
  // server.allowNavigation extern (Cross-Origin) nachgeladen werden (bekannte
  // Einschraenkung, siehe ionic-team/capacitor#7454). Rein UI-steuernde
  // Entscheidungen (z. B. native-only Menüpunkte) laufen daher serverseitig
  // über request.state.is_native_app (ec_native-Cookie, siehe main.py) statt
  // über einen Client-Check hier. FCM/Battery/Duty-Poll bleiben best-effort:
  // schlagen sie fehl, heilt der 60s-Poll das von selbst, sobald die Bruecke
  // (falls je) verfuegbar wird.
  async function _init() {
    if (_isNative()) {
      _registerFcmToken();
      _initBattery();
      await _pollDutyState();
      _primeLocationPermission();
    }

    // Duty-Status-Poll starten (No-Op wenn nicht nativ)
    _startDutyPoll();

    // Sofort neu pollen wenn Netzwerk wiederkehrt (z. B. nach Tunnelausfahrt)
    window.addEventListener('online', () => {
      if (_isNative()) { _pollDutyState(); _registerFcmToken(); _retryPendingLocation(); }
    });

    // Sofort neu pollen wenn App aus dem Hintergrund kommt - deckt auch den Fall
    // ab, dass die Benachrichtigungs-Berechtigung waehrend die App im Hintergrund
    // war nachtraeglich in den Android-Einstellungen erteilt wurde.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && _isNative()) {
        _pollDutyState();
        _registerFcmToken();
      }
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
    openUrl,
    downloadFile,
    openFile,
    registerPush,
    setBatterySaver(on) { _setBatterySaver(on); },
    get batterySaverActive() { return _batterySaver; },
    get isTracking() { return _isTracking; },
    get accessInvalid() { return _accessInvalid; },
    refresh,
    ptrBlocked,
  };
})();
