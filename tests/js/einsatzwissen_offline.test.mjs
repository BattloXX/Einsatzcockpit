import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
const {validPackage} = createRequire(import.meta.url)('../../app/static/js/einsatzwissen_offline.js');

test('Offlinefreigaben sind benutzergebunden, zeitlich begrenzt und laufen geschlossen ab', () => {
  const data = {owner: '7:2', erstellt: 1000, gueltig_bis: 901000, eintraege: [{id: 1}]};
  assert.equal(validPackage(data, 1000, '7:2'), true);
  assert.equal(validPackage(data, 900999, '7:2'), true);
  assert.equal(validPackage(data, 901000, '7:2'), false);
  assert.equal(validPackage(data, 999, '7:2'), false);
  assert.equal(validPackage(data, 2000, '8:2'), false);
  assert.equal(validPackage(data, 2000, '7:3'), false);
  assert.equal(validPackage({...data, gueltig_bis: 901001}, 2000, '7:2'), false);
  assert.equal(validPackage({...data, eintraege: Array(21).fill({id: 1})}, 2000, '7:2'), false);
});
