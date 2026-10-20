import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Only SHA-256 matches from npm tarballs are mapped. The separately minified
// leaflet.min.js and leaflet.min.css differ from leaflet@1.9.4 and stay untouched.
export const mapping = [
  { pkg: 'htmx.org', from: 'dist/htmx.min.js', to: 'app/static/js/htmx.min.js' },
  { pkg: 'alpinejs', from: 'dist/cdn.min.js', to: 'app/static/js/alpine.min.js' },
  { pkg: 'sortablejs', from: 'Sortable.min.js', to: 'app/static/js/sortable.min.js' },
  { pkg: '@geoman-io/leaflet-geoman-free', from: 'dist/leaflet-geoman.min.js', to: 'app/static/js/leaflet-geoman.min.js' },
  { pkg: 'quill', from: 'dist/quill.js', to: 'app/static/js/quill.min.js' },
  { pkg: 'chart.js', from: 'dist/chart.umd.min.js', to: 'app/static/js/chart.umd.min.js' },
  { pkg: 'konva', from: 'konva.min.js', to: 'app/static/js/konva.min.js' },
  { pkg: 'quill-cursors', from: 'dist/quill-cursors.js', to: 'app/static/js/quill-cursors.js' },
  { pkg: 'leaflet.markercluster', from: 'dist/leaflet.markercluster.js', to: 'app/static/js/leaflet.markercluster.js' },
  { pkg: 'lib0', from: 'array.js', to: 'app/static/js/collab/lib0/array.js' },
  { pkg: 'lib0', from: 'binary.js', to: 'app/static/js/collab/lib0/binary.js' },
  { pkg: 'lib0', from: 'broadcastchannel.js', to: 'app/static/js/collab/lib0/broadcastchannel.js' },
  { pkg: 'lib0', from: 'buffer.js', to: 'app/static/js/collab/lib0/buffer.js' },
  { pkg: 'lib0', from: 'conditions.js', to: 'app/static/js/collab/lib0/conditions.js' },
  { pkg: 'lib0', from: 'decoding.js', to: 'app/static/js/collab/lib0/decoding.js' },
  { pkg: 'lib0', from: 'dom.js', to: 'app/static/js/collab/lib0/dom.js' },
  { pkg: 'lib0', from: 'encoding.js', to: 'app/static/js/collab/lib0/encoding.js' },
  { pkg: 'lib0', from: 'environment.js', to: 'app/static/js/collab/lib0/environment.js' },
  { pkg: 'lib0', from: 'error.js', to: 'app/static/js/collab/lib0/error.js' },
  { pkg: 'lib0', from: 'eventloop.js', to: 'app/static/js/collab/lib0/eventloop.js' },
  { pkg: 'lib0', from: 'function.js', to: 'app/static/js/collab/lib0/function.js' },
  { pkg: 'lib0', from: 'iterator.js', to: 'app/static/js/collab/lib0/iterator.js' },
  { pkg: 'lib0', from: 'json.js', to: 'app/static/js/collab/lib0/json.js' },
  { pkg: 'lib0', from: 'logging.common.js', to: 'app/static/js/collab/lib0/logging.common.js' },
  { pkg: 'lib0', from: 'logging.js', to: 'app/static/js/collab/lib0/logging.js' },
  { pkg: 'lib0', from: 'map.js', to: 'app/static/js/collab/lib0/map.js' },
  { pkg: 'lib0', from: 'math.js', to: 'app/static/js/collab/lib0/math.js' },
  { pkg: 'lib0', from: 'metric.js', to: 'app/static/js/collab/lib0/metric.js' },
  { pkg: 'lib0', from: 'number.js', to: 'app/static/js/collab/lib0/number.js' },
  { pkg: 'lib0', from: 'object.js', to: 'app/static/js/collab/lib0/object.js' },
  { pkg: 'lib0', from: 'observable.js', to: 'app/static/js/collab/lib0/observable.js' },
  { pkg: 'lib0', from: 'pair.js', to: 'app/static/js/collab/lib0/pair.js' },
  { pkg: 'lib0', from: 'prng/Xoroshiro128plus.js', to: 'app/static/js/collab/lib0/prng/Xoroshiro128plus.js' },
  { pkg: 'lib0', from: 'prng/Xorshift32.js', to: 'app/static/js/collab/lib0/prng/Xorshift32.js' },
  { pkg: 'lib0', from: 'prng.js', to: 'app/static/js/collab/lib0/prng.js' },
  { pkg: 'lib0', from: 'promise.js', to: 'app/static/js/collab/lib0/promise.js' },
  { pkg: 'lib0', from: 'random.js', to: 'app/static/js/collab/lib0/random.js' },
  { pkg: 'lib0', from: 'schema.js', to: 'app/static/js/collab/lib0/schema.js' },
  { pkg: 'lib0', from: 'set.js', to: 'app/static/js/collab/lib0/set.js' },
  { pkg: 'lib0', from: 'storage.js', to: 'app/static/js/collab/lib0/storage.js' },
  { pkg: 'lib0', from: 'string.js', to: 'app/static/js/collab/lib0/string.js' },
  { pkg: 'lib0', from: 'symbol.js', to: 'app/static/js/collab/lib0/symbol.js' },
  { pkg: 'lib0', from: 'time.js', to: 'app/static/js/collab/lib0/time.js' },
  { pkg: 'lib0', from: 'trait/equality.js', to: 'app/static/js/collab/lib0/trait/equality.js' },
  { pkg: 'lib0', from: 'url.js', to: 'app/static/js/collab/lib0/url.js' },
  { pkg: 'lib0', from: 'webcrypto.js', to: 'app/static/js/collab/lib0/webcrypto.js' },
  { pkg: 'y-protocols', from: 'auth.js', to: 'app/static/js/collab/y-protocols/auth.js' },
  { pkg: 'y-protocols', from: 'awareness.js', to: 'app/static/js/collab/y-protocols/awareness.js' },
  { pkg: 'y-protocols', from: 'sync.js', to: 'app/static/js/collab/y-protocols/sync.js' },
  { pkg: 'y-quill', from: 'src/y-quill.js', to: 'app/static/js/collab/y-quill.js' },
  { pkg: 'y-websocket', from: 'src/y-websocket.js', to: 'app/static/js/collab/y-websocket.js' },
  { pkg: 'yjs', from: 'dist/yjs.mjs', to: 'app/static/js/collab/yjs.mjs' },
  { pkg: 'leaflet.markercluster', from: 'dist/MarkerCluster.Default.css', to: 'app/static/css/MarkerCluster.Default.css' },
  { pkg: '@geoman-io/leaflet-geoman-free', from: 'dist/leaflet-geoman.css', to: 'app/static/css/leaflet-geoman.min.css' },
  { pkg: 'quill', from: 'dist/quill.snow.css', to: 'app/static/css/quill.snow.css' },
  { pkg: 'quill-cursors', from: 'dist/quill-cursors.min.css', to: 'app/static/css/quill-cursors.min.css' },
];

