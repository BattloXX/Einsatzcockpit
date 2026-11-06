import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { IDBFactory } from 'fake-indexeddb';

const source = readFileSync(new URL('../../app/static/js/breathing_outbox.js', import.meta.url), 'utf8');
function environment(script = [], options = {}, globals = {}) {
  const module = { exports: {} }, window = {};
  vm.runInNewContext(source, { module, window, URLSearchParams, AbortController, setTimeout, clearTimeout, ...globals });
  assert.equal(module.exports, window.ECPBreathingOutbox);
  let time = Date.parse('2026-11-06T12:00:00Z'), sequence = 0;
  const calls = [];
  const config = { indexedDB: new IDBFactory(), incidentId: 42, now: () => time,
    uuid: () => `event-id-${++sequence}`, csrfToken: () => 'csrf', offsetMs: () => 12000,
    fetch: async (path, init) => {
      calls.push({ path, ...init });
      const response = script.shift();
      if (response instanceof Error) throw response;
      if (typeof response === 'function') return response(path, init);
      return response || new Response(null, { status: 204 });
    }, ...options };
  return { api: module.exports, config, calls, outbox: module.exports.createOutbox(config),
    advance(ms) { time += ms; } };
}
const path = '/einsatz/42/atemschutz/7/druck';
const add = (o, fields = {}) => o.erfassen(path, { pressure_bar: '180', ...fields });

test('IndexedDB survives restart, ordering uses server timestamp then capture sequence', async () => {
  const e = environment();
  await add(e.outbox, { erfasst_at: '2026-11-06T12:00:20Z' });
  await add(e.outbox, { erfasst_at: '2026-11-06T12:00:10Z' });
  await add(e.outbox, { erfasst_at: '2026-11-06T12:00:10Z' });
  const second = e.api.createOutbox(e.config);
  assert.equal(await second.anzahl(), 3);
  assert.equal((await second.liste()).map(x => x.id).join(','), 'event-id-2,event-id-3,event-id-1');
  await second.senden();
  assert.equal(e.calls.map(x => new URLSearchParams(x.body).get('client_event_id')).join(','), 'event-id-2,event-id-3,event-id-1');
});

test('POST carries fields, stable identity, server time and current CSRF; 204 and 303 delete', async () => {
  const e = environment([new Response(null, { status: 204 }), new Response(null, { status: 303 })]);
  const item = await add(e.outbox, { korrektur_grund: 'Messfehler' });
  assert.equal(item.erfasst_at, '2026-11-06T12:00:12.000Z');
  await add(e.outbox);
  assert.equal(await e.outbox.senden(), 2);
  assert.equal(await e.outbox.anzahl(), 0);
  const request = e.calls[0], body = new URLSearchParams(request.body);
  assert.equal(request.method, 'POST');
  assert.equal(request.credentials, 'same-origin');
  assert.equal(request.headers['Content-Type'], 'application/x-www-form-urlencoded');
  for (const [key, value] of Object.entries({ client_event_id: item.id, erfasst_at: item.erfasst_at,
    _csrf: 'csrf', pressure_bar: '180', korrektur_grund: 'Messfehler' })) assert.equal(body.get(key), value);
});

test('network loss stops serial delivery, retry keeps client_event_id', async () => {
  const e = environment([new TypeError('offline')]);
  const item = await add(e.outbox);
  await add(e.outbox);
  await e.outbox.senden();
  assert.equal(e.calls.length, 1);
  const waiting = (await e.outbox.liste())[0];
  assert.equal(waiting.status, 'wartend');
  assert.equal(waiting.versuche, 1);
  assert.equal(waiting.id, item.id);
  e.advance(5000);
  await e.outbox.senden();
  assert.equal(new URLSearchParams(e.calls[1].body).get('client_event_id'), item.id);
  assert.equal(await e.outbox.anzahl(), 0);
});

