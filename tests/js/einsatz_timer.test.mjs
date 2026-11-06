import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const appSource = readFileSync(new URL('../../app/static/js/app.js', import.meta.url), 'utf8');
const helpers = appSource.slice(appSource.indexOf('function elapsedDurationSeconds('), appSource.indexOf('/* ─── Incident Header'));
const context = vm.createContext({});
vm.runInContext(helpers, context);
const start = '2026-11-06T08:00:00Z';
const end = '2026-11-06T09:15:30Z';
const now = Date.parse('2026-11-06T10:00:00Z');

test('Einsatzdauer ohne Ende laeuft mit der aktuellen Zeit weiter', () => {
  assert.equal(context.elapsedDurationSeconds(start, null, now), 7200);
  assert.equal(context.elapsedDurationSeconds(start, null, now + 1000), 7201);
  assert.equal(context.formatElapsedDuration(7201), '2:00:01');
});

test('Einsatzdauer mit Ende bleibt unabhaengig von der aktuellen Zeit stehen', () => {
  for (const current of [now, now + 3600000]) {
    const duration = context.elapsedDurationSeconds(start, end, current);
    assert.equal(duration, 4530);
    assert.equal(context.formatElapsedDuration(duration), '1:15:30');
  }
  assert.equal(context.elapsedDurationSeconds(end, start, now), 0);
});

test('Live-Banner verwendet closed_at und ended_at sowie Serverzeit fuer aktive Einsaetze', () => {
  const tick = appSource.match(/    tick\(\) \{([\s\S]*?)\n    \},\n\n    dismiss/)[1];
  const sandbox = vm.createContext({Date: class extends Date { static now() { return now; } }});
  vm.runInContext(helpers + '\nfunction tick() {' + tick + '\n}', sandbox);
  for (const field of ['closed_at', 'ended_at']) {
    const state = {incident: {started_at: start, [field]: end}, serverTimeSkew: 60000};
    sandbox.tick.call(state);
    assert.equal(state.duration, '1:15:30');
  }
  const state = {incident: {started_at: start}, serverTimeSkew: 60000};
  sandbox.tick.call(state);
  assert.equal(state.duration, '2:01:00');
});

test('Dashboard liest nach WS-Swap neue Start- und Abschlussattribute', () => {
  const source = readFileSync(new URL('../../app/templates/incident/dashboard.html', import.meta.url), 'utf8');
  const timer = source.slice(source.indexOf('  function pad('), source.indexOf('  setInterval(tickTimer'));
  let element = {dataset: {startedAt: start, closedAt: ''}};
  let current = now;
  const sandbox = vm.createContext({
    Date: class extends Date { static now() { return current; } },
    document: {getElementById: () => element},
  });
  vm.runInContext(timer, sandbox);
  sandbox.tickTimer();
  assert.equal(element.textContent, '02:00:00');
  element = {dataset: {startedAt: '2026-11-06T08:30:00Z', closedAt: end}};
  sandbox.tickTimer();
  assert.equal(element.textContent, '00:45:30');
  current += 60000;
  sandbox.tickTimer();
  assert.equal(element.textContent, '00:45:30');
});
