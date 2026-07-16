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

  // STAB-6: State-Resync nach Reconnect. Ein Blip trennt die WS-Verbindung;
  // waehrenddessen gesendete Broadcasts (Karten-/Sektor-Aenderungen anderer
  // Nutzer) gehen verloren, ohne dass das Board das je bemerkt (es reconnected
  // einfach stillschweigend). Analog zum "server-wins"-Reload in app.js
  // (incidentBoard._connectWS): Reconnect mit Backoff+Jitter, und ein
  // Full-Reload NUR wenn die Verbindung tatsaechlich eine Weile weg war (kurze
  // Blips sollen nicht neu laden) und nicht haeufiger als alle 10s (Schutz vor
  // Reload-Stuermen bei flackernder Verbindung).
  const RELOAD_COOLDOWN_MS = 10000;

  function initWs(lageId) {
    if (!lageId) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let pingInterval;
    let disconnectedAt = null;
    let reconnectAttempt = 0;

    function connect() {
      const ws = new WebSocket(`${proto}://${location.host}/ws/lage/${lageId}`);

      ws.addEventListener('open', () => {
        pingInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send('ping');
        }, 25000);

        if (disconnectedAt !== null) {
          const downMs = Date.now() - disconnectedAt;
          const lastReload = Number(sessionStorage.getItem('ec_last_lage_ws_reload') || 0);
          if (downMs > 2000 && Date.now() - lastReload > RELOAD_COOLDOWN_MS) {
            sessionStorage.setItem('ec_last_lage_ws_reload', String(Date.now()));
            const modal = document.getElementById('siteDetailModal');
            if (modal && modal.open) {
              modal.addEventListener('close', () => location.reload(), { once: true });
            } else {
              location.reload();
            }
          }
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

      ws.addEventListener('close', () => {
        clearInterval(pingInterval);
        if (disconnectedAt === null) disconnectedAt = Date.now();
        reconnectAttempt++;
        const backoff = Math.min(1000 * 2 ** reconnectAttempt, 15000);
        const jitter = Math.random() * 500;
        setTimeout(connect, backoff + jitter);
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
    });
  } else {
    const lageId = getLageId();
    initBoard();
    initWs(lageId);
  }

  document.body.addEventListener('htmx:afterSwap',    scheduleInit);
  document.body.addEventListener('htmx:oobAfterSwap', scheduleInit);
  document.body.addEventListener('htmx:afterSettle',  scheduleInit);

  // Kurzer optischer Puls auf einer frisch per WS/HTMX aktualisierten Karte
  // (vormals inline in board.html neben der jetzt entfernten zweiten
  // WS-Verbindung; hierher verschoben, damit es weiterhin ausgeloest wird).
  document.body.addEventListener('htmx:afterSwap', evt => {
    const tgt = evt.detail.target;
    if (tgt && tgt.dataset && tgt.dataset.siteId) {
      if (typeof window.applyBoardFilters === 'function') window.applyBoardFilters();
      const newCard = document.querySelector(`.site-card[data-site-id="${tgt.dataset.siteId}"]`);
      if (newCard) {
        newCard.classList.add('site-card--refreshed');
        setTimeout(() => newCard.classList.remove('site-card--refreshed'), 800);
      }
    }
  });
})();
