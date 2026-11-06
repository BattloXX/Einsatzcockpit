import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/js/asuew_sitzung.js', import.meta.url), 'utf8');
function env(responses, extra = {}) {
  const module = { exports: {} }, window = {};
  vm.runInNewContext(source, { module, window, Date, setTimeout, clearTimeout });
  assert.equal(window.ECPAsuewSitzung, module.exports);
  let time = Date.parse('2026-11-05T12:00:00Z');
  const calls = [], timers = [], states = [];
  const controller = module.exports.start({
    incidentId: 42, native: { getFcmToken: async () => 'fcm-token' },
    navigator: { onLine: true, getBattery: async () => ({ level: 0.756, charging: true }) },
    notification: { permission: 'granted' }, now: () => time,
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; },
    clearTimeout: () => {}, onState: state => states.push(state),
    fetchImpl: async (url, options) => {
      calls.push({ url, ...options, data: JSON.parse(options.body) });
      const next = responses.shift();
      if (next instanceof Error) throw next;
      assert.ok(next, 'unexpected request');
      return { ok: !next.status, status: next.status || 200, json: async () => next };
    }, ...extra
  });
  return { controller, calls, timers, states, api: module.exports,
    async next() { const timer = timers.shift(); assert.ok(timer); time += timer.delay; await timer.fn(); } };
}
const started = () => ({ session_key: 'session-1', heartbeat_interval_s: 7,
  server_time: '2026-11-05T12:00:12Z', ampel: 'gelb', probleme: ['Akku'] });

test('start payload, server heartbeat interval, clock offset and state', async () => {
  const e = env([started(), { ampel: 'gruen' }]);
  await e.controller.ready;
  assert.equal(e.calls[0].url, '/api/asuew/sitzung');
  assert.equal(e.calls[0].credentials, 'same-origin');
  assert.equal(e.calls[0].method, 'POST');
  assert.equal(e.calls[0].data.incident_id, 42);
  assert.equal(e.calls[0].data.fcm_token, 'fcm-token');
  assert.equal(e.calls[0].data.akku_prozent, 76);
  assert.equal(e.calls[0].data.laedt, true);
  assert.deepEqual(e.calls[0].data.selbsttest, { online: true, benachrichtigung: 'granted' });
  assert.equal(e.api.getState().zustand, 'aktiv');
  assert.equal(e.api.getState().ampel, 'gelb');
  assert.equal(e.timers[0].delay, 7000);
  await e.next();
  assert.equal(e.calls[1].url, '/api/asuew/heartbeat');
  assert.equal(e.calls[1].headers['X-ECP-ASUEW'], 'session-1');
  assert.equal(e.calls[1].data.selbsttest.uhr_abweichung_s, 12);
  assert.equal(e.calls[1].data.fcm_token, undefined);
  assert.equal(e.api.getState().ampel, 'gruen');
});

test('409 restarts immediately, subsequent restarts wait a minute', async () => {
  const ended = { status: 409, error: 'sitzung_beendet' };
  const e = env([started(), ended, started(), ended, started()]);
  await e.controller.ready;
  await e.next();
  assert.equal(e.timers[0].delay, 0);
  await e.next();
  assert.equal(e.calls[2].url, '/api/asuew/sitzung');
  await e.next();
  assert.equal(e.timers[0].delay, 53000);
  await e.next();
  assert.equal(e.calls[4].url, '/api/asuew/sitzung');
});

for (const [status, error] of [[403, 'app_erforderlich'], [426, 'app_veraltet']]) {
  test(`${status} is terminal with server message`, async () => {
    const e = env([{ status, error, message: 'Bitte aktualisieren' }]);
    await e.controller.ready;
    assert.equal(e.api.getState().zustand, 'fehler');
    assert.equal(e.api.getState().message, 'Bitte aktualisieren');
    assert.equal(e.timers.length, 0);
  });
}

test('three consecutive network failures degrade, success recovers and resets count', async () => {
  const e = env([started(), new Error(), new Error(), new Error(), { ampel: 'rot' }, new Error()]);
  await e.controller.ready;
  await e.next(); await e.next(); await e.next();
  assert.equal(e.api.getState().zustand, 'gestoert');
  await e.next();
  assert.equal(e.api.getState().zustand, 'aktiv');
  await e.next();
  assert.equal(e.api.getState().zustand, 'aktiv');
});