const root = fileURLToPath(new URL('../', import.meta.url));

function generateIcons(data) {
  return `// Automatisch erzeugt von scripts/vendor-js.mjs – nicht von Hand ändern
(function (root) {
  'use strict';
  const icons = ${JSON.stringify(data, null, 2)};
  const sizes = {xs: 16, sm: 20, md: 24, lg: 32};
  const statuses = new Set(['rot', 'orange', 'gelb', 'gruen', 'blau', 'grau', 'schwarz', 'weiss']);
  const warned = new Set();
  const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&#34;', "'": '&#39;'}[c]));
  const classes = value => escape(String(value).trim().split(/\\s+/).filter(Boolean).join(' '));
  function ecpIcon(name, {size = 'md', cls = '', label = null} = {}) {
    if (!has(sizes, size)) throw new RangeError('Unbekannte Icon-Größe: ' + size);
    if (!has(icons, name)) {
      if (!warned.has(name)) {
        console.warn('Unbekanntes Icon: ' + name);
        warned.add(name);
      }
      name = 'help-square';
    }
    const css = classes('ecp-icon ecp-icon--' + size + ' ' + cls);
    const aria = label === null ? 'aria-hidden="true"' : 'role="img" aria-label="' + escape(label) + '"';
    const title = label === null ? '' : '<title>' + escape(label) + '</title>';
    return '<svg class="' + css + '" width="' + sizes[size] + '" height="' + sizes[size] + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' + aria + ' focusable="false">' + title + icons[name] + '</svg>';
  }
  function ecpStatusDot(status, label, cls = '') {
    if (!statuses.has(status)) throw new RangeError('Unbekannter Status: ' + status);
    return '<span class="' + classes('status-dot status-dot--' + status + ' ' + cls) + '" role="img" aria-label="' + escape(label) + '" title="' + escape(label) + '"></span>';
  }
  function ecpMarkerIcon(name, {color = '#d42225', size = 32, cls = ''} = {}) {
    if (!root.L) return null;
    if (!/^(?:#(?:[\\da-f]{3}|[\\da-f]{4}|[\\da-f]{6}|[\\da-f]{8})|var\\(--[a-zA-Z_][a-zA-Z0-9_-]*\\))$/i.test(color)) color = '#d42225';
    if (typeof size !== 'number' || !Number.isFinite(size) || size <= 0) size = 32;
    return root.L.divIcon({
      className: 'ecp-map-marker-wrap',
      html: '<div class="' + classes('ecp-map-marker ' + cls) + '" style="--marker-color:' + color + ';width:' + size + 'px;height:' + size + 'px">' + ecpIcon(name, {size: size >= 36 ? 'md' : 'sm'}) + '</div>',
      iconSize: [size, size], iconAnchor: [size / 2, size / 2], popupAnchor: [0, -size / 2]
    });
  }
  Object.assign(root, {ecpIcon, ecpStatusDot, ecpMarkerIcon});
  if (typeof module !== 'undefined') module.exports = {ecpIcon, ecpStatusDot, ecpMarkerIcon};
})(typeof window !== 'undefined' ? window : globalThis);
`;
}

