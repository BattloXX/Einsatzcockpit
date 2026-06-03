/* CSRF-Token-Helfer (Phase 7).
 *
 * Liest den CSRF-Token aus dem `fwwo_csrf`-Cookie und setzt ihn bei jedem
 * HTMX-POST/PUT/PATCH/DELETE-Request als `X-CSRF-Token`-Header.
 * Server vergleicht Cookie ≡ Header (Double-Submit-Pattern).
 */
(function () {
  'use strict';

  function readCookie(name) {
    const cookies = (document.cookie || '').split(/;\s*/);
    for (const c of cookies) {
      const i = c.indexOf('=');
      if (i === -1) continue;
      const k = c.slice(0, i).trim();
      if (k === name) return decodeURIComponent(c.slice(i + 1));
    }
    return null;
  }

  function unsafeMethod(m) {
    if (!m) return false;
    const up = m.toUpperCase();
    return up === 'POST' || up === 'PUT' || up === 'PATCH' || up === 'DELETE';
  }

  // HTMX: vor jedem Request den Header setzen
  if (typeof document !== 'undefined') {
    document.body.addEventListener('htmx:configRequest', (evt) => {
      if (!unsafeMethod(evt.detail.verb)) return;
      const token = readCookie('fwwo_csrf');
      if (token) evt.detail.headers['X-CSRF-Token'] = token;
    });
  }

  // Globale fetch()-Aufrufe (z.B. moveVehicle in app.js): patch hinzufügen
  const _origFetch = window.fetch;
  window.fetch = function (input, init = {}) {
    const method = (init.method || (typeof input === 'object' && input.method) || 'GET');
    if (unsafeMethod(method)) {
      const token = readCookie('fwwo_csrf');
      if (token) {
        const headers = new Headers(init.headers || {});
        headers.set('X-CSRF-Token', token);
        init.headers = headers;
      }
    }
    return _origFetch.call(this, input, init);
  };

  // Plain HTML-Forms bekommen ein verstecktes Feld injiziert
  function injectFormToken() {
    const token = readCookie('fwwo_csrf');
    if (!token) return;
    document.querySelectorAll('form[method="post" i]:not([data-csrf-skip])').forEach(form => {
      if (form.querySelector('input[name="_csrf"]')) return;
      const inp = document.createElement('input');
      inp.type = 'hidden';
      inp.name = '_csrf';
      inp.value = token;
      form.appendChild(inp);
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectFormToken);
  } else {
    injectFormToken();
  }
  // Nach HTMX-Swaps neue Forms erneut versorgen
  document.body.addEventListener('htmx:afterSwap', injectFormToken);

  // Alpine.js und andere Frameworks fügen Forms dynamisch ein — MutationObserver fängt das ab
  let _pending = false;
  const _mo = new MutationObserver(() => {
    if (_pending) return;
    _pending = true;
    requestAnimationFrame(() => { _pending = false; injectFormToken(); });
  });
  function _startObserver() {
    _mo.observe(document.body, { childList: true, subtree: true });
  }
  if (document.body) { _startObserver(); }
  else { document.addEventListener('DOMContentLoaded', _startObserver); }
})();
