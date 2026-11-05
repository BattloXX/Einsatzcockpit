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