function vendorIcons(check, failures) {
  const names = [...new Set(readFileSync(resolve(root, 'app/static/icons/tabler-auswahl.txt'), 'utf8')
    .split(/\r?\n/).map(line => line.trim()).filter(line => line && !line.startsWith('#')))].sort();
  const data = {};
  const outputs = [];
  for (const name of names) {
    try {
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) throw new Error('Ungültiger Name');
      const svg = readFileSync(resolve(root, 'node_modules/@tabler/icons/icons/outline', name + '.svg'));
      const inner = svg.toString('utf8').match(/<svg\b[^>]*>([\s\S]*?)<\/svg>/)[1]
        .replace(/<path stroke="none" d="M0 0h24v24H0z" fill="none"\s*\/>/g, '')
        .replace(/>\s+</g, '><').trim();
      if (!inner) throw new Error('Leeres SVG');
      data[name] = inner;
      outputs.push(['app/static/icons/tabler/' + name + '.svg', svg]);
    } catch (error) {
      failures.push(`Unbekanntes oder ungültiges Icon ${name}: ${error.message}`);
    }
  }
  outputs.push(['app/static/icons/tabler/LICENSE', readFileSync(resolve(root, 'node_modules/@tabler/icons/LICENSE'))]);
  outputs.push(['app/static/js/icons.js', Buffer.from(generateIcons(data))]);
  for (const [to, bytes] of outputs) {
    try {
      const target = resolve(root, to);
      if (check) {
        if (!bytes.equals(readFileSync(target))) failures.push(`${to} differs from icon selection`);
      } else {
        mkdirSync(dirname(target), {recursive: true});
        writeFileSync(target, bytes);
      }
    } catch (error) {
      failures.push(`${to}: ${error.message}`);
    }
  }
  if (check) {
    for (const file of readdirSync(resolve(root, 'app/static/icons/tabler'))) {
      if (file.endsWith('.svg') && !names.includes(file.slice(0, -4))) failures.push(`Überzähliges Icon: ${file}`);
    }
  }
}

function main() {
  const args = process.argv.slice(2);
  if (args.some(arg => arg !== '--check')) {
    console.error('Usage: node scripts/vendor-js.mjs [--check]');
    process.exitCode = 1;
    return;
  }
  const check = args.includes('--check');
  const failures = [];
  for (const { pkg, from, to } of mapping) {
    const source = resolve(root, 'node_modules', pkg, from);
    const target = resolve(root, to);
    try {
      if (check) {
        if (!readFileSync(source).equals(readFileSync(target))) {
          failures.push(`${to} differs from ${pkg}/${from}`);
        }
      } else {
        mkdirSync(dirname(target), { recursive: true });
        writeFileSync(target, readFileSync(source));
      }
    } catch (error) {
      failures.push(`${to} (${pkg}/${from}): ${error.message}`);
    }
  }
  try {
    vendorIcons(check, failures);
  } catch (error) {
    failures.push(`Icon-Vendoring: ${error.message}`);
  }
  if (failures.length) {
    console.error(failures.join('\n'));
    process.exitCode = 1;
  } else {
    console.log(check
      ? `vendor:check: ${mapping.length} files match npm packages.`
      : `vendor: ${mapping.length} files copied from npm packages.`);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
