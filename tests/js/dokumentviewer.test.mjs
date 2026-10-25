import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
const {parsePages, compressPages} = createRequire(import.meta.url)('../../app/static/js/dokumentviewer.js');

test('freie Seitenauswahl, Unicode-Bereiche, Duplikate und ganze Dokumente', () => {
  assert.deepEqual(parsePages('Seite 5', 15), [5]);
  assert.deepEqual(parsePages('Seiten 5–8', 15), [5, 6, 7, 8]);
  assert.deepEqual(parsePages('2, 5 und 9', 15), [2, 5, 9]);
  assert.deepEqual(parsePages('3–5 und 12–15', 15), [3, 4, 5, 12, 13, 14, 15]);
  assert.deepEqual(parsePages('5, 2, 2-4', 15), [2, 3, 4, 5]);
  assert.deepEqual(parsePages('alle', 3), [1, 2, 3]);
  assert.equal(compressPages([2, 3, 4, 7, 9, 10]), '2-4,7,9-10');
});

test('ungültige und übergroße Auswahl führt zu einer verständlichen Fehlermeldung', () => {
  for (const input of ['', '0', '-2', '6', '3-1', '1,,2', '1e2', '1.5', '1-99999999999999']) {
    assert.throws(() => parsePages(input, 5));
  }
  assert.throws(() => parsePages('alle', 2001), /2000 Seiten/);
  assert.throws(() => parsePages('1', 0));
});
