/**
 * conn-status.js - Verbindungsstatus fuer alle Seiten (APP-02)
 *
 * Zeigt bei fehlender Verbindung zum Einsatzcockpit einen dezenten Hinweis mit dem Zeitpunkt der
 * letzten erfolgreichen Aktualisierung. Nach Wiederherstellung wird genau einmal ein Resync
 * ausgeloest (Event "ec:resync", das Board, Lage und Einheitenmodus bereits abonnieren);
 * es gibt keine eigenen Wiederholungsversuche oder Polling-Schleifen.
 *
 * Quellen: navigator.onLine / online / offline, HTMX-Sendefehler, globale WebSocket
 * ("ec:ws-state" aus app.js). Als "frisch" gelten erfolgreiche HTMX-Antworten, WebSocket-Open
 * und Board-Aktualisierungen ("board-last-update").
 */
(function () {
  'use strict';

  var WS_GRACE_MS = 8000;       // kurze Reconnects der WebSocket nicht anzeigen
  var STORAGE_KEY = 'ec_last_ok';

  var lastOk = Date.now();
  var offline = typeof navigator !== 'undefined' && navigator.onLine === false;
  var wsDownSince = null;
  var wsTimer = null;
  var banner = null;
  var wasDown = offline;

  try {
    var saved = Number(sessionStorage.getItem(STORAGE_KEY));
    if (saved > 0) lastOk = saved;
  } catch (e) { /* Browser ohne Speicher */ }

  function markFresh() {
    lastOk = Date.now();
    try { sessionStorage.setItem(STORAGE_KEY, String(lastOk)); } catch (e) { /* ignorieren */ }
    if (banner && !isDown()) hide();
  }

  function isDown() {
    return offline || (wsDownSince !== null && Date.now() - wsDownSince >= WS_GRACE_MS);
  }

  function clock(ts) {
    try {
      return new Date(ts).toLocaleTimeString('de-AT', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch (e) {
      return '';
    }
  }

  function ensureBanner() {
    if (banner) return banner;
    banner = document.createElement('div');
    banner.id = 'ec-conn-banner';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    banner.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);'
      + 'bottom:calc(12px + env(safe-area-inset-bottom,0px));z-index:9000;max-width:calc(100vw - 32px);'
      + 'padding:8px 14px;border-radius:999px;font-size:.85rem;line-height:1.3;'
      + 'background:var(--surface-high,#2b2b2b);color:var(--text,#fff);'
      + 'border:1px solid var(--color-danger,#d83030);box-shadow:0 2px 10px rgba(0,0,0,.35);';
    document.body.appendChild(banner);
    return banner;
  }

  function render() {
    if (!document.body) return;
    if (!isDown()) { hide(); return; }
    var el = ensureBanner();
    el.textContent = (offline ? 'Keine Verbindung zum Einsatzcockpit' : 'Verbindung unterbrochen')
      + ' · Stand ' + clock(lastOk);
  }

  function hide() {
    if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
    banner = null;
  }

  function checkRecovered() {
    var down = isDown();
    if (wasDown && !down) {
      wasDown = false;
      markFresh();
      hide();
      // genau ein Resync pro Wiederherstellung; "online" aus app.js loest ebenfalls einen aus,
      // daher hier nur, wenn die Wiederherstellung nicht vom Browser-Event stammt
      if (!recoveredByOnlineEvent && Date.now() - lastResyncAt > 5000) {
        window.dispatchEvent(new CustomEvent('ec:resync', { detail: { source: 'ws-reconnect', handled: false } }));
      }
      recoveredByOnlineEvent = false;
    } else if (!wasDown && down) {
      wasDown = true;
    }
    render();
  }

  var recoveredByOnlineEvent = false;
  var lastResyncAt = 0;
  // jedes Resync (auch von app.js bei "online" oder vom Board-WebSocket) zaehlt: nie doppelt nachladen
  window.addEventListener('ec:resync', function () { lastResyncAt = Date.now(); });

  window.addEventListener('offline', function () {
    offline = true;
    checkRecovered();
  });
  window.addEventListener('online', function () {
    offline = false;
    recoveredByOnlineEvent = true;
    checkRecovered();
  });
  window.addEventListener('ec:ws-state', function (e) {
    var open = !!(e.detail && e.detail.open);
    if (open) {
      wsDownSince = null;
      clearTimeout(wsTimer);
      markFresh();
      checkRecovered();
    } else if (wsDownSince === null) {
      wsDownSince = Date.now();
      clearTimeout(wsTimer);
      wsTimer = setTimeout(checkRecovered, WS_GRACE_MS + 50);
    }
  });
  document.addEventListener('board-last-update', markFresh);
  document.addEventListener('htmx:afterRequest', function (e) {
    if (e.detail && e.detail.successful) markFresh();
  });
  document.addEventListener('htmx:sendError', function () {
    offline = navigator.onLine === false ? true : offline;
    render();
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render);
  else render();

  window.ECConnStatus = {
    get lastOk() { return lastOk; },
    get isDown() { return isDown(); },
    markFresh: markFresh,
  };
})();
