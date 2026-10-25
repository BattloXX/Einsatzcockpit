import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import test from 'node:test';
import { mapping } from '../../scripts/vendor-js.mjs';

test('all mapped vendor targets exist', () => {
  for (const { to } of mapping) {
    assert.ok(existsSync(new URL(`../../${to}`, import.meta.url)), `Missing vendor target: ${to}`);
  }
});

test('PDF.js verwendet dieselbe feste Version für Paket, Viewer und unveränderliche Assetpfade', async () => {
  const {readFileSync} = await import('node:fs');
  const project = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url)));
  const installed = JSON.parse(readFileSync(new URL('../../node_modules/pdfjs-dist/package.json', import.meta.url)));
  assert.equal(project.devDependencies['pdfjs-dist'], installed.version);
  const source = readFileSync(new URL('../../app/static/js/dokumentviewer.js', import.meta.url), 'utf8');
  assert.ok(source.includes(`/static/vendor/pdfjs/${installed.version}/`));
  assert.ok(mapping.filter(item => item.pkg === 'pdfjs-dist').every(item => item.to.includes(`/${installed.version}/`)));
});

test('selected Tabler icons and license exist', async () => {
  const { readFileSync, readdirSync } = await import('node:fs');
  const directory = new URL('../../app/static/icons/tabler/', import.meta.url);
  const names = readFileSync(new URL('../../app/static/icons/tabler-auswahl.txt', import.meta.url), 'utf8')
    .split(/\r?\n/).map(line => line.trim()).filter(line => line && !line.startsWith('#'));
  assert.deepEqual(readdirSync(directory).filter(name => name.endsWith('.svg')).sort(), names.map(name => name + '.svg').sort());
  for (const name of names) {
    assert.deepEqual(readFileSync(new URL(name + '.svg', directory)), readFileSync(new URL(`../../node_modules/@tabler/icons/icons/outline/${name}.svg`, import.meta.url)));
  }
  assert.deepEqual(readFileSync(new URL('LICENSE', directory)), readFileSync(new URL('../../node_modules/@tabler/icons/LICENSE', import.meta.url)));
});
