import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const board = readFileSync(new URL('../../app/templates/breathing/board.html', import.meta.url), 'utf8');
const code = board.split('/* Gezielte Updates:')[1].split('/* ── Warn-Quittierung:')[0].replaceAll('{{ incident.id }}', '42');
function environment() {
  let now = 0, sequence = 0, fullReloads = 0;
  const timers = new Map(), requests = [], listeners = new Map(), cards = new Map();
  const document = {
    activeElement: null,
    getElementById: id => cards.get(id),
    body: { addEventListener: (type, fn) => listeners.set(type, fn) }
  };
  const window = { setTimeout(fn, delay) { const id = ++sequence; timers.set(id, { at: now + delay, fn }); return id; } };
  class LocalDate extends Date { static now() { return now; } }
  const context = vm.createContext({ Date: LocalDate, window, document, navigator: { onLine: true },
    breathingLoaded: true, breathingSending: null, breathingPending: 0, breathingCount: 0, breathingReloadPending: false,
    getComputedStyle: popup => ({ visibility: popup.visibility || 'visible' }),
    doBreathingReload() { fullReloads += 1; },
    htmx: { ajax(method, url, options) {
      let resolve;
      const promise = new Promise(done => { resolve = done; });
      requests.push({ method, url, options, resolve });
      return promise;
    } }
  });
  vm.runInContext('/* Gezielte Updates:' + code, context);
  function addCard(id) {
    const card = { id: 'troop-' + id, popups: [],
      contains: el => el && el.card === card,
      querySelectorAll: () => card.popups,
      matches: selector => selector === '.troop-card'
    };
    cards.set(card.id, card);
    return card;
  }
  addCard(7); addCard(8);
  return { document, cards, requests, context, addCard,
    get fullReloads() { return fullReloads; },
    refresh(id) { context.refreshBreathingTroop(id); },
    async advance(ms) {
      const end = now + ms;
      while (true) {
        const first = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!first) break;
        now = first[1].at;
        timers.delete(first[0]); first[1].fn();
        await Promise.resolve();
      }
      now = end;
      await Promise.resolve();
    },
    beforeSwap(id) {
      const detail = { target: cards.get('troop-' + id), shouldSwap: true };
      listeners.get('htmx:beforeSwap')({ detail });
      return detail.shouldSwap;
    }
  };
}

test('events coalesce per troop while different troops share one summary request', async () => {
  const e = environment();
  e.refresh(7); e.refresh(7); e.refresh(8);
  await e.advance(299);
  assert.equal(e.requests.length, 0);
  await e.advance(1);
  assert.deepEqual(e.requests.filter(request => request.url.endsWith('/karte')).map(request => request.url), [
    '/einsatz/42/atemschutz/7/karte', '/einsatz/42/atemschutz/8/karte'
  ]);
  const cardRequest = e.requests.find(request => request.url.endsWith('/7/karte'));
  assert.equal(cardRequest.options.target, '#troop-7');
  assert.equal(cardRequest.options.swap, 'outerHTML');
  await e.advance(300);
  assert.equal(e.requests.filter(request => request.url.endsWith('/uebersicht')).length, 1);
});

test('missing card uses full reload and offline or waiting outbox defers fetching', async () => {
  const e = environment();
  e.refresh(999);
  assert.equal(e.fullReloads, 1);
  for (const state of ['navigator.onLine = false', 'breathingCount = 1', 'breathingPending = 1', 'breathingLoaded = false', 'breathingSending = {}']) {
    vm.runInContext('navigator.onLine = true; breathingCount = 0; breathingPending = 0; breathingLoaded = true; breathingSending = null; ' + state, e.context);
    e.refresh(7);
    await e.advance(400);
    assert.equal(e.requests.length, 0);
    assert.equal(e.context.breathingReloadPending, true);
  }
});

test('queued requests recheck outbox state before starting', async () => {
  const e = environment();
  e.refresh(7);
  e.context.breathingPending = 1;
  await e.advance(600);
  assert.equal(e.requests.length, 0);
  assert.equal(e.context.breathingReloadPending, true);
});

