import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/js/breathing_net.js', import.meta.url), 'utf8');

function environment(extra = {}) {
  const window = {};
  const module = { exports: {} };
  vm.runInNewContext(source, { window, module, AbortController, setTimeout, clearTimeout, ...extra });
  assert.equal(window.ECPBreathingNet, module.exports);
  return module.exports;
}

test('backoff starts at one second and caps at thirty seconds', () => {
  const net = environment();
  assert.equal(net.backoffMs(0), 1000);
  assert.equal(net.backoffMs(5), 30000);
  assert.equal(net.backoffMs(10), 30000);
});

test('send confirms only response.ok and sends same-origin FormData with abort signal', async () => {
  const net = environment();
  const formData = { kind: 'withdraw' };
  const result = await net.send('/atemschutz/ack', formData, {
    fetchImpl: async (url, options) => {
      assert.equal(url, '/atemschutz/ack');
      assert.equal(options.method, 'POST');
      assert.equal(options.credentials, 'same-origin');
      assert.equal(options.body, formData);
      assert.equal(options.signal.aborted, false);
      return { ok: true, status: 200, text: async () => '  gespeichert\n' };
    }
  });
  assert.equal(result.ok, true);
  assert.equal(result.status, 200);
  assert.equal(result.message, 'gespeichert');
});

test('send returns a 400 plaintext body, trimmed and limited to 200 characters', async () => {
  const result = await environment().send('/atemschutz/druck', {}, {
    fetchImpl: async () => ({ ok: false, status: 400, text: async () => '  Druck ungültig  ' })
  });
  assert.equal(result.ok, false);
  assert.equal(result.status, 400);
  assert.equal(result.message, 'Druck ungültig');
  const long = await environment().send('/atemschutz/druck', {}, {
    fetchImpl: async () => ({ ok: false, status: 400, text: async () => '  ' + 'x'.repeat(250) + '\n' })
  });
  assert.equal(long.message, 'x'.repeat(200));
});

test('send reports network errors without rejecting', async () => {
  const result = await environment().send('/atemschutz/ack', {}, {
    fetchImpl: async () => { throw new TypeError('offline'); }
  });
  assert.equal(result.ok, false);
  assert.equal(result.status, 0);
  assert.equal(result.message, 'Keine Verbindung zum Server');
});

test('send times out and aborts even when fetch does not settle', async () => {
  let timeout;
  let cleared = false;
  let signal;
  const net = environment({
    setTimeout(fn, ms) { assert.equal(ms, 25); timeout = fn; return 42; },
    clearTimeout(id) { assert.equal(id, 42); cleared = true; }
  });
  const pending = net.send('/atemschutz/ack', {}, {
    timeoutMs: 25,
    fetchImpl: (_url, options) => { signal = options.signal; return new Promise(() => {}); }
  });
  timeout();
  const result = await pending;
  assert.equal(signal.aborted, true);
  assert.equal(result.ok, false);
  assert.equal(result.status, 0);
  assert.equal(result.message, 'Keine Verbindung zum Server');
  assert.equal(cleared, true);
});

test('send keeps timeout active while reading the response body', async () => {
  let timeout;
  const net = environment({ setTimeout(fn) { timeout = fn; }, clearTimeout() {} });
  const pending = net.send('/atemschutz/ack', {}, {
    fetchImpl: async () => ({ ok: true, status: 200, text: () => new Promise(() => {}) })
  });
  await Promise.resolve();
  timeout();
  assert.equal((await pending).ok, false);
});

test('describeFailure distinguishes conflict, server rejection and loss of connection', () => {
  const net = environment();
  assert.equal(net.describeFailure(400, 'Druck ungültig'), 'Nicht gespeichert: Druck ungültig');
  assert.equal(net.describeFailure(409, 'Trupp zurück'), 'Status wurde bereits geändert: Trupp zurück');
  assert.equal(net.describeFailure(0, 'Keine Verbindung zum Server'), 'Nicht gespeichert: keine Verbindung');
});

