import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../../app/static/js/breathing_alerts.js', import.meta.url), 'utf8');
function environment() {
  let current = null;
  const listeners = new Map(), timers = [];
  const card = { highlighted: false, scrolled: null,
    scrollIntoView(options) { this.scrolled = options; },
    classList: { add() { card.highlighted = true; }, remove() { card.highlighted = false; } } };
  vm.runInNewContext(source, {
    Set, JSON, Infinity,
    document: { getElementById: id => id === 'breathingAlertBar' ? current : card,
      body: { addEventListener: (event, fn) => listeners.set(event, fn) } },
    window: { setTimeout: fn => timers.push(fn) }
  });
  function show(alerts) {
    const attributes = new Map();
    const controls = new Map();
    const text = { textContent: alerts[0].text };
    for (const key of ['[data-alert-jump]', '[data-alert-next]']) {
      controls.set(key, { addEventListener: (_, fn) => controls.set(key, fn) });
    }
    current = { dataset: { alerts: JSON.stringify(alerts) },
      setAttribute: (key, value) => attributes.set(key, value), removeAttribute: key => attributes.delete(key),
      querySelector: key => key === '[data-alert-text]' ? text : controls.get(key) };
    listeners.get('htmx:afterSettle')();
    return { attributes, controls, text };
  }
  return { show, card, flush() { while (timers.length) timers.shift()(); } };
}
const warning = (key, rank) => ({ key, rank, text: key, color: rank === 0 ? 'red' : 'yellow', troop_id: 7, emergency: rank === 0 });
test('alert only announces first warning or a newly more urgent warning across summary swaps', () => {
  const e = environment();
  const initial = e.show([warning('control', 4)]);
  assert.equal(initial.attributes.get('role'), 'alert');
  assert.equal(initial.attributes.get('aria-live'), 'assertive');
  e.flush();
  assert.equal(initial.attributes.has('role'), false);
  assert.equal(initial.text.textContent, 'control');
  assert.equal(e.show([warning('control', 4)]).attributes.has('role'), false);
  assert.equal(e.show([warning('heartbeat', 5)]).attributes.has('role'), false);
  assert.equal(e.show([warning('emergency', 0)]).attributes.get('role'), 'alert');
});
test('next warning changes the line, scrolls to its card and briefly highlights it', () => {
  const e = environment();
  const bar = e.show([warning('emergency', 0), warning('control', 4)]);
  e.flush();
  bar.controls.get('[data-alert-next]')();
  assert.equal(bar.text.textContent, 'control');
  assert.equal(e.card.scrolled.block, 'center');
  assert.equal(e.card.scrolled.behavior, 'smooth');
  assert.equal(e.card.highlighted, true);
  e.flush();
  assert.equal(e.card.highlighted, false);
});
