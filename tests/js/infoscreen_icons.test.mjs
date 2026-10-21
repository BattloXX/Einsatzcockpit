import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

function infoscreen() {
  const template = readFileSync(new URL('../../app/templates/objekt/infoscreen_alarm.html', import.meta.url), 'utf8');
  let source = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
  source = source.replace('{{ token | tojson }}', '"test-token"');
  source = source.replace(/  aktualisieren\(\);\n  setInterval\(aktualisieren, 15000\);\n  wetterLaden\(\);\n  setInterval\(wetterLaden, 600000\);\n  verbindeWs\(\);/, '');
  source = source.replace(/\}\)\(\);\s*$/, 'globalThis.screen = {fahrzeugKarte, rsvpPanelHtml, wetterLaden};\n})();');
  const boxes = {'is-wetter': {style: {}}, 'gsl-wetter': {style: {}}};
  const context = vm.createContext({
    console, setInterval() {},
    document: {getElementById: id => boxes[id] || null},
    fetch: async () => ({json: async () => ({code: 95, temp: 17, wind: 12})}),
  });
  vm.runInContext(readFileSync(new URL('../../app/static/js/icons.js', import.meta.url), 'utf8'), context);
  vm.runInContext(source, context);
  return {screen: context.screen, boxes};
}

test('Alarm-Infoscreen rendert Fahrzeug- und RSVP-Icons und escaped Namen', () => {
  const {screen} = infoscreen();
  for (const extern of [false, true]) {
    const html = screen.fahrzeugKarte({extern, rufname: '<img src=x>', status_code: 'S2'});
    assert.match(html, /<svg class="ecp-icon ecp-icon--lg"/);
    assert.match(html, /&lt;img src=x&gt;/);
    assert.doesNotMatch(html, /<img/);
  }
  const html = screen.rsvpPanelHtml({namen: [
    {name: '<script>', status: 'zugesagt'}, {name: 'Person B', status: 'abgesagt'},
  ]});
  assert.equal((html.match(/<svg /g) || []).length, 3);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, />circle-(?:check|x)</);
});

test('Alarm-Infoscreen rendert WMO-Wettercode als SVG in beiden Wetteranzeigen', async () => {
  const {screen, boxes} = infoscreen();
  screen.wetterLaden();
  await new Promise(resolve => setImmediate(resolve));
  for (const box of Object.values(boxes)) {
    assert.match(box.innerHTML, /<svg class="ecp-icon ecp-icon--lg"/);
    assert.match(box.innerHTML, /17°C/);
    assert.doesNotMatch(box.innerHTML, />cloud-storm</);
  }
});
