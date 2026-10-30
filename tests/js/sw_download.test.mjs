import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/sw.js', import.meta.url), 'utf8');

for (const path of [
  '/objekt-medien/dokument/4/original?download=1',
  '/objekt-medien/seite/7/pdf?download=1&nt=token',
  '/objekte/1/dokumente/sammel-pdf?nur_einsatzdruck=1&download=1',
  '/objekt-medien/dokument/4/original?nt=token',
]) {
  test(`Download/Handoff umgeht Service-Worker-Cache: ${path}`, () => {
    const listeners = new Map();
    vm.runInNewContext(source, {
      self: { addEventListener: (name, fn) => listeners.set(name, fn) },
      URL, location: { origin: 'https://ecp.test' },
      caches: { open() { assert.fail('Kein Cachezugriff bei Downloads'); } },
    });
    listeners.get('fetch')({
      request: { url: 'https://ecp.test' + path, method: 'GET' },
      respondWith() { assert.fail('Nativer Netzwerkrequest muss unveraendert bleiben'); },
    });
  });
}