test('connection tracker preserves the first loss until the next open', () => {
  let now = 1000;
  const tracker = environment().createConnTracker(() => now);
  assert.equal(tracker.lostSeconds(), null);
  tracker.onOpen();
  assert.equal(tracker.lostSeconds(), null);
  tracker.onClose();
  assert.equal(tracker.lostSeconds(), 0);
  now += 21500;
  tracker.onClose();
  assert.equal(tracker.lostSeconds(), 21);
  tracker.onOpen();
  assert.equal(tracker.lostSeconds(), null);
  tracker.onClose();
  now += 1500;
  assert.equal(tracker.lostSeconds(), 1);
});

function bannerEnvironment() {
  const elements = new Map(), timers = [], events = [];
  const document = {
    getElementById: id => elements.get(id),
    createElement() { return {
      style: {}, setAttribute(key, value) { this[key] = value; },
      addEventListener() {}, append(...children) { this.children = children; },
      remove() { elements.delete(this.id); }
    }; },
    body: { prepend(element) { elements.set(element.id, element); } }
  };
  const net = environment({ document,
    setTimeout(fn, ms) { timers.push({ fn, ms }); },
    CustomEvent: class { constructor(type, options) { this.type = type; this.bubbles = options.bubbles; } }
  });
  const form = { closest: () => form, getAttribute: () => '/einsatz/42/atemschutz/7/druck',
    querySelector: () => null, dispatchEvent: event => events.push(event) };
  return { net, elements, timers, events, form };
}

test('showInfo uses neutral status banner, replaces previous message and closes after six seconds', () => {
  const e = bannerEnvironment();
  e.net.showInfo('Offline vorgemerkt');
  const first = e.elements.get('breathing-info-banner');
  assert.equal(first.role, 'status');
  assert.equal(first.textContent, 'Offline vorgemerkt');
  assert.equal(e.elements.has('breathing-error-banner'), false);
  e.net.showInfo('Noch eine Eingabe');
  assert.notEqual(e.elements.get('breathing-info-banner'), first);
  assert.equal(e.timers[1].ms, 6000);
  e.timers[1].fn();
  assert.equal(e.elements.size, 0);
});

test('formFailed status zero emits queued on form without error; HTTP failure shows error once', () => {
  const e = bannerEnvironment();
  e.net.formFailed({ detail: { xhr: { status: 0 } }, target: e.form });
  assert.equal(e.events[0].type, 'breathing-queued');
  assert.equal(e.events[0].bubbles, true);
  assert.equal(e.elements.size, 0);
  const event = { detail: { xhr: { status: 400, responseText: 'Ungültiger Druck' } }, target: e.form };
  e.net.formFailed(event);
  const banner = e.elements.get('breathing-error-banner');
  assert.equal(banner.children[0].textContent, 'Nicht gespeichert: Ungültiger Druck');
  e.net.formFailed(event);
  assert.equal(e.elements.get('breathing-error-banner'), banner);
});

test('online-only network failure reports clear error without queued event', () => {
  for (const route of ['starten', 'trupp', 'erneut-einsetzen', 'notfall/aufheben']) {
    const e = bannerEnvironment();
    e.net.formFailed({ detail: { xhr: { status: 0 }, requestConfig: { path: '/einsatz/42/atemschutz/7/' + route } }, target: e.form });
    assert.equal(e.events.length, 0);
    assert.equal(e.elements.get('breathing-error-banner').children[0].textContent, 'Diese Aktion ist nur online möglich.');
  }
});

test('pressure increase conflict opens correction and displays error next to field', () => {
  const e = bannerEnvironment(), details = {}, error = {};
  let focused = false;
  const field = { closest: () => details, focus() { focused = true; } };
  e.form.querySelector = selector => selector === '[data-correction-error]' ? error : field;
  const message = 'Der Druck kann nicht steigen. Bei einer Korrektur bitte einen Grund angeben.';
  e.net.formFailed({ detail: { xhr: { status: 409, responseText: message } }, target: e.form });
  assert.equal(details.open, true);
  assert.equal(error.textContent, message);
  assert.equal(focused, true);
  assert.equal(e.elements.size, 0);
});
