import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../app/static/js/server_clock.js', import.meta.url), 'utf8');
function clock() {
  let local = Date.parse('2026-11-06T12:00:00Z');
  class LocalDate extends Date { static now() { return local; } }
  const context = { module: { exports: {} }, window: {}, Date: LocalDate };
  vm.runInNewContext(source, context);
  assert.equal(context.window.ECPServerClock, context.module.exports);
  return { api: context.module.exports, local, advance(ms) { local += ms; } };
}

test('server clock sync supports positive and negative offsets and follows local time', () => {
  const e = clock();
  assert.equal(e.api.now(), e.local);
  e.api.sync('2026-11-06T12:00:12Z', e.local);
  assert.equal(e.api.now(), e.local + 12000);
  e.advance(1500);
  assert.equal(e.api.now(), e.local + 13500);
  e.api.sync('2026-11-06T11:59:50Z');
  assert.equal(e.api.now(), e.local - 10000);
});

test('session offset and zero offset replace the render offset', () => {
  const e = clock();
  e.api.setOffsetMs(-5000);
  assert.equal(e.api.now(), e.local - 5000);
  e.api.setOffsetMs(0);
  assert.equal(e.api.now(), e.local);
});

test('invalid sync and invalid session offsets leave the clock unchanged', () => {
  const e = clock();
  e.api.setOffsetMs(3000);
  for (const value of ['', 'bad', null, undefined]) e.api.sync(value, e.local);
  e.api.sync('2026-11-06T12:00:00Z', NaN);
  e.api.setOffsetMs(Infinity);
  e.api.setOffsetMs(NaN);
  assert.equal(e.api.now(), e.local + 3000);
});
