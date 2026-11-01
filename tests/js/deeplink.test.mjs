import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const require = createRequire(import.meta.url);
const { _resolveDeepLink } = require('../../app/static/js/deeplink.js');
const origin = 'https://app.example';

for (const [url, expected] of [
  ['https://einsatzcockpit.com/kontakte', '/kontakte'],
  ['https://www.einsatzcockpit.com/kontakte', '/kontakte'],
  ['https://einsatzcockpit.com/kontakte?q=a%20b#person', '/kontakte?q=a%20b#person'],
  ['https://evil.com/kontakte', null],
  ['https://einsatzcockpit.com.evil.com/kontakte', null],
  ['https://einsatzcockpit.com@evil.com/kontakte', null],
  ['https://einsatzcockpit.com:444/kontakte', null],
  ['http://einsatzcockpit.com/kontakte', null],
  ['javascript:alert(1)', null],
  ['einsatzcockpit://open?path=/kontakte', '/kontakte'],
  ['einsatzcockpit://widget/ignoriert?path=%2Fkontakte%3Fq%3Dtest%23person', '/kontakte?q=test#person'],
  ['einsatzcockpit://widget/kontakte', null],
  ['einsatzcockpit://open?path=//evil.com', null],
  ['einsatzcockpit://open?path=/\\evil', null],
  ['einsatzcockpit://open?path=%2F%5Cevil', null],
  ['einsatzcockpit://open?path=kontakte', null],
  ['einsatzcockpit://open?path=%2Fkontakte%0Aevil', null],
  ['https://einsatzcockpit.com//evil.com', null],
  ['ungueltig', null],
  [undefined, null],
]) {
  test(`Deep-Link ${JSON.stringify(url)} -> ${JSON.stringify(expected)}`, () => {
    assert.equal(_resolveDeepLink(url, origin), expected);
  });
}

const bridgeSource = readFileSync(new URL('../../app/static/js/native-bridge.js', import.meta.url), 'utf8');

function bridgeEnvironment(platform, native = true) {
  const listeners = [];
  const assigned = [];
  const window = {
    Capacitor: {
      isNativePlatform: () => native,
      getPlatform: () => platform,
      Plugins: { App: { addListener(name, callback) { listeners.push({ name, callback }); } } },
    },
    ECPDeepLink: { _resolveDeepLink },
    ECPullToRefresh: {},
    location: { origin, href: origin + '/aktuell', assign(target) { assigned.push(target); } },
  };
  const document = { readyState: 'loading', addEventListener() {} };
  vm.runInNewContext(bridgeSource, { window, document, URL });
  return { listeners, assigned };
}

test('iOS registriert genau einen Listener und navigiert nur zu erlaubten neuen Zielen', () => {
  const { listeners, assigned } = bridgeEnvironment('ios');
  assert.equal(listeners.length, 1);
  assert.equal(listeners[0].name, 'appUrlOpen');
  const open = listeners[0].callback;
  open({ url: 'https://einsatzcockpit.com/aktuell' });
  open({ url: 'https://evil.com/kontakte' });
  open({});
  assert.deepEqual(assigned, []);
  open({ url: 'https://www.einsatzcockpit.com/kontakte?q=test#person' });
  open({ url: 'einsatzcockpit://open?path=/objekte' });
  assert.deepEqual(assigned, [origin + '/kontakte?q=test#person', origin + '/objekte']);
});

test('Android und Browser registrieren keinen Deep-Link-Listener', () => {
  assert.equal(bridgeEnvironment('android').listeners.length, 0);
  assert.equal(bridgeEnvironment('ios', false).listeners.length, 0);
});
