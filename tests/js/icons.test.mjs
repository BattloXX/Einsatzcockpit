import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const {ecpIcon, ecpStatusDot, ecpMarkerIcon} = require('../../app/static/js/icons.js');

test('icons render sizes, escaped classes and accessible labels', () => {
  assert.match(ecpIcon('printer'), /class="ecp-icon ecp-icon--md" width="24" height="24"/);
  assert.match(ecpIcon('printer'), /aria-hidden="true" focusable="false"><path/);
  for (const [size, pixels] of Object.entries({xs: 16, sm: 20, md: 24, lg: 32})) {
    assert.ok(ecpIcon('printer', {size}).includes(`width="${pixels}" height="${pixels}"`));
  }
  const svg = ecpIcon('printer', {cls: '  foo   <script>" ', label: '<script>"&\''});
  assert.match(svg, /foo &lt;script&gt;&#34;/);
  assert.match(svg, /role="img" aria-label="&lt;script&gt;&#34;&amp;&#39;" focusable="false"><title>&lt;script&gt;&#34;&amp;&#39;<\/title>/);
  assert.ok(!svg.includes('aria-hidden'));
  assert.throws(() => ecpIcon('printer', {size: 'huge'}), RangeError);
});

test('unknown icons warn once per name and render fallback', () => {
  const original = console.warn;
  const warnings = [];
  console.warn = message => warnings.push(message);
  try {
    assert.equal(ecpIcon('missing-one'), ecpIcon('help-square'));
    assert.equal(ecpIcon('missing-one'), ecpIcon('help-square'));
    assert.equal(ecpIcon('missing-two'), ecpIcon('help-square'));
    assert.equal(ecpIcon('toString'), ecpIcon('help-square'));
    assert.equal(warnings.length, 3);
  } finally { console.warn = original; }
});

test('status dots validate status and escape labels', () => {
  for (const status of ['rot', 'orange', 'gelb', 'gruen', 'blau', 'grau', 'schwarz', 'weiss']) {
    assert.equal(ecpStatusDot(status, '<"&'), `<span class="status-dot status-dot--${status}" role="img" aria-label="&lt;&#34;&amp;" title="&lt;&#34;&amp;"></span>`);
  }
  assert.throws(() => ecpStatusDot('purple', 'x'), RangeError);
});

test('map markers work with Leaflet and sanitize colors and dimensions', () => {
  assert.equal(ecpMarkerIcon('printer'), null);
  globalThis.L = {divIcon: o => o};
  try {
    const marker = ecpMarkerIcon('printer', {size: 36, color: 'var(--red)', cls: 'extra'});
    assert.equal(marker.className, 'ecp-map-marker-wrap');
    assert.deepEqual(marker.iconSize, [36, 36]);
    assert.deepEqual(marker.iconAnchor, [18, 18]);
    assert.deepEqual(marker.popupAnchor, [0, -18]);
    assert.match(marker.html, /ecp-map-marker extra/);
    assert.match(marker.html, /--marker-color:var\(--red\);width:36px;height:36px/);
    assert.match(marker.html, /ecp-icon--md/);
    assert.match(ecpMarkerIcon('printer').html, /ecp-icon--sm/);
    assert.match(ecpMarkerIcon('printer', {color: '\" onload=\"alert(1)'}).html, /--marker-color:#d42225;/);
    assert.match(ecpMarkerIcon('printer', {color: '#abc'}).html, /--marker-color:#abc;/);
    assert.deepEqual(ecpMarkerIcon('printer', {size: NaN}).iconSize, [32, 32]);
  } finally { delete globalThis.L; }
});