test('5xx retries back off at 5/10/20/40/60 seconds and block later entries', async () => {
  const e = environment(Array.from({ length: 6 }, () => new Response('Serverfehler', { status: 503 })));
  await add(e.outbox); await add(e.outbox);
  for (const delay of [5000, 10000, 20000, 40000, 60000, 60000]) {
    const count = e.calls.length;
    await e.outbox.senden();
    assert.equal(e.calls.length, count + 1);
    await e.outbox.senden();
    assert.equal(e.calls.length, count + 1);
    e.advance(delay - 1);
    await e.outbox.senden();
    assert.equal(e.calls.length, count + 1);
    e.advance(1);
  }
  await e.outbox.senden();
  assert.equal(await e.outbox.anzahl(), 0);
});

for (const status of [400, 403, 404, 409, 422]) {
  test(`${status} rejects with plaintext, limits error to 200 characters and continues`, async () => {
    const e = environment([new Response('  ' + 'Konflikt '.repeat(40), { status })]);
    await add(e.outbox); await add(e.outbox);
    await e.outbox.senden();
    assert.equal(e.calls.length, 2);
    const [rejected] = await e.outbox.liste();
    assert.equal(rejected.status, 'abgelehnt');
    assert.equal(rejected.fehler, 'Konflikt '.repeat(40).trim().slice(0, 200));
    await e.outbox.verwerfen(rejected.id);
    assert.equal(await e.outbox.anzahl(), 0);
  });
}

test('parallel sends share the exact same promise and duplicate capture preserves entry', async () => {
  let release;
  const e = environment([() => new Promise(resolve => { release = resolve; })]);
  await add(e.outbox, { client_event_id: 'original-id', erfasst_at: '2026-11-06T12:00:00Z' });
  await add(e.outbox, { client_event_id: 'original-id', pressure_bar: '170' });
  assert.equal(await e.outbox.anzahl(), 1);
  const first = e.outbox.senden(), second = e.outbox.senden();
  assert.equal(first, second);
  while (!release) await new Promise(resolve => setImmediate(resolve));
  release(new Response(null, { status: 204 }));
  await first;
  assert.equal(e.calls.length, 1);
  assert.equal(new URLSearchParams(e.calls[0].body).get('pressure_bar'), '180');
});

test('15 second timeout aborts uncertain request and preserves identity for retry', async () => {
  let timeout, signal;
  const e = environment([(_path, init) => { signal = init.signal; return new Promise(() => {}); }], { indexedDB: null }, {
    setTimeout(fn, ms) { assert.equal(ms, 15000); timeout = fn; return 1; }, clearTimeout() {}
  });
  const item = await add(e.outbox);
  const sending = e.outbox.senden();
  while (!timeout) await Promise.resolve();
  timeout(); await sending;
  assert.equal(signal.aborted, true);
  assert.equal((await e.outbox.liste())[0].id, item.id);
  e.advance(5000); await e.outbox.senden();
  assert.equal(new URLSearchParams(e.calls[1].body).get('client_event_id'), item.id);
});

for (const mode of ['missing', 'throwing', 'blocked', 'timeout']) {
  test(`IndexedDB ${mode} falls back to memory and localStorage across restart`, async () => {
    const saved = new Map();
    const storage = { getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value) };
    const indexedDB = mode === 'missing' ? null : mode === 'throwing' ? { open() { throw Error('denied'); } }
      : { open() { const request = {}; if (mode === 'blocked') queueMicrotask(() => request.onblocked()); return request; } };
    const e = environment([], { indexedDB, localStorage: storage, openTimeoutMs: 5 });
    const item = await add(e.outbox);
    assert.ok(saved.has('ec-asuew-outbox-42'));
    const restarted = e.api.createOutbox(e.config);
    assert.equal((await restarted.liste())[0].id, item.id);
    await restarted.senden();
    assert.equal(await restarted.anzahl(), 0);
    assert.equal(saved.get('ec-asuew-outbox-42'), '[]');
  });
}

test('unavailable storage still permits in-memory operation, subscriptions can unsubscribe', async () => {
  let changes = 0, subscribed = 0;
  const e = environment([], { indexedDB: null, localStorage: { getItem() { throw Error(); }, setItem() { throw Error(); } }, onChange: () => changes++ });
  const off = e.outbox.abonnieren(() => subscribed++);
  const item = await add(e.outbox); off();
  await e.outbox.verwerfen(item.id);
  assert.equal(changes, 2); assert.equal(subscribed, 1);
  assert.match(e.api.zufallsUuid(null), /^[a-f0-9-]{36}$/);
});

