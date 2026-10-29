/* Gemeinsamer Pull-to-Refresh fuer alle WebView-Seiten, ohne GPS/Push. */
(function () {
  'use strict';
  // ─── Pull-to-Refresh (APP-01) ───────────────────────────────────────────────
  // Die Android-App legt ein SwipeRefreshLayout um die WebView. Die Seite entscheidet, ob die
  // Geste gerade erlaubt ist (window.ECPtr = native Schnittstelle, nur in der App vorhanden) und
  // fuehrt die Aktualisierung ueber die vorhandenen Resync-Funktionen aus (Event "ec:resync"),
  // statt die Seite hart neu zu laden. Seiten ohne Resync-Handler werden neu geladen, sofern kein
  // Formular ungespeicherte Eingaben enthaelt.
  // Blockiert, wenn die Beruehrung innerhalb dieser Elemente beginnt (Karte, Editor, Drag-Zone)
  const _PTR_TARGET_SELECTOR = '.leaflet-container, [data-no-ptr], .cm-editor, .ql-editor, .sortable-chosen, .sortable-ghost';
  // Blockiert, sobald eines dieser Elemente sichtbar ist (Dialog, Overlay, laufendes Ziehen)
  const _PTR_OVERLAY_SELECTOR = 'dialog[open], .modal--open, .person-flyout-backdrop, .sortable-chosen';

  function _visible(el) {
    return !!(el && (el.offsetParent !== null || (el.getClientRects && el.getClientRects().length > 0)));
  }

  // Nur Eingaben markieren: Alpine/HTMX und vorbelegte Werte sind keine Bearbeitung.
  const _edited = new WeakSet();
  const _NO_DIRTY = '[data-no-dirty], input[type="search"], [role="search"], form[method="get" i], form[hx-get]:not([hx-post]):not([hx-put]):not([hx-delete])';
  function _ignored(field) {
    return field.disabled || ['hidden', 'submit', 'button', 'reset'].includes(field.type)
      || !!field.closest(_NO_DIRTY);
  }
  function _formDirty() {
    const fields = document.querySelectorAll('input, textarea, select, [contenteditable="true"]');
    for (const f of fields) {
      if (!_edited.has(f) || _ignored(f)) continue;
      if (f.isContentEditable) return true; // Auch das Loeschen vorhandenen Textes schuetzen.
      if (f.type === 'checkbox' || f.type === 'radio') {
        if (f.checked !== f.defaultChecked) return true;
      } else if (f.tagName === 'SELECT') {
        if (Array.from(f.options).some(o => o.selected !== o.defaultSelected)) return true;
      } else if (f.value !== f.defaultValue) return true;
    }
    return false;
  }

  function _blockedReason(target) {
    try {
      const overlays = document.querySelectorAll(_PTR_OVERLAY_SELECTOR);
      for (const overlay of overlays) {
        if (_visible(overlay)) return 'Bitte zuerst den offenen Dialog schliessen.';
      }
      let scroller = null;
      for (let el = target; el && el !== document.documentElement; el = el.parentElement) {
        if (el.matches && el.matches(_PTR_TARGET_SELECTOR)) return 'Aktualisierung in Karte, Editor oder Ziehbereich gesperrt.';
        if (!scroller && el.nodeType === 1 && el.scrollHeight > el.clientHeight
            && /^(auto|scroll|overlay)$/.test(window.getComputedStyle(el).overflowY)) scroller = el;
      }
      // Der naechste echte Scrollbereich entscheidet, auch in 100dvh-Layouts.
      const top = scroller ? scroller.scrollTop
        : Math.max(window.scrollY || 0, document.scrollingElement?.scrollTop || 0);
      if (top > 0) return 'Zum Aktualisieren zuerst ganz nach oben scrollen.';
      if (_formDirty()) return 'Ungespeicherte Eingaben – bitte zuerst speichern.';
      return '';
    } catch (_) {
      return 'Aktualisierung derzeit nicht moeglich.';
    }
  }

  function ptrBlocked(target) { return !!_blockedReason(target); }

  let _touchTarget = null;
  let _touchY = null;
  let _explained = false;
  function _pushPtrState() {
    if (window.ECPtr && typeof window.ECPtr.setBlocked === 'function') {
      window.ECPtr.setBlocked(ptrBlocked(_touchTarget));
    }
  }
  document.addEventListener('input', (event) => {
    const field = event.target.closest('input, textarea, select, [contenteditable="true"]');
    if (field && event.isTrusted && !_ignored(field)) _edited.add(field);
    _pushPtrState();
  }, true);
  document.addEventListener('touchstart', (event) => {
    const touch = event.touches[0];
    _touchTarget = touch ? document.elementFromPoint(touch.clientX, touch.clientY) || event.target : event.target;
    _touchY = touch ? touch.clientY : null;
    _explained = false;
    _pushPtrState();
  }, { passive: true, capture: true });
  document.addEventListener('touchmove', (event) => {
    _pushPtrState();
    const reason = _blockedReason(_touchTarget);
    if (!_explained && reason && _touchY !== null && event.touches[0]?.clientY - _touchY > 60) {
      _explained = true;
      if (window.ECPtr?.failed) window.ECPtr.failed(reason);
    }
  }, { passive: true, capture: true });
  document.addEventListener('touchend', () => { _touchY = null; }, { passive: true });
  document.addEventListener('touchcancel', () => { _touchY = null; }, { passive: true });
  document.addEventListener('scroll', _pushPtrState, { passive: true, capture: true });
  document.addEventListener('focusin', _pushPtrState, { passive: true });
  document.addEventListener('reset', () => setTimeout(_pushPtrState, 0), true);
  window.addEventListener('pageshow', _pushPtrState);
  window.addEventListener('pagehide', () => {
    if (window.ECPtr?.setBlocked) window.ECPtr.setBlocked(false);
  });
  _pushPtrState();

  /** Aktualisiert die Seite; meldet das Ende an die App (ECPtr.done/failed). */
  async function refresh() {
    const ptr = window.ECPtr;
    const done = () => { if (ptr && ptr.done) ptr.done(); };
    const failed = (msg) => { if (ptr && ptr.failed) ptr.failed(msg); };
    if (navigator.onLine === false) {
      failed('Keine Internetverbindung – Aktualisierung nicht möglich.');
      return false;
    }
    try {
      const reason = _blockedReason(_touchTarget);
      if (reason) { failed(reason); return false; }
      const detail = { source: 'pull', handled: false, promises: [] };
      window.dispatchEvent(new CustomEvent('ec:resync', { detail }));
      if (detail.handled) {
        let timer;
        try {
          await Promise.race([
            Promise.all(detail.promises),
            new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), 7000); }),
          ]);
        } finally { clearTimeout(timer); }
        done();
        return true;
      }
      if (_formDirty()) {
        failed('Ungespeicherte Eingaben – bitte zuerst speichern.');
        return false;
      }
      done();
      window.location.reload();
      return true;
    } catch (e) {
      failed('Aktualisierung fehlgeschlagen.');
      return false;
    }
  }

  window.ECPullToRefresh = { refresh, ptrBlocked };
  window.ELNative = Object.assign(window.ELNative || {}, window.ECPullToRefresh);
})();
