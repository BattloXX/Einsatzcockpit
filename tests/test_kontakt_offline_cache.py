"""Service-Worker-Verhalten bei Rechteentzug und Identitaetswechsel."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node fuer den Service-Worker-Test erforderlich")
def test_rechteentzug_entfernt_offlinekopien_und_kontowechsel_bereinigt_caches():
    script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
(async () => {
  const handlers = {};
  const buckets = new Map();
  let offline = false;
  const cacheFor = async (name) => {
    if (!buckets.has(name)) buckets.set(name, new Map());
    const values = buckets.get(name);
    const key = request => typeof request === 'string' ? request : request.url;
    return {
      match: async request => values.get(key(request)),
      put: async (request, response) => values.set(key(request), response),
      delete: async request => values.delete(key(request)),
    };
  };
  const caches = {
    open: cacheFor,
    match: async (request, options) => (await cacheFor(options?.cacheName || 'core')).match(request),
    keys: async () => [...buckets.keys()],
    delete: async key => buckets.delete(key),
  };
  vm.runInNewContext(fs.readFileSync('app/static/sw.js', 'utf8'), {
    self: {addEventListener: (name, handler) => { handlers[name] = handler; }},
    location: {origin: 'https://test.local'}, caches, URL, Response,
    fetch: async () => {
      if (offline) throw Error('offline');
      return new Response('Forbidden', {status: 403});
    },
  });
  for (const [path, cacheName] of [
    ['/kontakte/1', 'ec-objekt-v2'],
    ['/kontakte/1/profilbild', 'ec-objekt-v2'],
    ['/objekte/1/einsatz?incident_id=2', 'ec-board-v4'],
    ['/objekte/1/einsatz-fragment?incident_id=2', 'ec-board-v4'],
    ['/einsatz/2/kontakte', 'ec-board-v4'],
  ]) {
    const request = new Request('https://test.local' + path);
    const cache = await cacheFor(cacheName);
    await cache.put(request, new Response('SECRET'));
    let pending;
    const event = {request, respondWith: promise => { pending = promise; }};
    offline = false;
    handlers.fetch(event);
    assert.equal((await pending).status, 403);
    assert.equal(await cache.match(request), undefined);
    offline = true;
    handlers.fetch(event);
    assert.equal((await pending).status, 503);
  }
  buckets.set('ec-objekt-v2', new Map());
  buckets.set('ec-board-v4', new Map());
  buckets.set('ec-nachschlagewerk-v1', new Map());
  const storage = new Map([['ec-kontakt-cache-owner', 'old:full']]);
  const window = {caches};
  const source = fs.readFileSync('app/static/js/app.js', 'utf8');
  vm.runInNewContext(source.slice(0, source.indexOf('window.closeSiteDetailPanel')), {
    window, caches, document: {body: {dataset: {kontaktCacheOwner: 'new:restricted'}}},
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
  });
  await window.ecKontaktCacheReady;
  assert.equal(buckets.has('ec-objekt-v2'), false);
  assert.equal(buckets.has('ec-board-v4'), false);
  assert.equal(buckets.has('ec-nachschlagewerk-v1'), true);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