test('fallback entries migrate when IndexedDB becomes available again', async () => {
  const saved = new Map();
  const storage = { getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value) };
  const e = environment([], { indexedDB: null, localStorage: storage });
  const item = await add(e.outbox);
  const recovered = e.api.createOutbox({ ...e.config, indexedDB: new IDBFactory() });
  assert.equal((await recovered.liste())[0].id, item.id);
  await recovered.senden();
  assert.equal(await recovered.anzahl(), 0);
  assert.equal(saved.get('ec-asuew-outbox-42-fallback'), '0');
});

const board = readFileSync(new URL('../../app/templates/breathing/board.html', import.meta.url), 'utf8');
const boardCode = board.split('/* Offline-Erfassung:')[1].split('/* ── Audio unlock')[0]
  .replaceAll('{{ incident.id }}', '42').replaceAll('{{ request.state.csrf_token | tojson }}', '"csrf"')
  .replaceAll(/\{%[^%]*%\}/g, '');
async function boardEnvironment({ online = false } = {}) {
  const e = environment();
  const elements = new Map(), bodyHandlers = new Map(), docHandlers = new Map(), windowHandlers = new Map();
  const info = [], errors = [], nativeCalls = [], pressure = [], reloads = [], intervals = [];
  function element() {
    return { style: {}, children: [], handlers: new Map(),
      addEventListener(name, fn) { this.handlers.set(name, fn); },
      replaceChildren() { this.children = []; },
      append(...items) { this.children.push(...items); },
      appendChild(item) { this.children.push(item); } };
  }
  const document = { hidden: false,
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: element,
    body: { addEventListener(name, fn) { bodyHandlers.set(name, fn); } },
    addEventListener(name, fn) { docHandlers.set(name, fn); }
  };
  const window = { indexedDB: new IDBFactory(), fetch: e.config.fetch,
    ELNative: { asuew: { noteLocalEvent: async args => nativeCalls.push(args) } },
    dispatchEvent(event) { pressure.push(event); },
    addEventListener(name, fn) { windowHandlers.set(name, fn); }, setTimeout() {}
  };
  let ackResult = { ok: false, status: 0 };
  const context = vm.createContext({ window, document, navigator: { onLine: online },
    ECPBreathingOutbox: e.api, ECPAsuewSitzung: { getOffsetMs: () => 12000 },
    ECPBreathingNet: { showInfo: msg => info.push(msg), showError: msg => errors.push(msg),
      formFailed: event => errors.push(event), send: async () => ackResult,
      describeFailure: (status, message) => status + ': ' + message },
    htmx: { ajax: (...args) => reloads.push(args) }, location: { pathname: '/board', search: '' },
    setInterval(fn, ms) { intervals.push({ fn, ms }); }, FormData,
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    _stopLoopingWarning: (...args) => nativeCalls.push(args)
  });
  vm.runInContext(readFileSync(new URL('../../app/static/js/server_clock.js', import.meta.url), 'utf8'), context);
  context.ECPServerClock = window.ECPServerClock;
  context.ECPServerClock.setOffsetMs(12000);
  vm.runInContext('/* Offline-Erfassung:' + boardCode, context);
  await vm.runInContext('sendBreathingOutbox()', context);
  return { ...e, context, elements, document, window, bodyHandlers, docHandlers, windowHandlers,
    info, errors, nativeCalls, pressure, reloads, intervals,
    execute: code => vm.runInContext(code, context),
    setAck: result => { ackResult = result; },
    async request(route, fields) {
      const config = { verb: 'post', path: '/einsatz/42/atemschutz/7/' + route, parameters: fields };
      bodyHandlers.get('htmx:configRequest')({ detail: config });
      return config;
    },
    async fail(config, type = 'htmx:sendError') {
      let stopped = false;
      bodyHandlers.get(type)({ detail: { requestConfig: config }, stopPropagation() { stopped = true; } });
      await vm.runInContext('breathingQueueWork', context);
      await vm.runInContext('sendBreathingOutbox()', context);
      assert.equal(stopped, true);
    }
  };
}

