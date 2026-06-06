/**
 * Adress-Autocomplete für Einsatzleiter (Photon/Historie über Backend).
 *
 * Nutzung:
 *   initAddressAutocomplete({
 *     inputId:    'newIncStreet',
 *     field:      'street' | 'house' | 'city',
 *     getCity:    () => document.getElementById('newIncCity').value,  // optional
 *     getStreet:  () => document.getElementById('newIncStreet').value, // optional
 *     onSelect:   (item) => void,   // item = {label,street,house_number,city,lat,lng,source}
 *     minChars:   1,      // optional, default 1
 *     debounceMs: 250,    // optional, default 250
 *   });
 *
 * Idempotent: setzt data-ac-bound auf dem Input-Element.
 * Freitext bleibt immer möglich – das Dropdown ist rein additiv.
 */
(function (global) {
  'use strict';

  function initAddressAutocomplete(opts) {
    var inputId    = opts.inputId;
    var field      = opts.field;
    var getCity    = opts.getCity    || function () { return ''; };
    var getStreet  = opts.getStreet  || function () { return ''; };
    var onSelect   = opts.onSelect   || function () {};
    var minChars   = opts.minChars   !== undefined ? opts.minChars   : 1;
    var debounceMs = opts.debounceMs !== undefined ? opts.debounceMs : 250;

    var input = document.getElementById(inputId);
    if (!input || input.dataset.acBound) return;
    input.dataset.acBound = '1';
    input.setAttribute('autocomplete', 'off');

    var ul = null;      // Dropdown-Element (wird lazy erzeugt)
    var timer = null;
    var activeIdx = -1;
    var lastItems = [];

    // ── Dropdown erzeugen (lazy, an body gebunden, position:fixed) ──────────
    function ensureDropdown() {
      if (ul) return;
      ul = document.createElement('ul');
      ul.className = 'addr-suggest';
      ul.style.cssText = [
        'display:none',
        'position:fixed',
        'z-index:10000',
        'list-style:none',
        'margin:0',
        'padding:4px 0',
        'background:var(--surface-mid,#171f33)',
        'border:1px solid var(--border,#2e3347)',
        'border-radius:6px',
        'box-shadow:0 8px 24px rgba(0,0,0,.55)',
        'max-height:240px',
        'overflow-y:auto',
        'font-size:.88rem',
        'font-family:var(--font-sans,sans-serif)',
      ].join(';');
      // Im <dialog>-Element anhängen, damit das Dropdown im Browser-Top-Layer
      // gerendert wird und nicht hinter dem Dialog-Backdrop verschwindet.
      var dlg = input.closest('dialog');
      (dlg || document.body).appendChild(ul);
    }

    function reposition() {
      if (!ul || ul.style.display === 'none') return;
      var rect = input.getBoundingClientRect();
      ul.style.top   = (rect.bottom + 2) + 'px';
      ul.style.left  = rect.left + 'px';
      ul.style.width = rect.width + 'px';
    }

    // ── Dropdown befüllen und anzeigen ──────────────────────────────────────
    function showDropdown(items) {
      ensureDropdown();
      lastItems = items;
      activeIdx = -1;
      ul.innerHTML = '';

      if (!items || !items.length) {
        ul.style.display = 'none';
        return;
      }

      items.forEach(function (item, i) {
        var li = document.createElement('li');
        li.style.cssText = [
          'padding:7px 12px',
          'cursor:pointer',
          'display:flex',
          'align-items:center',
          'gap:6px',
          'white-space:nowrap',
          'overflow:hidden',
          'text-overflow:ellipsis',
          'color:var(--on-surface,#dae2fd)',
        ].join(';');

        var span = document.createElement('span');
        span.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;';
        span.textContent = item.label;
        li.appendChild(span);

        if (item.source === 'history') {
          var badge = document.createElement('span');
          badge.style.cssText = 'font-size:.68rem;color:var(--text-muted,#7a87a8);flex-shrink:0;';
          badge.title = 'Aus bisherigen Einsätzen';
          badge.textContent = '↺';
          li.appendChild(badge);
        }

        li.addEventListener('mousedown', function (e) {
          e.preventDefault(); // blur verhindern
          selectItem(i);
        });
        li.addEventListener('mouseover', function () {
          setActive(i);
        });
        ul.appendChild(li);
      });

      reposition();
      ul.style.display = 'block';
    }

    function hideDropdown() {
      if (ul) ul.style.display = 'none';
      activeIdx = -1;
    }

    function setActive(idx) {
      if (!ul) return;
      var lis = ul.querySelectorAll('li');
      lis.forEach(function (li, i) {
        li.style.background = i === idx
          ? 'var(--surface-high,#222a3d)'
          : '';
      });
      activeIdx = idx;
    }

    // ── Auswahl übernehmen ──────────────────────────────────────────────────
    function selectItem(idx) {
      var item = lastItems[idx];
      if (!item) return;

      if (field === 'street') {
        input.value = item.street || item.label;
      } else if (field === 'house') {
        input.value = item.house_number || item.label;
      } else if (field === 'city') {
        input.value = item.city || item.label;
      }

      hideDropdown();
      onSelect(item);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    // ── Tastatur-Navigation ─────────────────────────────────────────────────
    input.addEventListener('keydown', function (e) {
      if (!ul || ul.style.display === 'none') return;
      var lis = ul.querySelectorAll('li');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActive(Math.min(activeIdx + 1, lis.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive(Math.max(activeIdx - 1, 0));
      } else if (e.key === 'Enter') {
        if (activeIdx >= 0) {
          e.preventDefault();
          selectItem(activeIdx);
        }
      } else if (e.key === 'Escape') {
        hideDropdown();
      }
    });

    // Blur → schließen (Verzögerung, damit mousedown noch feuern kann)
    input.addEventListener('blur', function () {
      setTimeout(hideDropdown, 160);
    });

    // Scroll/Resize → Dropdown repositionieren
    window.addEventListener('scroll', reposition, { passive: true });
    window.addEventListener('resize', reposition, { passive: true });

    // ── Tippen → Debounce → Fetch ───────────────────────────────────────────
    input.addEventListener('input', function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < minChars) { hideDropdown(); return; }
      timer = setTimeout(function () {
        var city   = (getCity()   || '').trim();
        var street = (getStreet() || '').trim();
        var params = new URLSearchParams({ q: q, field: field });
        if (city)   params.set('city', city);
        if (street) params.set('street', street);
        fetch('/adresse/vorschlaege?' + params.toString(), {
          credentials: 'same-origin',
        })
          .then(function (r) { return r.ok ? r.json() : { items: [] }; })
          .then(function (d) { showDropdown(d.items || []); })
          .catch(function () { hideDropdown(); });
      }, debounceMs);
    });
  }

  global.initAddressAutocomplete = initAddressAutocomplete;
})(window);