test('optional browser APIs and app version', async () => {
  const e = env([started()], { navigator: { onLine: false }, notification: { permission: 'denied' },
    native: { getFcmToken: async () => 't', getAppVersion: async () => '2026.11.05' } });
  await e.controller.ready;
  assert.equal(e.calls[0].data.app_version, '2026.11.05');
  assert.equal(e.calls[0].data.akku_prozent, undefined);
  assert.equal(e.calls[0].data.selbsttest.online, false);
});

test('initial network errors retry start and recover after degraded state', async () => {
  const e = env([new Error(), new Error(), new Error(), started()]);
  await e.controller.ready;
  await e.next(); await e.next();
  assert.equal(e.api.getState().zustand, 'gestoert');
  await e.next();
  assert.equal(e.api.getState().zustand, 'aktiv');
  assert.ok(e.calls.every(call => call.url === '/api/asuew/sitzung'));
});

for (const [status, error] of [[403, 'app_erforderlich'], [426, 'app_veraltet']]) {
  test(`heartbeat ${status} stops retries`, async () => {
    const e = env([started(), { status, error, message: 'App erforderlich' }]);
    await e.controller.ready;
    await e.next();
    assert.equal(e.api.getState().zustand, 'fehler');
    assert.equal(e.timers.length, 0);
  });
}

for (const getAppVersion of [async () => null, async () => { throw new Error('Plugin fehlt'); }]) {
  test('app version failure is omitted and start still succeeds', async () => {
    const e = env([started()], { native: { getFcmToken: async () => 't', getAppVersion } });
    await e.controller.ready;
    assert.equal(e.calls.length, 1);
    assert.equal(Object.hasOwn(e.calls[0].data, 'app_version'), false);
    assert.equal(e.api.getState().zustand, 'aktiv');
  });
}

test('native selftest overrides web fields while web clock offset stays authoritative', async () => {
  const nativeTest = { online: false, benachrichtigung: 'denied', alarmton_getestet: true,
    exakte_wecker: false, vollbild: false, akku_optimierung_ausgenommen: true,
    alarm_lautstaerke_prozent: 29, uhr_abweichung_s: 999, unknown: 'ignored' };
  const e = env([started(), { ampel: 'rot' }], { native: {
    getFcmToken: async () => 'token', asuew: { getSelbsttest: async () => nativeTest }
  } });
  await e.controller.ready;
  await e.next();
  const { uhr_abweichung_s, unknown, ...expected } = nativeTest;
  assert.deepEqual(e.calls[0].data.selbsttest, expected);
  assert.deepEqual(e.calls[1].data.selbsttest, { ...expected, uhr_abweichung_s: 12 });
});

test('native watcher starts once after successful session, including session restart', async () => {
  const calls = [];
  const e = env([new Error(), started(), { ampel: 'gruen' },
    { status: 409, error: 'sitzung_beendet' }, started()], { native: {
    getFcmToken: async () => 'token', asuew: {
      start: async id => calls.push(['start', id]), keepScreenOn: async on => calls.push(['screen', on])
    }
  } });
  await e.controller.ready;
  assert.deepEqual(calls, []);
  await e.next(); await e.next(); await e.next(); await e.next();
  assert.deepEqual(calls, [['start', 42], ['screen', true]]);
});

test('native plugin failures do not interrupt start or heartbeats', async () => {
  let starts = 0, screens = 0;
  const e = env([started(), { ampel: 'gruen' }], { native: {
    getFcmToken: async () => 'token', asuew: {
      getSelbsttest: async () => { throw Error('Selftest'); },
      start: async () => { starts++; throw Error('Start'); },
      keepScreenOn: async () => { screens++; throw Error('Screen'); }
    }
  } });
  await e.controller.ready;
  await e.next();
  assert.equal(e.calls.length, 2);
  assert.equal(e.controller.getState().zustand, 'aktiv');
  assert.equal(e.calls[1].data.selbsttest.online, true);
  assert.equal(starts, 1);
  assert.equal(screens, 1);
});

test('immediate refresh coalesces in-flight requests and replaces scheduled heartbeat', async () => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  let requests = 0;
  const cancelled = [];
  const e = env([], { clearTimeout: id => cancelled.push(id), fetchImpl: async () => {
    requests++;
    await waiting;
    return { ok: true, json: async () => started() };
  } });
  const refresh = e.controller.refresh();
  release();
  await e.controller.ready; await refresh;
  assert.equal(requests, 1);
  assert.equal(e.timers.length, 1);
  await e.controller.refresh();
  assert.equal(requests, 2);
  assert.equal(cancelled.at(-1), 1);
});

