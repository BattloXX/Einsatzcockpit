/* Lage-Phasen-Board – SortableJS Drag & Drop + WebSocket Live-Reload */
(function () {
  'use strict';

  function getLageId() {
    const el = document.getElementById('lage-board');
    return el ? el.dataset.lageId : null;
  }

  function getCsrf() {
    return document.cookie.match(/(?:^|;\s*)ec_csrf=([^;]+)/)?.[1] || '';
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

  function resyncBoard(lageId) {
    const scroll = { x: window.scrollX, y: window.scrollY };
    const focusedId = document.activeElement && document.activeElement.id;
    htmx.trigger(document.body, 'sitePhaseChanged');
    htmx.ajax('GET', `/lage/${lageId}/kopf`, { target: document.body, swap: 'none' });
    htmx.trigger(document.body, 'crossMarkerChanged');
    if (typeof window.applyBoardFilters === 'function') window.applyBoardFilters();
    requestAnimationFrame(() => {
      window.scrollTo(scroll.x, scroll.y);
      if (focusedId) document.getElementById(focusedId)?.focus({ preventScroll: true });
    });
  }

  function updateConnectionStatus(status) {
    const el = document.getElementById('lage-connection-status');
    if (!el) return;
    const now = new Date().toLocaleTimeString('de-AT', { hour12: false });
    el.textContent = `${status} · aktualisiert ${now}`;
    document.dispatchEvent(new CustomEvent('board-last-update', { detail: Date.now() }));
  }

  function showSessionExpiredBanner() {
    if (document.getElementById('sessionExpiredBanner')) return;
    const banner = document.createElement('div');
    banner.id = 'sessionExpiredBanner';
    banner.style.cssText = (
      'position:fixed;top:0;left:0;right:0;z-index:9999;'
      + 'background:#b91c1c;color:#fff;padding:12px 16px;'
      + 'display:flex;align-items:center;justify-content:center;gap:16px;'
      + 'box-shadow:0 2px 8px rgba(0,0,0,.3);font-weight:600;'
    );
    banner.innerHTML = '<span>Sitzung abgelaufen -- bitte neu anmelden</span>'
      + '<a style="background:#fff;color:#b91c1c;padding:6px 14px;'
      + 'border-radius:4px;font-weight:700;text-decoration:none;" href="/login">Anmelden</a>';
    document.body.appendChild(banner);
  }

  function initWs(lageId) {
    if (!lageId) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let pingInterval;
    let disconnectedAt = null;
    let reconnectAttempt = 0;
    let reconnectStopped = false;
    const clientId = sessionStorage.getItem('ecBoardClientId') || crypto.randomUUID();
    sessionStorage.setItem('ecBoardClientId', clientId);
    document.body.addEventListener('htmx:configRequest', event => {
      event.detail.headers['X-EC-Client'] = clientId;
    });
    setInterval(() => {
      fetch('/api/v1/live/state', { credentials: 'same-origin', headers: { 'X-EC-Client': clientId } })
        .then(response => { if (response.ok) updateConnectionStatus('verbunden'); });
    }, 300000);

    function connect() {
      const ws = new WebSocket(`${proto}://${location.host}/ws/lage/${lageId}`);

      ws.addEventListener('open', () => {
        updateConnectionStatus('verbunden');
        pingInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send('ping');
        }, 25000);

        if (disconnectedAt !== null) {
          window.ecDiagLog('lage-ws:reconnected', Date.now() - disconnectedAt);
          resyncBoard(lageId);
          disconnectedAt = null;
        }
        reconnectAttempt = 0;
      });

      // Einziger WS-Handler fuer das Lage-Board (vormals zusaetzlich dupliziert
      // in board.html inline -- zwei unabhaengige Verbindungen zum selben
      // Endpunkt mit widerspruechlicher Behandlung derselben Events, siehe
      // GSL-Reload-Audit Session 2026-07-16). Vollstaendiger Dispatch aller
      // von ui_major_incident.py::broadcast_lage() gesendeten Event-Typen,
      // die das Board betreffen -- keiner davon reloadet mehr die Seite.
      function refreshCard(siteId) {
        const card = document.querySelector(`.site-card[data-site-id="${siteId}"]`);
        if (card) {
          htmx.ajax('GET', `/lage/${lageId}/stellen/${siteId}/card`, {
            target: card,
            swap: 'outerHTML',
          }).then(() => scheduleInit());
        }
        const modal = document.getElementById('siteDetailModal');
        const content = document.getElementById('siteDetailContent');
        if (modal && modal.open && content) {
          const header = content.querySelector('.modal__header[data-open-site-id]');
          if (header && String(header.dataset.openSiteId) === String(siteId)) {
            htmx.ajax('GET', `/lage/${lageId}/stellen/${siteId}`, {
              target: '#siteDetailContent',
              swap: 'innerHTML',
            });
          }
        }
      }

      ws.addEventListener('message', evt => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.origin && msg.origin === clientId) return;
          updateConnectionStatus('verbunden');
          if (msg.type === 'cross_marker:changed') {
            htmx.trigger(document.body, 'crossMarkerChanged');
            return;
          }
          // Reine Karten-Attribut-Aenderungen (keine Phasen-/Spaltenbewegung):
          // site_updated/site:sector_changed aendern nie site.phase (site_edit()
          // aendert nur Bezeichnung/Adresse; die Abschnitts-Zuweisung haengt nicht
          // von der Phase ab) -- ein gezielter Karten-Swap genuegt, kein Reload.
          if (
            (msg.type === 'site:card_changed' || msg.type === 'site_prio_changed'
              || msg.type === 'site_updated' || msg.type === 'site:sector_changed')
            && msg.site_id
          ) {
            refreshCard(msg.site_id);
            return;
          }
          // Strukturelle Aenderungen (neue Karte / Karte wechselt die Phasen-Spalte):
          // alle Phasen-Spalten hoeren per hx-trigger="sitePhaseChanged from:body"
          // auf dieses Event und laden ihren Inhalt gezielt per htmx-GET neu
          // (analog zum bestehenden cross-marker-col-body-Muster), niemand muss
          // wissen, welche Spalte konkret betroffen ist.
          if (msg.type === 'site_created' || msg.type === 'site_phase_changed') {
            htmx.trigger(document.body, 'sitePhaseChanged');
            return;
          }
          if (msg.type === 'staff:changed' || msg.type === 'ressource:changed') {
            htmx.trigger(document.body, 'sitePhaseChanged');
            return;
          }
          if (msg.type === 'section:changed') {
            htmx.trigger(document.body, 'sitePhaseChanged');
            const filter = document.getElementById('sector-filter-wrap');
            if (filter) {
              htmx.ajax('GET', `/lage/${lageId}/board-sektorfilter`, { target: filter, swap: 'outerHTML' })
                .then(() => {
                  window._allSectorVals = Array.from(document.querySelectorAll('.sector-cb'), cb => cb.value);
                  if (typeof window._updateSectorUI === 'function') window._updateSectorUI();
                  if (typeof window.applyBoardFilters === 'function') window.applyBoardFilters();
                });
            }
            return;
          }
          // Lage-Stammdaten (Name/Status) geaendert: nur die Kopfzeile per OOB
          // nachladen, kein Reload -- analog zur Kopfleiste des Einsatz-Boards.
          if (msg.type === 'lage_updated') {
            htmx.ajax('GET', `/lage/${lageId}/kopf`, { target: document.body, swap: 'none' });
            return;
          }
          // Lage beendet: Board ist als Live-Kontext vorbei -- gezielt aufs
          // Dashboard weiterleiten statt die Seite blind neu zu laden.
          if (msg.type === 'lage_closed') {
            window.location.href = `/lage/${lageId}/dashboard`;
          }
        } catch (e) { /* noop */ }
      });

      ws.addEventListener('close', event => {
        clearInterval(pingInterval);
        pingInterval = null;
        if (event.code === 4401 || event.code === 4403) {
          updateConnectionStatus('Sitzung abgelaufen');
          reconnectStopped = true;
          showSessionExpiredBanner();
          return;
        }
        if (disconnectedAt === null) disconnectedAt = Date.now();
        updateConnectionStatus('verbindet neu');
        const backoff = reconnectAttempt === 0
          ? 0
          : Math.min(1000 * 2 ** (reconnectAttempt - 1), 15000);
        reconnectAttempt++;
        const jitter = Math.random() * 500;
        setTimeout(() => { if (!reconnectStopped) connect(); }, backoff + jitter);
      });

      ws.addEventListener('error', () => ws.close());
    }

    connect();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      const lageId = getLageId();
      initBoard();
      initWs(lageId);
      window.addEventListener('ec:resync', () => resyncBoard(lageId));
    });
  } else {
    const lageId = getLageId();
    initBoard();
    initWs(lageId);
    window.addEventListener('ec:resync', () => resyncBoard(lageId));
  }

  document.body.addEventListener('htmx:afterSwap',    scheduleInit);
  document.body.addEventListener('htmx:oobAfterSwap', scheduleInit);
  document.body.addEventListener('htmx:afterSettle',  scheduleInit);

  // Kurzer optischer Puls auf einer frisch per WS/HTMX aktualisierten Karte
  // (vormals inline in board.html neben der jetzt entfernten zweiten
  // WS-Verbindung; hierher verschoben, damit es weiterhin ausgeloest wird).
  document.body.addEventListener('htmx:afterSwap', evt => {
    const tgt = evt.detail.target;
    if (typeof window.applyBoardFilters === 'function') window.applyBoardFilters();
    if (tgt && tgt.dataset && tgt.dataset.siteId) {
      const newCard = document.querySelector(`.site-card[data-site-id="${tgt.dataset.siteId}"]`);
      if (newCard) {
        newCard.classList.add('site-card--refreshed');
        setTimeout(() => newCard.classList.remove('site-card--refreshed'), 800);
      }
    }
  });
})();
