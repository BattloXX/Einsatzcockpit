import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { nextContrast, restore, toggle } = createRequire(import.meta.url)('../../app/static/js/breathing_contrast.js');
function board() {
  const values = new Set();
  return { classList: { contains: value => values.has(value), toggle(value, enabled) {
    if (enabled) values.add(value); else values.delete(value);
  } } };
}
test('contrast defaults off, restores preference and persists both toggle states', () => {
  const element = board();
  const values = new Map();
  const storage = () => ({ getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) });
  assert.equal(restore(element, storage), false);
  assert.equal(toggle(element, storage), true);
  assert.equal(values.get('ec-breathing-hc'), '1');
  assert.equal(restore(board(), storage), true);
  assert.equal(toggle(element, storage), false);
  assert.equal(values.get('ec-breathing-hc'), '0');
});
test('contrast still toggles when storage access or methods throw', () => {
  for (const storage of [() => { throw Error('access'); }, () => ({
    getItem() { throw Error('read'); }, setItem() { throw Error('write'); }
  })]) {
    const element = board();
    assert.equal(restore(element, storage), false);
    assert.equal(toggle(element, storage), true);
    assert.equal(element.classList.contains('breathing-hc'), true);
  }
});

test('pure contrast transition flips the current state', () => {
  assert.equal(nextContrast(false), true);
  assert.equal(nextContrast(true), false);
});
