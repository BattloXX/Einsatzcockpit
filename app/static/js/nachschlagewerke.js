/* Nachschlagewerke - Gefahrgut-Suche (offlinefaehig ueber index.json).
 *
 * Laedt den kompletten Datensatz einmal (vom Service Worker gecacht) und sucht
 * clientseitig - funktioniert damit auch ohne Netz. Detail wird inline
 * aufgeklappt (kein Seitenwechsel noetig, offline nutzbar). Nur gerade ASCII-Quotes.
 */
(function () {
  var input = document.getElementById('gg-q');
  var out = document.getElementById('gg-treffer');
  if (!input || !out) return;

  var ENTRIES = null;
  var LADEFEHLER = false;

  function esc(s) {
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function normUn(s) {
    var z = (s == null ? '' : String(s)).replace(/\D/g, '');
    return z.replace(/^0+/, '') || z;
  }

  function un4(s) {
    var z = (s == null ? '' : String(s)).replace(/\D/g, '');
    if (!z) return '';
    return z.length < 4 ? ('0000' + z).slice(-4) : z;
  }

  function normName(s) {
    return (s == null ? '' : String(s)).toLowerCase()
      .replace(/ä/g, 'ae').replace(/ö/g, 'oe')
      .replace(/ü/g, 'ue').replace(/ß/g, 'ss');
  }

  function suche(q) {
    q = (q || '').trim();
    if (!q || !ENTRIES) return [];
    var res;
    if (/^(un)?[\s-]*\d+$/i.test(q)) {
      var p = normUn(q);
      res = ENTRIES.filter(function (e) { return normUn(e.un_nummer).indexOf(p) === 0; });
      res.sort(function (a, b) {
        var x = normUn(a.un_nummer), y = normUn(b.un_nummer);
        return (x.length - y.length) || (x < y ? -1 : x > y ? 1 : 0);
      });
    } else {
      var nq = normName(q);
      res = ENTRIES.filter(function (e) { return normName(e.stoffname).indexOf(nq) >= 0; });
      res.sort(function (a, b) {
        var x = normName(a.stoffname), y = normName(b.stoffname);
        return x < y ? -1 : x > y ? 1 : 0;
      });
    }
    return res.slice(0, 50);
  }

  function badge(text, mod, title) {
    if (!text) return '';
    var t = title ? ' title="' + esc(title) + '"' : '';
    return '<span class="badge-pill badge-pill--' + mod + '"' + t + '>' + esc(text) + '</span>';
  }

  function detailHtml(e) {
    var rows = [
      ['Stoffname', e.stoffname],
      ['Gefahrklasse', e.klasse],
      ['Klassifizierungscode', e.klassifizierungscode],
      ['Gefahrnummer (Kemler)', e.gefahrnummer],
      ['Verpackungsgruppe', e.verpackungsgruppe]
    ];
    var dl = '<dl style="display:grid;grid-template-columns:auto 1fr;gap:6px 16px;margin:10px 0 0;">';
    rows.forEach(function (r) {
      dl += '<dt class="text-muted">' + esc(r[0]) + '</dt><dd style="margin:0;">' + esc(r[1] || '—') + '</dd>';
    });
    dl += '</dl>';
    var links = '';
    if (e.links && e.links.length) {
      links = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;">';
      e.links.forEach(function (l) {
        links += '<a href="' + esc(l.url) + '" target="_blank" rel="noopener" class="btn btn--secondary btn--xs">🔗 ' + esc(l.label) + '</a>';
      });
      links += '</div>';
    }
    return '<div style="padding:0 14px 14px;">' + dl + links + '</div>';
  }

  function cardHtml(e) {
    var u = un4(e.un_nummer);
    return '<details class="card" style="padding:0;">' +
      '<summary style="padding:12px 14px;cursor:pointer;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">' +
      badge('UN ' + u, 'red') +
      '<strong style="flex:1;min-width:160px;">' + esc(e.stoffname || '—') + '</strong>' +
      badge(e.gefahrnummer ? 'GN ' + e.gefahrnummer : '', 'amber', 'Gefahrnummer (Kemler)') +
      badge(e.klasse ? 'Kl. ' + e.klasse : '', 'gray') +
      badge(e.verpackungsgruppe ? 'VG ' + e.verpackungsgruppe : '', 'gray') +
      '</summary>' + detailHtml(e) + '</details>';
  }

  function render() {
    var q = input.value;
    if (!q.trim()) {
      out.innerHTML = '<div class="card" style="padding:16px;"><p class="text-muted" style="margin:0;">' +
        'Suchbegriff eingeben – UN-Nummer (z. B. 1203) oder Stoffname (z. B. Benzin).</p></div>';
      return;
    }
    if (LADEFEHLER || !ENTRIES) {
      out.innerHTML = '<div class="card" style="padding:16px;"><p class="text-muted" style="margin:0;">' +
        'Datensatz konnte nicht geladen werden.</p></div>';
      return;
    }
    var treffer = suche(q);
    if (!treffer.length) {
      out.innerHTML = '<div class="card" style="padding:16px;text-align:center;"><p class="text-muted" style="margin:0;">' +
        'Kein Treffer für „' + esc(q) + '“. UN-Nummer oder Stoffname prüfen.</p></div>';
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

  fetch('/nachschlagewerke/gefahrgut/index.json', { credentials: 'same-origin' })
    .then(function (r) { return r.json(); })
    .then(function (data) { ENTRIES = data.eintraege || []; render(); })
    .catch(function () { LADEFEHLER = true; render(); });
})();