test('focused input and open popup delay replacement until closed', async () => {
  const e = environment();
  const card = e.cards.get('troop-7');
  e.document.activeElement = { card, matches: () => true };
  e.refresh(7);
  await e.advance(1000);
  assert.equal(e.requests.filter(request => request.url.endsWith('/karte')).length, 0);
  assert.equal(e.requests.filter(request => request.url.endsWith('/uebersicht')).length, 1);
  card.popups = [{ getClientRects: () => [1] }];
  e.document.activeElement = null;
  await e.advance(1000);
  assert.equal(e.requests.filter(request => request.url.endsWith('/karte')).length, 0);
  assert.equal(e.requests.filter(request => request.url.endsWith('/uebersicht')).length, 1);
  card.popups = [];
  await e.advance(100);
  assert.equal(e.requests.filter(request => request.url.endsWith('/karte')).length, 1);
});

test('open popup forces update after twenty seconds and hidden popup does not block', async () => {
  const e = environment();
  e.cards.get('troop-7').popups = [{ getClientRects: () => [1] }];
  e.cards.get('troop-8').popups = [{ getClientRects: () => [] }];
  e.refresh(7); e.refresh(8);
  await e.advance(19999);
  assert.equal(e.requests.filter(request => request.url.endsWith('/7/karte')).length, 0);
  assert.equal(e.requests.filter(request => request.url.endsWith('/8/karte')).length, 1);
  await e.advance(1);
  assert.equal(e.requests.filter(request => request.url.endsWith('/7/karte')).length, 1);
  assert.equal(e.beforeSwap(7), true);
});

test('focus gained during fetch is checked again before swap and a fresh response is requested', async () => {
  const e = environment();
  e.refresh(7);
  await e.advance(300);
  const card = e.cards.get('troop-7');
  e.document.activeElement = { card, matches: () => true };
  assert.equal(e.beforeSwap(7), false);
  e.requests.find(request => request.url.endsWith('/karte')).resolve();
  await e.advance(500);
  assert.equal(e.requests.filter(request => request.url.endsWith('/karte')).length, 1);
  e.document.activeElement = null;
  await e.advance(100);
  assert.equal(e.requests.filter(request => request.url.endsWith('/karte')).length, 2);
  e.context.breathingCount = 1;
  assert.equal(e.beforeSwap(7), false);
  assert.equal(e.context.breathingReloadPending, true);
});

test('troop timer uses the server clock and releases interval and pressure listener on destroy', () => {
  const code = 'function troopCard(' + board.split('function troopCard(')[1].split('/* ── New-troop form')[0];
  const listeners = new Set(), intervals = new Set();
  let now = Date.parse('2026-11-06T12:10:00Z');
  const context = { ECPServerClock: { now: () => now },
    setInterval(fn) { intervals.add(fn); return fn; }, clearInterval: fn => intervals.delete(fn),
    window: { addEventListener: (type, fn) => listeners.add(fn), removeEventListener: (type, fn) => listeners.delete(fn) }
  };
  vm.runInNewContext(code, context);
  const card = context.troopCard(180, 300, 100, '2026-11-06T12:09:30Z', '2026-11-06T12:00:00Z', 30, null, 30);
  card.$el = { id: 'troop-7' };
  card.init(); card.init();
  assert.equal(card.timeAgo, 'vor 30 Sek.');
  assert.equal(card.timerDisplay, '10:00');
  assert.equal(intervals.size, 1);
  assert.equal(listeners.size, 1);
  now += 60000;
  [...intervals][0]();
  assert.equal(card.timerDisplay, '11:00');
  card.destroy();
  assert.equal(intervals.size, 0);
  assert.equal(listeners.size, 0);
  const returned = context.troopCard(100, 300, 100, null, null, 30, '2026-11-06T12:00:00Z', 30);
  returned.$el = { id: 'troop-8' }; returned.init();
  assert.equal(returned.recoverySeconds, 19 * 60);
  returned.destroy();
});
