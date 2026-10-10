/* Zugang-Tab der Ressourcenkarte: SMS senden, Nachricht/Link kopieren.
 * Der Klartext-Link lebt nur im Arbeitsspeicher dieser Seite (max. 5 Minuten),
 * nie im DOM, in localStorage oder in Attributen. */
(function () {
  var FRISCH_MS = 5 * 60 * 1000;
  var frisch = null; // {einheitId, link, text, generation, at}

  function root() { return document.querySelector('[data-zugang-root]'); }
  function frischFuer(einheitId) {
    if (frisch && frisch.einheitId === einheitId && Date.now() - frisch.at < FRISCH_MS) return frisch;
    frisch = null;
    return null;
  }
  function toast(zone, text, fehler) {
    var old = zone.querySelector('[data-zugang-toast]');
    if (old) old.remove();
    var el = document.createElement('div');
    el.className = 'ressource-zugang__toast' + (fehler ? ' ressource-zugang__toast--fehler' : '');
    el.setAttribute('data-zugang-toast', '1');
    el.textContent = text;
    zone.appendChild(el);
    setTimeout(function () { el.remove(); }, 6000);
  }
  function bestaetigen(zone, hatFrischen) {
    if (hatFrischen || zone.dataset.sitzungAktiv !== '1') return true;
    return window.confirm('Der Gruppenkommandant ist gerade angemeldet. Ein neuer Link beendet seine Sitzung. Fortfahren?');
  }
  function holeLink(zone, modus, bestaetigt) {
    var einheitId = zone.dataset.einheitId;
    var f = frischFuer(einheitId);
    var form = new FormData();
    form.append('modus', modus);
    if (f) form.append('bestehender_link', f.link);
    if (bestaetigt) form.append('bestaetigt', '1');
    return fetch(zone.dataset.basis + '/zugang/link', { method: 'POST', body: form, credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 409) throw new Error('Sitzung aktiv - bitte Ansicht aktualisieren und erneut versuchen.');
        if (!r.ok) throw new Error('Link konnte nicht erstellt werden (' + r.status + ').');
        return r.json();
      })
      .then(function (d) {
        frisch = { einheitId: einheitId, link: d.link, text: d.text, generation: d.generation, at: Date.now() };
        return d;
      });
  }
  function fallbackDialog(text) {
    var dlg = document.createElement('dialog');
    dlg.className = 'modal';
    dlg.innerHTML = '<div class="modal__content" style="min-width:min(520px,95vw);">' +
      '<p>Automatisches Kopieren nicht möglich. Text markieren und kopieren (Strg+C bzw. lange drücken):</p>' +
      '<textarea readonly rows="8" style="width:100%;font-size:16px;"></textarea>' +
      '<p><button type="button" class="btn btn--primary" data-close>Schließen</button></p></div>';
    var area = dlg.querySelector('textarea');
    area.value = text;
    function schliessen() { area.value = ''; dlg.close(); dlg.remove(); }
    dlg.querySelector('[data-close]').addEventListener('click', schliessen);
    dlg.addEventListener('cancel', function () { area.value = ''; });
    document.body.appendChild(dlg);
    dlg.showModal();
    area.focus();
    area.select();
  }
  function kopieren(zone, modus) {
    var holder = { text: null, d: null };
    if (!bestaetigen(zone, !!frischFuer(zone.dataset.einheitId))) return;
    var bestaetigt = zone.dataset.sitzungAktiv === '1';
    var p = holeLink(zone, modus, bestaetigt).then(function (d) {
      holder.d = d;
      holder.text = d.text;
      return d.text;
    });
    var schreiben;
    if (navigator.clipboard && window.ClipboardItem) {
      schreiben = navigator.clipboard.write([new ClipboardItem({
        'text/plain': p.then(function (t) { return new Blob([t], { type: 'text/plain' }); })
      })]);
    } else if (navigator.clipboard && navigator.clipboard.writeText) {
      schreiben = p.then(function (t) { return navigator.clipboard.writeText(t); });
    } else {
      schreiben = p.then(function () { throw new Error('clipboard-nicht-verfuegbar'); });
    }
    schreiben.then(function () {
      toast(zone, (modus === 'link' ? 'Link' : 'Nachricht') + ' kopiert · ersetzt den vorherigen Link', false);
    }).catch(function (err) {
      p.then(function (t) { fallbackDialog(t); }).catch(function () {
        toast(zone, (err && err.message) || 'Kopieren fehlgeschlagen.', true);
      });
    });
  }
  function senden(zone) {
    var f = frischFuer(zone.dataset.einheitId);
    if (!bestaetigen(zone, !!f)) return;
    var form = new FormData();
    if (f) form.append('bestehender_link', f.link);
    if (zone.dataset.sitzungAktiv === '1' || f) form.append('bestaetigt', '1');
    var knoepfe = zone.querySelectorAll('button');
    knoepfe.forEach(function (b) { b.disabled = true; });
    fetch(zone.dataset.basis + '/zugang/senden', { method: 'POST', body: form, credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 409) throw new Error('Sitzung aktiv - bitte Ansicht aktualisieren und erneut versuchen.');
        if (!r.ok) return r.text().then(function (t) { throw new Error(t || 'SMS konnte nicht gesendet werden.'); });
        return r.text();
      })
      .then(function (html) {
        var body = document.getElementById('ressourceKarteBody');
        body.innerHTML = html;
        if (window.htmx) window.htmx.process(body);
      })
      .catch(function (err) {
        knoepfe.forEach(function (b) { b.disabled = false; });
        toast(zone, err.message, true);
      });
  }
  document.addEventListener('click', function (event) {
    var knopf = event.target.closest('[data-zugang-aktion]');
    if (!knopf || knopf.disabled) return;
    var zone = knopf.closest('[data-zugang-root]');
    if (!zone) return;
    var aktion = knopf.dataset.zugangAktion;
    if (aktion === 'sms') senden(zone);
    else if (aktion === 'nachricht' || aktion === 'link') kopieren(zone, aktion);
  });
  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-karte-close]')) frisch = null;
  });
}());