test('board queues exact failed parameters once, suppresses global toast, mirrors native deadline and pressure', async () => {
  const e = await boardEnvironment();
  const config = await e.request('druck', { pressure_bar: '180', troop_member_id: '9' });
  const id = config.parameters.client_event_id, at = config.parameters.erfasst_at;
  config.parameters.pressure_bar = '90'; // Form changes must not alter the failed request snapshot.
  await e.fail(config);
  await e.fail(config, 'htmx:timeout');
  const items = await e.execute('breathingOutbox.liste()');
  assert.equal(items.length, 1);
  assert.equal(items[0].id, id);
  assert.equal(items[0].erfasst_at, at);
  assert.equal(items[0].fields.pressure_bar, '180');
  assert.equal(e.pressure[0].detail.pressure, 180);
  assert.equal(e.pressure[0].detail.local, true);
  assert.equal(e.pressure[0].detail.waiting, true);
  assert.equal(e.nativeCalls[0].kind, 'druck');
  assert.equal(e.nativeCalls[0].atMs, Date.parse(at));
  assert.equal(e.info[0], 'Offline vorgemerkt - wird automatisch gesendet');
  assert.equal(e.errors.length, 0);
  assert.equal(e.elements.get('breathing-outbox').hidden, false);
  assert.equal(e.elements.get('breathing-outbox-count').textContent, '1 Eingaben warten auf Übertragung');
  assert.equal(config.timeout, 15000);
  e.execute('doBreathingReload()');
  assert.equal(e.reloads.length, 0);
  e.execute('navigator.onLine = true');
  await e.windowHandlers.get('online')();
  assert.equal(e.reloads.length, 1);
  assert.equal(await e.execute('breathingOutbox.anzahl()'), 0);
});

test('board ack offline is treated as success; controls and ack mirror correct native arguments', async () => {
  const e = await boardEnvironment();
  const button = { disabled: false, closest: () => ({ id: 'troop-7' }) };
  e.context.testButton = button;
  await e.execute("ackWarning(testButton, '/einsatz/42/atemschutz/7/ack', 'withdraw', 'warning')");
  assert.equal(button.disabled, true);
  assert.equal(e.elements.get('warning').style.opacity, '0.45');
  assert.equal(e.nativeCalls[0].kind, 'ack');
  assert.equal(e.nativeCalls[0].ackKind, 'withdraw');
  assert.equal(e.nativeCalls[1].join(','), 'withdraw,7');
  const control = await e.request('kontrolle', { stufe: 'one_third', art: 'funk' });
  await e.fail(control);
  assert.equal(e.nativeCalls[2].kind, 'kontrolle');
  assert.equal(e.nativeCalls[2].stufe, 'one_third');
  assert.equal(e.pressure.length, 0);
  const noContact = await e.request('kontrolle', { stufe: 'two_third', art: 'kein_kontakt' });
  await e.fail(noContact);
  assert.equal(e.nativeCalls.length, 3);
});

test('board keeps online-only actions out of outbox and registers all recurring send triggers', async () => {
  const e = await boardEnvironment();
  for (const route of ['starten', 'trupp', 'erneut-einsetzen', 'notfall/aufheben']) {
    const config = await e.request(route, {});
    assert.equal(config.parameters.client_event_id, undefined);
    await e.fail(config);
  }
  assert.equal(await e.execute('breathingOutbox.anzahl()'), 0);
  assert.equal(e.errors.length, 4);
  assert.equal(e.intervals[0].ms, 15000);
  assert.ok(e.windowHandlers.has('online'));
  assert.ok(e.docHandlers.has('visibilitychange'));
  assert.match(board, /ws\.onopen = function \(\) \{[\s\S]*?sendBreathingOutbox\(\);/);
});

test('reload during empty in-flight send is deferred and performed once afterward', async () => {
  const e = await boardEnvironment({ online: true });
  e.execute('sendBreathingOutbox(); doBreathingReload(); doBreathingReload();');
  assert.equal(e.reloads.length, 0);
  await e.execute('sendBreathingOutbox()');
  assert.equal(e.reloads.length, 1);
});
