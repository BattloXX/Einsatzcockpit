/* Lage-Phasen-Board – SortableJS Drag & Drop + WebSocket Live-Reload */
(function () {
  'use strict';

  function getLageId() {
    const el = document.getElementById('lage-board');
    return el ? el.dataset.lageId : null;
  }

  function getCsrf() {
    return document.cookie.match(/(?:^|;\s*)fwwo_csrf=([^;]+)/)?.[1] || '';
  }

  function postPhase(lageId, siteId, phase, sortIndex) {
    const body = new URLSearchParams({ phase, sort_index: sortIndex, _csrf: getCsrf() });
    return fetch(`/lage/${lageId}/stellen/${siteId}/phase`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
      credentials: 'same-origin',
    }).catch(err => console.warn('[lage_board] postPhase error:', err));
  }

  let _initTimer = null;
  function scheduleInit() {
    if (_initTimer) clearTimeout(_initTimer);
    _initTimer = setTimeout(() => { _initTimer = null; initBoard(); }, 150);
  }

  function initBoard() {
    const lageId = getLageId();
    if (!lageId || typeof Sortable === 'undefined') return;

    document.querySelectorAll('.phase-col__body').forEach(zone => {
      if (zone._lageSortable) {
        try { zone._lageSortable.destroy(); } catch (e) { /* noop */ }
        zone._lageSortable = null;
      }
      zone._lageSortable = new Sortable(zone, {
        group: 'lage-phase',
        animation: 150,
        ghostClass: 'site-card--ghost',
        chosenClass: 'site-card--chosen',
        dragClass: 'site-card--drag',
        delay: 150,
        delayOnTouchOnly: true,
        touchStartThreshold: 8,
        fallbackTolerance: 5,
        fallbackOnBody: true,
        handle: '.site-card',
        filter: 'select,input,button,a',
        onEnd(evt) {
          const card = evt.item;
          const siteId = card.dataset.siteId;
          if (!siteId) return;
          if (evt.from === evt.to && evt.oldIndex === evt.newIndex) return;
          const phase = evt.to.dataset.phase;
          if (!phase) return;
          postPhase(lageId, siteId, phase, evt.newIndex);
        },
      });
    });
  }

  function initWs(lageId) {
    if (!lageId) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/lage/${lageId}`);
    let pingInterval;

    ws.addEventListener('open', () => {
      pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 25000);
    });

    ws.addEventListener('message', evt => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'cross_marker:changed') {
          htmx.trigger(document.body, 'crossMarkerChanged');
          return;
        }
        if (msg.type === 'site:card_changed' && msg.site_id) {
          const card = document.querySelector(`[data-site-id="${msg.site_id}"]`);
          if (card) {
            htmx.ajax('GET', `/lage/${lageId}/stellen/${msg.site_id}/card`, {
              target: card,
              swap: 'outerHTML',
            }).then(() => scheduleInit());
          }
          return;
        }
        if (msg.reload_board || msg.type === 'site:sector_changed') location.reload();
      } catch (e) { /* noop */ }
    });

    ws.addEventListener('close', () => {
      clearInterval(pingInterval);
      setTimeout(() => initWs(lageId), 5000);
    });

    ws.addEventListener('error', () => ws.close());
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      const lageId = getLageId();
      initBoard();
      initWs(lageId);
    });
  } else {
    const lageId = getLageId();
    initBoard();
    initWs(lageId);
  }

  document.body.addEventListener('htmx:afterSwap',    scheduleInit);
  document.body.addEventListener('htmx:oobAfterSwap', scheduleInit);
  document.body.addEventListener('htmx:afterSettle',  scheduleInit);
})();
