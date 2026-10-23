import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

function seitengruppe(anzahlen) {
  const gesamt = anzahlen.reduce((summe, anzahl) => summe + anzahl, 0);
  let beginn = 1;
  const seiten = anzahlen.map((anzahl, index) => {
    const label = `${beginn}–${beginn + anzahl - 1} / ${gesamt}`;
    beginn += anzahl;
    return {hidden: index > 0, dataset: {label}};
  });
  const anzeige = {textContent: seiten[0].dataset.label};
  return {
    seiten, anzeige,
    querySelectorAll: () => seiten,
    querySelector: () => anzeige,
  };
}

function monitor(gruppen) {
  const template = readFileSync(new URL('../../app/templates/weather/infoscreen.html', import.meta.url), 'utf8');
  const source = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
  const timer = [];
  const elemente = Object.fromEntries(['hdr-time', 'hdr-date', 'footer-refresh', 'footer-bar']
    .map(id => [id, {textContent: '', style: {}}]));
  let neuladungen = 0;
  vm.runInNewContext(source, {
    document: {
      querySelectorAll: () => gruppen,
      getElementById: id => elemente[id],
    },
    setInterval: (callback, interval) => timer.push({callback, interval}),
    location: {reload: () => neuladungen++},
  });
  let sekunden = 0;
  return {
    elemente, timer,
    neuladungen: () => neuladungen,
    weiter(anzahl) {
      for (let i = 0; i < anzahl; i++) {
        sekunden++;
        for (const eintrag of timer) {
          if (sekunden * 1000 % eintrag.interval === 0) eintrag.callback();
        }
      }
    },
  };
}

test('Wetter-Monitor zeigt nacheinander alle Warnungen und Pegel ohne Neuladen', () => {
  const warnungen = seitengruppe([4, 4, 1]);
  const pegel = seitengruppe([4, 1]);
  const screen = monitor([warnungen, pegel]);
  for (const [warnseite, pegelseite] of [[1, 1], [2, 0], [0, 1]]) {
    screen.weiter(15);
    for (const [gruppe, aktiv] of [[warnungen, warnseite], [pegel, pegelseite]]) {
      assert.deepEqual(gruppe.seiten.map(seite => seite.hidden), gruppe.seiten.map((_, index) => index !== aktiv));
      assert.equal(gruppe.anzeige.textContent, gruppe.seiten[aktiv].dataset.label);
    }
  }
  assert.equal(screen.neuladungen(), 0);
  assert.equal(screen.elemente['footer-refresh'].textContent, 'Nächste Aktualisierung in 4:15');
});

test('Einzelne Seite bleibt stehen, Uhr und Fünf-Minuten-Aktualisierung laufen weiter', () => {
  const gruppe = seitengruppe([3]);
  const screen = monitor([gruppe]);
  assert.equal(screen.timer.filter(timer => timer.interval === 15000).length, 0);
  assert.match(screen.elemente['hdr-time'].textContent, /^\d{2}:\d{2}:\d{2}$/);
  screen.weiter(299);
  assert.equal(screen.neuladungen(), 0);
  assert.equal(screen.elemente['footer-refresh'].textContent, 'Nächste Aktualisierung in 0:01');
  assert.equal(gruppe.seiten[0].hidden, false);
  screen.weiter(1);
  assert.equal(screen.neuladungen(), 1);
});