const boardSource = readFileSync(new URL('../../app/templates/breathing/board.html', import.meta.url), 'utf8')
  .split('const ASUEW_READONLY = ')[1].split('{% if (can_edit')[0]
  .replace('{{ asuew_readonly | default(false) | tojson }}', 'true')
  .replaceAll('{{ incident.id }}', '42');
function boardEnv({ isNative = true, available = true } = {}) {
  const elements = new Map(), listeners = {}, intervals = [], calls = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      hidden: true, style: {}, dataset: {}, children: [],
      addEventListener(type, fn) { this[type] = fn; },
      replaceChildren() { this.children = []; },
      appendChild(item) { this.children.push(item); }
    });
    return elements.get(id);
  }
  const buttons = ['batterie', 'wecker', 'vollbild', 'benachrichtigung'].map(type => {
    const button = element(type);
    button.dataset.asuewSetting = type;
    return button;
  });
  const document = {
    hidden: false, getElementById: element, createElement: () => ({}),
    querySelector: selector => element(selector.match(/="(.*?)"/)[1]),
    querySelectorAll: () => buttons,
    addEventListener: (name, fn) => { listeners[name] = fn; }, removeEventListener() {}
  };
  const asuew = {
    isAvailable: () => available,
    getState: async () => { calls.push('getState'); return { connection: 'online', battery: 'ok' }; },
    getSelbsttest: async () => ({ exakte_wecker: false }),
    openSettings: async type => calls.push(type),
    playTestAlarm: async () => { calls.push('play'); return true; },
    confirmAlarmTest: async () => { calls.push('confirm'); return true; }
  };
  let opts;
  const window = { ELNative: { isNative, asuew },
    setInterval(fn, delay) { intervals.push({ fn, delay }); return 1; }, clearInterval() {}, addEventListener() {} };
  vm.runInNewContext('const ASUEW_READONLY = ' + boardSource, {
    window, ELNative: window.ELNative, document,
    ECPAsuewSitzung: { start(options) { opts = options; return { refresh: async () => calls.push('refresh') }; } }
  });
  return { element, document, intervals, calls, listeners, state: data => opts.onState(data) };
}

test('board device check stays hidden outside app or without native plugin', () => {
  for (const options of [{ isNative: false }, { available: false }]) {
    const e = boardEnv(options);
    assert.equal(e.element('asuewDeviceCheck').hidden, true);
    assert.equal(e.intervals.length, 0);
  }
});

test('readonly board shows problems, settings, alarm confirmation and visible-tab polling', async () => {
  const e = boardEnv();
  await new Promise(resolve => setImmediate(resolve));
  e.state({ zustand: 'aktiv', ampel: 'rot', probleme: ['Benachrichtigungen sind nicht erlaubt.'] });
  assert.equal(e.element('asuewDeviceCheck').hidden, false);
  assert.equal(e.element('asuewDeviceLight').textContent, 'Selbsttest: rot');
  assert.equal(e.element('asuewDeviceProblems').children[0].textContent, 'Benachrichtigungen sind nicht erlaubt.');
  assert.equal(e.element('wecker').hidden, false);
  assert.equal(e.element('benachrichtigung').hidden, false);
  assert.equal(e.element('batterie').hidden, true);
  assert.match(e.element('asuewNativeStatus').textContent, /online.*ok/);
  await e.element('wecker').click();
  await e.element('asuewTestAlarm').click();
  assert.equal(e.element('asuewAlarmConfirmation').hidden, false);
  await e.element('asuewConfirmAlarm').click();
  assert.equal(e.element('asuewAlarmConfirmation').hidden, true);
  assert.deepEqual(e.calls.slice(-4), ['wecker', 'play', 'confirm', 'refresh']);
  assert.equal(e.intervals[0].delay, 10000);
  const before = e.calls.length;
  e.document.hidden = true;
  await e.intervals[0].fn();
  assert.equal(e.calls.length, before);
  e.document.hidden = false;
  await e.listeners.visibilitychange();
  assert.equal(e.calls.at(-1), 'getState');
});


test('getOffsetMs starts unknown and tracks latest successful server time in milliseconds', async () => {
  const e = env([started(), { server_time: '2026-11-05T12:00:05Z' }, new Error()]);
  assert.equal(e.api.getOffsetMs(), 0);
  await e.controller.ready;
  assert.equal(e.api.getOffsetMs(), 12000);
  assert.equal(e.controller.getOffsetMs(), 12000);
  await e.next();
  assert.equal(e.api.getOffsetMs(), -2000);
  await e.next();
  assert.equal(e.api.getOffsetMs(), -2000);
});
