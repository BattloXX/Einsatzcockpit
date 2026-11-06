import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source = readFileSync(new URL('../../app/static/js/asuew_logik.js', import.meta.url), 'utf8');
const module = { exports: {} }, window = {};
vm.runInNewContext(source, { module, window });
const logic = module.exports;
const vectors = JSON.parse(readFileSync(new URL('../data/asuew/logik_vektoren.json', import.meta.url), 'utf8'));
assert.equal(window.ECPAsuewLogik, logic);
for (const c of vectors.cases) {
  test(c.name, () => {
    const before = JSON.stringify(c.args);
    const actual = logic[c.function](...c.args);
    assert.deepEqual(JSON.parse(JSON.stringify(actual)), c.expected);
    assert.equal(JSON.stringify(c.args), before, 'Eingaben bleiben unveraendert');
  });
}
