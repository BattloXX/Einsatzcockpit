/* Nachschlagewerke - Rettungskarten-Katalog (Euro NCAP / CTIF Euro Rescue).
 *
 * Laedt den kompletten Modellkatalog einmal (vom Service Worker gecacht) und sucht
 * clientseitig nach Hersteller/Modell - funktioniert damit auch ohne Netz. Beim
 * Oeffnen wird das PDF serverseitig on-demand geladen und danach offline
 * vorgehalten. Nur gerade ASCII-Quotes.
 */
(function () {
  var input = document.getElementById('rk-q');
  var out = document.getElementById('rk-katalog-treffer');
  if (!input || !out) return;

  var ENTRIES = null;
  var LADEFEHLER = false;

  function esc(s) {
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function norm(s) {
    return (s == null ? '' : String(s)).toLowerCase()
      .replace(/ä/g, 'ae').replace(/ö/g, 'oe')
      .replace(/ü/g, 'ue').replace(/ß/g, 'ss')
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '');  // Citroen, Skoda, ...
  }

  // Gaengige Marken-/Antriebs-Kuerzel, damit "VW Golf" den Katalog-"Volkswagen" findet.
  var ALIASES = {
    'volkswagen': 'vw',
    'mercedes-benz': 'mercedes merc mb',
    'land rover': 'landrover',
    'alfa romeo': 'alfa',
    'ds': 'ds automobiles',
    'electric': 'elektro elektrisch e-auto ev',
    'gasoline/diesel': 'benzin diesel'
  };

  function heuhaufen(e) {
    var h = norm([e.hersteller, e.modell, e.karosserie, e.antrieb].join(' '));
    var mArt = ALIASES[norm(e.hersteller)];
    if (mArt) h += ' ' + mArt;
    var aArt = ALIASES[norm(e.antrieb)];
    if (aArt) h += ' ' + aArt;
    return h;
  }

  function suche(q) {
    q = (q || '').trim();
    if (!q || !ENTRIES) return [];
    var begriffe = norm(q).split(/\s+/).filter(Boolean);
    var res = ENTRIES.filter(function (e) {
      var h = heuhaufen(e);
      return begriffe.every(function (b) { return h.indexOf(b) >= 0; });
    });
    res.sort(function (a, b) {
      var x = norm(a.hersteller + ' ' + a.modell), y = norm(b.hersteller + ' ' + b.modell);
      if (x !== y) return x < y ? -1 : 1;
      return (a.baujahr_von || 0) - (b.baujahr_von || 0);
    });
    return res.slice(0, 50);
  }

  function badge(text, mod, title) {
    if (!text) return '';
    var t = title ? ' title="' + esc(title) + '"' : '';
    return '<span class="badge-pill badge-pill--' + mod + '"' + t + '>' + esc(text) + '</span>';
  }

  function jahre(e) {
    if (!e.baujahr_von) return '';
    var s = String(e.baujahr_von);
    if (e.baujahr_bis && e.baujahr_bis !== e.baujahr_von) s += '-' + e.baujahr_bis;
    else s += '–';  // ab-Jahr, offenes Ende
    return s;
  }

  function cardHtml(e) {
    var titel = esc(e.hersteller) + ' ' + esc(e.modell);
    var meta = [];
    if (e.karosserie) meta.push(badge(e.karosserie, 'gray'));
    if (e.antrieb) meta.push(badge(e.antrieb, 'gray'));
    if (e.tueren) meta.push(badge(e.tueren + '-tuerig', 'gray'));
    var jr = jahre(e);
    var jahrBadge = jr ? badge(jr, 'amber', 'Baujahr') : '';
    var aktion;
    if (e.hat_pdf) {
      var lang = e.pdf_sprache && e.pdf_sprache !== 'DE'
        ? ' <span class="text-muted" style="font-size:.85em;">(' + esc(e.pdf_sprache) + ')</span>' : '';
      aktion = '<a href="/nachschlagewerke/rettungskarten/katalog/' + e.id + '/oeffnen" ' +
        'target="_blank" rel="noopener" class="btn btn--primary btn--xs">📄 Öffnen' + lang + '</a>';
    } else {
      aktion = '<span class="text-muted" style="font-size:.9em;">kein Rettungsblatt</span>';
    }
    return '<div class="card" style="padding:12px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">' +
      '<strong style="flex:1;min-width:160px;">' + titel + '</strong>' +
      jahrBadge + meta.join('') + aktion + '</div>';
  }

  function meldung(text) {
    out.innerHTML = '<div class="card" style="padding:16px;text-align:center;">' +
      '<p class="text-muted" style="margin:0;">' + text + '</p></div>';
  }

  function render() {
    var q = input.value;
    if (!q.trim()) {
      var n = ENTRIES ? ENTRIES.length : (input.getAttribute('data-katalog-anzahl') || 0);
      meldung(n
        ? 'Hersteller und/oder Modell eingeben – ' + n + ' Fahrzeuge im Katalog.'
        : 'Der Katalog wird noch synchronisiert. Nutze solange den Direktabruf unten.');
      return;
    }
    if (LADEFEHLER || !ENTRIES) {
      meldung('Katalog konnte nicht geladen werden. Direktabruf unten nutzen.');
      return;
    }
    var treffer = suche(q);
    if (!treffer.length) {
      meldung('Kein Treffer für „' + esc(q) + '“. Anders schreiben oder Direktabruf unten nutzen.');
      return;
    }
    out.innerHTML = '<div style="display:flex;flex-direction:column;gap:8px;">' +
      treffer.map(cardHtml).join('') + '</div>';
  }

  var timer = null;
  input.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(render, 200);
  });

  render();
  fetch('/nachschlagewerke/rettungskarten/katalog.json', { credentials: 'same-origin' })
    .then(function (r) { return r.json(); })
    .then(function (data) { ENTRIES = data.eintraege || []; render(); })
    .catch(function () { LADEFEHLER = true; render(); });
})();
