import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/js/app.js', import.meta.url), 'utf8');
const boardSource = source.slice(source.indexOf('function incidentBoard('), source.indexOf('async function moveVehicle('));

test('Timer-Popup escaped Meldungstext und rendert Icons mit beschriftetem Schliessen-Button', () => {
  const elements = [];
  const context = vm.createContext({
    document: {
      createElement() { return {}; },
      body: { appendChild(el) { elements.push(el); } },
    },
    setTimeout() {},
  });
  vm.runInContext(readFileSync(new URL('../../app/static/js/icons.js', import.meta.url), 'utf8'), context);
  vm.runInContext(source.slice(source.indexOf('function showTimerAlert('), source.indexOf('let recognition =')), context);
  context.showTimerAlert('<img src=x onerror="alert(1)"> & \'Text\'', 'warn');
  const html = elements[0].innerHTML;
  assert.ok(html.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; &#39;Text&#39;'));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('ecp-icon--sm'));
  assert.match(html, /<button aria-label="Schließen"[^>]*><svg class="ecp-icon ecp-icon--xs"/);
});

function setup() {
  const calls = [];
  const listeners = {};
  const timers = new Map();
  let timerId = 0;
  const grid = {
    dialogOpen: false,
    querySelector() { return this.dialogOpen ? {} : null; },
    contains(el) { return el?.inGrid; },
  };
  const document = {
    activeElement: null,
    body: {},
    getElementById(id) { return ['troopsGrid', 'fjList', 'topnavLeader'].includes(id) ? grid : null; },
    addEventListener(type, callback) { (listeners[type] ||= []).push(callback); },
  };
  const context = vm.createContext({
    document,
    window: {},
    location: { pathname: '/einsatz/42', search: '' },
    localStorage: { getItem() { return null; } },
    htmx: { ajax(...args) { calls.push(args); return Promise.resolve(); } },
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(boardSource, context);
  const board = context.incidentBoard(42);
  const flush = () => {
    const pending = [...timers.values()];
    timers.clear();
    pending.forEach(callback => callback());
  };
  const emit = (type, event = {}) => (listeners[type] || []).forEach(callback => callback(event));
  return { board, calls, document, grid, flush, emit };
}

test('Atemschutz fasst Events zusammen und verschiebt bei Eingaben bis focusout', () => {
  const { board, calls, document, flush, emit } = setup();
  document.activeElement = { inGrid: true, matches() { return true; } };
  board._queueBreathing(42);
  board._queueBreathing(42);
  flush();
  assert.equal(calls.length, 0);
  emit('focusout');
  flush();
  assert.equal(calls.length, 0);
  document.activeElement = null;
  emit('focusout');
  flush();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1], '/einsatz/42/atemschutz');
  assert.equal(calls[0][2].select, '#troopsGrid');
  assert.equal(calls[0][2].swap, 'outerHTML');
  calls.length = 0;
  board._queueBreathing(42);
  board._queueBreathing(42);
  board._queueBreathing(42);
  flush();
  assert.equal(calls.length, 1);
});

test('Dialoge und Fokus waehrend laufendem GET verhindern den Swap', () => {
  const { board, calls, document, grid, flush, emit } = setup();
  grid.dialogOpen = true;
  board._queueBreathing(42);
  flush();
  assert.equal(calls.length, 0);
  grid.dialogOpen = false;
  emit('close');
  flush();
  assert.equal(calls.length, 1);
  document.activeElement = { inGrid: true, matches() { return true; } };
  const event = { detail: { target: { id: 'troopsGrid' }, shouldSwap: true } };
  emit('htmx:beforeSwap', event);
  assert.equal(event.detail.shouldSwap, false);
  document.activeElement = null;
  emit('focusout');
  flush();
  assert.equal(calls.length, 2);
});

test('Funkjournal und EL-Events aktualisieren ihre Fragmente', () => {
  const { board, calls } = setup();
  board._handleBoardEvent({ type: 'funkjournal_changed' }, 42);
  assert.equal(calls[0][1], '/einsatz/42/funkjournal');
  assert.equal(calls[0][2].select, '#fjList');
  board._handleBoardEvent({ type: 'incident_leader_changed' }, 42);
  assert.equal(calls[1][2].select, '#topnavLeader');
  assert.equal(calls[2][1], '/einsatz/42/kopfleiste');
});

test('Funkjournal behaelt Suche und Offen-Filter nach einem Listen-Swap', () => {
  const template = readFileSync(new URL('../../app/templates/incident/funkjournal.html', import.meta.url), 'utf8');
  const script = template.match(/<script>([\s\S]*?)<\/script>/)[1].replaceAll('{{ incident.id }}', '42');
  const listeners = {};
  const search = { value: '  Florian  ' };
  const btn = { style: {} };
  const hint = { style: {} };
  let rows = [];
  const row = (text, handled = 'false') => ({
    dataset: { fjSearch: text, isRequest: 'true', handled }, style: {},
  });
  const context = vm.createContext({
    document: {
      getElementById(id) { return { 'fj-search': search, 'fj-filter-btn': btn, 'fj-filter-hint': hint }[id]; },
      querySelectorAll() { return rows; },
      addEventListener(type, callback) { listeners[type] = callback; },
    },
  });
  vm.runInContext(script, context);
  context.toggleOpenFilter();
  rows = [row('florian west'), row('florian ost', 'true'), row('abschnitt nord')];
  listeners['htmx:afterSwap']({ target: { id: 'fjList' } });
  assert.deepEqual(rows.map(item => item.style.display), ['', 'none', 'none']);
  assert.equal(hint.style.display, '');
  assert.equal(search.value, '  Florian  ');
  context.toggleOpenFilter();
  assert.deepEqual(rows.map(item => item.style.display), ['', '', 'none']);
});

test('Beide lokalen EL-Formulare aktualisieren nur bei erfolgreichem POST', () => {
  const calls = [];
  const start = source.indexOf("document.addEventListener('htmx:afterRequest', (e) => {\n  const config");
  const end = source.indexOf('\n});', start) + 4;
  let handler;
  const context = vm.createContext({
    document: { addEventListener(type, callback) { handler = callback; } },
    incidentBoard(id) { return { _swapLeader() { calls.push(id); } }; },
  });
  vm.runInContext(source.slice(start, end), context);
  for (const suffix of ['einsatzleiter-mitglied', 'einsatzleiter-mitglied-neu']) {
    const requestConfig = { verb: 'post', path: `/einsatz/42/${suffix}` };
    handler({ detail: { successful: false, requestConfig } });
    handler({ detail: { successful: true, requestConfig } });
  }
  assert.deepEqual(calls, ['42', '42']);
});

test('Resync auf Unterseiten beansprucht keinen Refresh; Kanban meldet Ladeauftrag', async () => {
  const listeners = new Map();
  let kanban = null;
  const context = vm.createContext({
    window: { addEventListener(name, callback) { listeners.set(name, callback); } },
    document: {
      body: { addEventListener() {} },
      getElementById(id) { return id === 'kanban' ? kanban : null; },
    },
    localStorage: { getItem() { return null; } },
    sessionStorage: { getItem() { return 'client'; }, setItem() {} },
    setInterval() {},
  });
  vm.runInContext(boardSource, context);
  const board = context.incidentBoard(42);
  board._connectWS = () => {};
  board._trackOpenModalEntity = () => {};
  board.init();
  const event = { detail: { source: 'pull', handled: false, promises: [] } };
  listeners.get('ec:resync')(event);
  assert.equal(event.detail.handled, false);
  assert.equal(event.detail.promises.length, 0);
  kanban = {};
  const work = Promise.resolve();
  board._resyncBoard = () => work;
  listeners.get('ec:resync')(event);
  assert.equal(event.detail.handled, true);
  assert.equal(event.detail.promises[0], work);
  await event.detail.promises[0];
});

test('Lage-PTR wartet auf Kopf und alle Spalten und reicht Spaltenfehler weiter', async () => {
  const lageSource = readFileSync(new URL('../../app/static/js/lage_board.js', import.meta.url), 'utf8');
  const resyncSource = lageSource.slice(lageSource.indexOf('  function resyncBoard('), lageSource.indexOf('  function updateConnectionStatus('));
  const calls = [];
  const pending = [];
  const columns = ['/phase/neu', '/uebergreifend/board-col'].map(url => ({ getAttribute() { return url; } }));
  const context = vm.createContext({
    window: { scrollTo() {} },
    document: { body: {}, querySelectorAll() { return columns; } },
    htmx: {
      ajax(method, url, options) {
        calls.push({ url, options });
        return new Promise((resolve, reject) => pending.push({ resolve, reject }));
      },
      trigger() { assert.fail('Pull muss die Auftraege direkt erfassen'); },
    },
    requestAnimationFrame(fn) { fn(); },
  });
  vm.runInContext(resyncSource, context);
  let finished = false;
  const work = context.resyncBoard(42, true);
  work.then(() => { finished = true; }, () => {});
  assert.deepEqual(calls.map(c => c.url), ['/lage/42/kopf', '/phase/neu', '/uebergreifend/board-col']);
  assert.equal(calls[1].options.target, columns[0]);
  pending[0].resolve();
  pending[1].resolve();
  await Promise.resolve();
  assert.equal(finished, false);
  pending[2].reject(new Error('Spalte fehlgeschlagen'));
  await assert.rejects(work, /Spalte fehlgeschlagen/);
});
