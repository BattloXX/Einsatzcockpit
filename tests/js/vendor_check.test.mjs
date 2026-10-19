import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import test from 'node:test';
import { mapping } from '../../scripts/vendor-js.mjs';

test('all mapped vendor targets exist', () => {
  for (const { to } of mapping) {
    assert.ok(existsSync(new URL(`../../${to}`, import.meta.url)), `Missing vendor target: ${to}`);
  }
});
