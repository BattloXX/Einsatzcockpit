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
