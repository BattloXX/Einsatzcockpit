/* App-Sitzung und Heartbeat fuer die Atemschutzueberwachung. */
(function () {
  'use strict';
  let current = null;

  function start(opts) {
    const root = typeof window !== 'undefined' ? window : {};
    const native = opts.native || root.ELNative;
    const nav = opts.navigator || navigator;
    const notification = opts.notification || (typeof Notification !== 'undefined' ? Notification : null);
    const fetchImpl = opts.fetchImpl || fetch;
    const now = opts.now || Date.now;
    const schedule = opts.setTimeout || setTimeout;
    const cancel = opts.clearTimeout || clearTimeout;
    let state = { zustand: 'verbindet' };
    let watcherStarted = false, pending = null;
    let timer, key = null, interval = 15000, failures = 0;
    let lastRestart = -Infinity, restartNeeded = false, offset = null, stopped = false;

    function publish(zustand, data = {}) {
      state = { zustand, ampel: data.ampel, probleme: data.probleme || [], message: data.message || '' };
      if (opts.onState) opts.onState(state);
    }

    async function payload() {
      let nativeTest = null;
      try { nativeTest = native.asuew && await native.asuew.getSelbsttest(); } catch (_) {}
      const selbsttest = { online: nav.onLine };
      if (notification) selbsttest.benachrichtigung = notification.permission;
      if (offset !== null) selbsttest.uhr_abweichung_s = offset;
      for (const field of ['benachrichtigung', 'online', 'alarmton_getestet', 'exakte_wecker',
        'vollbild', 'akku_optimierung_ausgenommen', 'alarm_lautstaerke_prozent']) {
        if (nativeTest && Object.prototype.hasOwnProperty.call(nativeTest, field)) selbsttest[field] = nativeTest[field];
      }
      const data = { selbsttest };
      if (nav.getBattery) {
        try {
          const battery = await nav.getBattery();
          data.akku_prozent = Math.round(battery.level * 100);
          data.laedt = battery.charging;
        } catch (_) { /* Akkuangaben sind optional. */ }
      }
      return data;
    }

    async function tick() {
      if (pending) return pending;
      pending = runTick();
      try { await pending; } finally { pending = null; }
    }

    async function runTick() {
      if (stopped) return;
      if (restartNeeded) {
        key = null;
        lastRestart = now();
        restartNeeded = false;
      }
      let retry = true, delay = interval;
      try {
        const data = await payload();
        if (!key) {
          data.incident_id = opts.incidentId;
          data.fcm_token = await native.getFcmToken();
          if (!data.fcm_token) throw new Error('FCM-Token fehlt');
          try {
            const version = native.getAppVersion ? await native.getAppVersion() : null;
            if (version) data.app_version = version;
          } catch (_) { /* Die App-Version ist optional. */ }
        }
        const response = await fetchImpl(key ? '/api/asuew/heartbeat' : '/api/asuew/sitzung', {
          method: 'POST', credentials: 'same-origin',
          headers: Object.assign({ 'Content-Type': 'application/json' }, key ? { 'X-ECP-ASUEW': key } : {}),
          body: JSON.stringify(data)
        });
        const result = await response.json();
        if (response.ok) {
          if (result.server_time) offset = (Date.parse(result.server_time) - now()) / 1000;
          if (result.heartbeat_interval_s > 0) interval = result.heartbeat_interval_s * 1000;
          const firstStart = !key;
          if (result.session_key) key = result.session_key;
          failures = 0;
          if (firstStart && !watcherStarted && native.asuew) {
            watcherStarted = true;
            try { await native.asuew.start(opts.incidentId); } catch (_) {}
            try { await native.asuew.keepScreenOn(true); } catch (_) {}
          }
          publish('aktiv', result);
          if (firstStart && opts.onStarted) opts.onStarted(result);
          delay = interval;
        } else if ((response.status === 403 && result.error === 'app_erforderlich') ||
                   (response.status === 426 && result.error === 'app_veraltet')) {
          retry = false;
          publish('fehler', result);
        } else if (response.status === 409 && result.error === 'sitzung_beendet') {
          publish('verbindet', result);
          delay = Math.max(0, 60000 - (now() - lastRestart));
          restartNeeded = true;
        } else {
          retry = false;
          publish('fehler', result);
        }
      } catch (_) {
        failures += 1;
        publish(failures >= 3 ? 'gestoert' : (key ? 'aktiv' : 'verbindet'), {
          message: 'Keine Verbindung zum Server'
        });
      }
      if (retry && !stopped) timer = schedule(tick, delay);
    }

    if (current) current.stop();
    current = {
      getState() { return state; },
      getOffsetMs() { return Number.isFinite(offset) ? offset * 1000 : 0; },
      refresh() { cancel(timer); return tick(); },
      stop() { stopped = true; cancel(timer); }
    };
    publish('verbindet');
    current.ready = tick();
    return current;
  }

  const api = { start, getOffsetMs() { return current ? current.getOffsetMs() : 0; }, getState() { return current ? current.getState() : { zustand: 'verbindet' }; } };
  if (typeof window !== 'undefined') window.ECPAsuewSitzung = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
