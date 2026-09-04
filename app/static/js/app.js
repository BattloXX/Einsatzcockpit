/* ─── Site-Detail-Panel: gemeinsames Schließen für Board-Dialog & Karten-Sidebar ─── */
window.closeSiteDetailPanel = function(el) {
  const dlg = el.closest('dialog');
  if (dlg) { dlg.close(); return; }
  const panel = el.closest('#site-panel-wrap');
  if (panel) { panel.classList.remove('open'); }
};

/* ─── Teilnahme-Liste: Sortierung wechseln (Mannschaft/Termin/Archiv-Seite) ─── */
window.teilnahmeSetSort = function(bezugTyp, bezugId, sort) {
  const container = document.querySelector('#teilnahme-liste-container, #mannschaft-container');
  if (!container || typeof htmx === 'undefined') return;
  const url = '/teilnahme/' + bezugTyp + '/' + bezugId + '/liste' + (sort ? ('?sort=' + sort) : '');
  htmx.ajax('GET', url, { target: container, swap: 'innerHTML' });
  ['teilnahme-druck-link', 'teilnahme-pdf-link', 'teilnahme-xlsx-link'].forEach(function(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const base = el.getAttribute('href').split('?')[0];
    el.setAttribute('href', sort ? (base + '?sort=' + sort) : base);
  });
};

/* ─── Alpine.js Global App State ────────────────────────────────── */
document.addEventListener('alpine:init', () => {
  Alpine.data('incidentSubnav', () => ({
    init() {
      this._reposition = () => {
        if (this.$el.open) this.positionMenu();
      };
      window.addEventListener('resize', this._reposition);
      window.addEventListener('scroll', this._reposition, { passive: true });
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', this._reposition);
        window.visualViewport.addEventListener('scroll', this._reposition);
      }
    },

    destroy() {
      window.removeEventListener('resize', this._reposition);
      window.removeEventListener('scroll', this._reposition);
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', this._reposition);
        window.visualViewport.removeEventListener('scroll', this._reposition);
      }
    },

    onToggle() {
      if (this.$el.open) requestAnimationFrame(() => this.positionMenu());
    },

    positionMenu() {
      const trigger = this.$refs.trigger.getBoundingClientRect();
      const menu = this.$refs.menu;
      const viewport = window.visualViewport;
      const viewportTop = viewport ? viewport.offsetTop : 0;
      const viewportLeft = viewport ? viewport.offsetLeft : 0;
      const viewportWidth = viewport ? viewport.width : window.innerWidth;
      const viewportHeight = viewport ? viewport.height : window.innerHeight;
      const gap = 4;
      const edge = 12;
      const safeBottom = parseFloat(getComputedStyle(this.$el).getPropertyValue('--incident-subnav-safe-bottom')) || 0;
      const availableWidth = Math.max(0, viewportWidth - edge * 2);
      const width = Math.min(Math.max(220, menu.scrollWidth), availableWidth);
      const left = Math.min(
        Math.max(trigger.left, viewportLeft + edge),
        viewportLeft + viewportWidth - edge - width
      );
      const viewportBottom = viewportTop + viewportHeight - safeBottom;
      const belowTop = trigger.bottom + gap;
      const roomBelow = viewportBottom - edge - belowTop;
      const roomAbove = trigger.top - gap - (viewportTop + edge);
      const openAbove = roomBelow < menu.scrollHeight && roomAbove > roomBelow;
      const maxHeight = Math.max(0, openAbove ? roomAbove : roomBelow);
      const top = openAbove
        ? Math.max(viewportTop + edge, trigger.top - gap - Math.min(menu.scrollHeight, maxHeight))
        : Math.max(viewportTop + edge, belowTop);

      menu.style.setProperty('--incident-subnav-top', top + 'px');
      menu.style.setProperty('--incident-subnav-left', left + 'px');
      menu.style.setProperty('--incident-subnav-width', width + 'px');
      menu.style.setProperty('--incident-subnav-max-height', maxHeight + 'px');
    }
  }));

  Alpine.data('appState', () => ({
    toasts: [],
    newIncidentAlert: null,
    mobileMenuOpen: false,
    profilSheetOpen: false,
    _ws: null,

    init() {
      this._connectGlobal();
      this._registerPush();
      // Schließe Mobile-Menü bei Navigation (Link-Klick auf Anker innerhalb des Panels)
      document.addEventListener('click', (e) => {
        const link = e.target.closest('.mobile-menu__link, .profil-sheet__link');
        if (link) {
          this.mobileMenuOpen = false;
          this.profilSheetOpen = false;
        }
      });
    },

    neuenEinsatzOeffnen() {
      const dlg = document.getElementById('newIncidentModal');
      if (dlg && typeof dlg.showModal === 'function') { dlg.showModal(); return; }
      window.location.href = '/?neuer_einsatz=1';
    },

    addToast(msg, type = 'info') {
      const id = Date.now();
      this.toasts.push({ id, msg, type });
      setTimeout(() => this.removeToast(id), 6000);
    },

    removeToast(id) {
      this.toasts = this.toasts.filter(t => t.id !== id);
    },

    onIncidentCreated(detail) {
      const alarmCode = detail.alarm_type_code || detail.alarm || '';
      if (detail.alarm_erlaubt === false) {
        return;
      }
      if (detail.is_exercise) {
        this.addToast('[ÜBUNG] Neuer Einsatz: ' + alarmCode, 'warn');
      } else {
        this.newIncidentAlert = detail;
        try { new Audio('/static/audio/alarm.mp3').play().catch(() => {}); } catch (_) {}
      }
    },

    _connectGlobal() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${location.host}/ws/global`;
      window.__ecGlobalWsOpen = false;
      // Exponentielles Backoff + Jitter statt fix 3s, damit eine flackernde
      // Verbindung nicht in einer engen Reconnect-Schleife hängt.
      let reconnectAttempt = 0;
      const connect = () => {
        const ws = new WebSocket(url);
        ws.onmessage = (e) => {
          if (e.data === 'pong') return;
          const ev = JSON.parse(e.data);
          if (ev.type === 'incident_created') {
            const customEv = new CustomEvent('incident-created', { detail: ev, bubbles: true });
            document.body.dispatchEvent(customEv);
          }
          if (ev.type === 'objekt_match') {
            // Objektverwaltung: Board-Panel neu laden (hx-trigger="objekt-match from:body")
            const matchEv = new CustomEvent('objekt-match', { detail: ev, bubbles: true });
            document.body.dispatchEvent(matchEv);
          }
          if (ev.type === 'einsatz_live') {
            const liveEv = new CustomEvent('einsatz-live', { detail: ev, bubbles: true });
            document.body.dispatchEvent(liveEv);
          }
          if (ev.type === 'gsl_live') {
            const liveEv = new CustomEvent('gsl-live', { detail: ev, bubbles: true });
            document.body.dispatchEvent(liveEv);
          }
          if (ev.type === 'print_job_status') {
            document.body.dispatchEvent(new CustomEvent('print-job-status', { detail: ev, bubbles: true }));
          }
        };
        ws.onclose = () => {
          window.__ecGlobalWsOpen = false;
          reconnectAttempt++;
          const backoff = Math.min(1000 * 2 ** reconnectAttempt, 15000);
          setTimeout(connect, backoff + Math.random() * 500);
        };
        ws.onopen = () => {
          window.__ecGlobalWsOpen = true;
          reconnectAttempt = 0;
        };
        this._ws = ws;
        setInterval(() => ws.readyState === 1 && ws.send('ping'), 30000);
      };
      connect();
    },

    _registerPush() {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
      fetch('/push/vapid-public-key')
        .then(r => r.json())
        .then(({ publicKey }) => {
          if (!publicKey) return;
          navigator.serviceWorker.ready.then(sw => {
            sw.pushManager.getSubscription().then(sub => {
              if (sub) {
                // Vorhandene Subscription immer ans Backend senden, damit nach einem
                // User-Wechsel (z.B. Device-Login) die user_id aktualisiert wird.
                fetch('/push/subscribe', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(sub),
                }).catch(() => {});
                return;
              }
              // Nur neu subscriben wenn Berechtigung bereits erteilt
              if (Notification.permission === 'granted') {
                sw.pushManager.subscribe({
                  userVisibleOnly: true,
                  applicationServerKey: urlBase64ToUint8Array(publicKey),
                }).then(newSub => {
                  fetch('/push/subscribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(newSub),
                  }).catch(() => {});
                }).catch(() => {});
              }
            });
          });
        }).catch(() => {});
    },
  }));

  /* ─── Laufender Einsatz: globales In-App-Banner ────────────────── */
  Alpine.data('einsatzLiveBanner', () => ({
    incident: null,
    lage: null,
    lageCount: 0,
    incidentCount: 0,
    duration: '0 min',
    serverTimeSkew: 0,
    dismissedIncidentId: null,
    _durationTimer: null,
    _pollTimer: null,
    _liveHandler: null,
    _visibilityHandler: null,
    _onlineHandler: null,

    get visible() {
      const current = this.lage ? 'lage-' + this.lage.id : (this.incident ? 'inc-' + this.incident.id : null);
      if (current === null || current === this.dismissedIncidentId) return false;
      if (this.lage) return true;
      const boardIncident = document.getElementById('incidentHeaderAlarm')?.dataset.incidentId;
      const isOwnMobileBoard = window.matchMedia('(max-width: 760px)').matches
        && boardIncident === String(this.incident.id);
      return !isOwnMobileBoard;
    },

    init() {
      try { this.dismissedIncidentId = sessionStorage.getItem('ec-live-dismissed'); } catch (_) {}
      this.fetchState();
      this._durationTimer = setInterval(() => this.tick(), 1000);
      this._pollTimer = setInterval(() => {
        if (!window.__ecGlobalWsOpen) this.fetchState();
      }, 60000);
      this._liveHandler = (event) => this.applyUpdate(event.detail);
      this._gslHandler = (event) => this.applyUpdate(event.detail);
      this._visibilityHandler = () => {
        if (document.visibilityState === 'visible') this.fetchState();
      };
      this._onlineHandler = () => this.fetchState();
      document.body.addEventListener('einsatz-live', this._liveHandler);
      document.body.addEventListener('gsl-live', this._gslHandler);
      document.addEventListener('visibilitychange', this._visibilityHandler);
      window.addEventListener('online', this._onlineHandler);
    },

    destroy() {
      if (this._durationTimer) clearInterval(this._durationTimer);
      if (this._pollTimer) clearInterval(this._pollTimer);
      document.body.removeEventListener('einsatz-live', this._liveHandler);
      document.body.removeEventListener('gsl-live', this._gslHandler);
      document.removeEventListener('visibilitychange', this._visibilityHandler);
      window.removeEventListener('online', this._onlineHandler);
    },

    async fetchState() {
      try {
        const response = await fetch('/api/v1/live/state');
        if (!response.ok) return;
        this.applyState(await response.json());
      } catch (_) {}
    },

    applyState(data) {
      const serverTime = Date.parse(data.server_time);
      if (!isNaN(serverTime)) this.serverTimeSkew = serverTime - Date.now();
      this.incidentCount = data.incident_count || 0;
      this.incident = data.incident || null;
      this.lageCount = data.lage_count || 0;
      this.lage = data.lage || null;
      this.tick();
    },

    applyUpdate(data) {
      if (!data) return;
      const serverTime = Date.parse(data.server_time);
      if (!isNaN(serverTime)) this.serverTimeSkew = serverTime - Date.now();
      if (Object.prototype.hasOwnProperty.call(data, 'incident_count')) {
        this.incidentCount = data.incident_count || 0;
      }
      if (Object.prototype.hasOwnProperty.call(data, 'lage_count')) this.lageCount = data.lage_count || 0;
      if (Object.prototype.hasOwnProperty.call(data, 'lage')) {
        this.lage = data.lage === null ? null : (this.lage && this.lage.id === data.lage.id
          ? { ...this.lage, ...data.lage } : data.lage);
      }
      if (Object.prototype.hasOwnProperty.call(data, 'incident')) {
        if (data.incident === null) {
          this.incident = null;
        } else if (this.incident && this.incident.id === data.incident.id) {
          this.incident = { ...this.incident, ...data.incident };
        } else {
          this.incident = data.incident;
        }
      } else {
        const update = data.live || data;
        const incidentId = update.id || update.incident_id;
        if (incidentId) {
          const normalized = { ...update, id: incidentId };
          this.incident = this.incident && this.incident.id === incidentId
            ? { ...this.incident, ...normalized }
            : normalized;
        }
      }
      this.tick();
    },

    tick() {
      const live = this.lage || this.incident;
      if (!live?.started_at) { this.duration = '0 min'; return; }
      const startedAt = Date.parse(live.started_at);
      if (isNaN(startedAt)) { this.duration = '0 min'; return; }
      const elapsedMinutes = Math.max(0, Math.floor((Date.now() + this.serverTimeSkew - startedAt) / 60000));
      if (elapsedMinutes < 60) {
        this.duration = elapsedMinutes + ' min';
        return;
      }
      const hours = Math.floor(elapsedMinutes / 60);
      const minutes = String(elapsedMinutes % 60).padStart(2, '0');
      this.duration = hours + ':' + minutes + ' h';
    },

    dismiss() {
      const current = this.lage ? 'lage-' + this.lage.id : (this.incident ? 'inc-' + this.incident.id : null);
      if (!current) return;
      this.dismissedIncidentId = current;
      try { sessionStorage.setItem('ec-live-dismissed', this.dismissedIncidentId); } catch (_) {}
    },

    formatCounts(counts) {
      if (!counts) return '';
      return counts.neu + ' neu · ' + counts.in_arbeit + ' in Arbeit · ' + counts.erledigt + ' erledigt';
    },
  }));

  /* ─── Lagemeldungs-Timer-Chip (SKKM-Regelkreis) ──────────────────
     Live-Countdown bis zur nächsten fälligen Lagemeldung. Erwartet ein
     ISO-UTC-Datum (z.B. "2026-06-16T11:00:00Z"). Ampel: grün → amber
     (≤10 min oder ≤20 %) → rot (überfällig). Sekündliches Update ohne Reload. */
  Alpine.data('lmChip', (dueIso, intervalMin) => ({
    dueIso: dueIso,
    intervalMin: intervalMin || 0,
    label: '',
    cls: '',
    _t: null,
    start() {
      this.tick();
      this._t = setInterval(() => this.tick(), 1000);
    },
    destroy() {
      if (this._t) clearInterval(this._t);
    },
    tick() {
      const due = new Date(this.dueIso).getTime();
      if (isNaN(due)) { this.label = ''; return; }
      const diffMs = due - Date.now();
      const diffMin = Math.round(diffMs / 60000);
      const t = new Date(this.dueIso).toLocaleTimeString('de-AT', { hour: '2-digit', minute: '2-digit' });
      // Schwellwerte: amber bei ≤10 min Rest oder ≤20 % des Intervalls
      const amberAt = Math.max(10, Math.round((this.intervalMin || 0) * 0.2));
      if (diffMs < 0) {
        const overdue = Math.abs(diffMin);
        this.cls = 'lm-chip--red';
        this.label = '⚠ Lagemeldung überfällig · seit ' + overdue + ' min (' + t + ')';
      } else if (diffMin <= amberAt) {
        this.cls = 'lm-chip--amber';
        this.label = 'Lagemeldung fällig ' + t + ' · in ' + diffMin + ' min';
      } else {
        this.cls = 'lm-chip--green';
        this.label = 'Lagemeldung ' + t + ' · in ' + diffMin + ' min';
      }
    },
  }));
});


/* ─── Zu-/Absage-Widget (Teams-Alarmierung) im Board-Header (Desktop only —
   .incident-header__actions ist auf Mobil bereits per CSS ausgeblendet) ─── */
function rsvpWidget(incidentId) {
  return {
    open: false,
    zusagen: 0,
    absagen: 0,
    namen: [],

    init() {
      this.load();
      window.addEventListener('rsvp-refresh', () => this.load());
    },

    async load() {
      try {
        const res = await fetch(`/einsatz/${incidentId}/rsvp.json`);
        if (!res.ok) return;
        const data = await res.json();
        this.zusagen = data.zusagen || 0;
        this.absagen = data.absagen || 0;
        this.namen = data.namen || [];
      } catch (_) { /* still — Widget zeigt einfach den letzten bekannten Stand */ }
    },
  };
}

/* ─── Incident Header (timer + last-update only, no WS) ─────────── */
function headerState(startedAt) {
  return {
    timerDisplay: '00:00',
    _lastUpdate: Date.now(),
    lastUpdateDisplay: '–',
    lastUpdateAgeSec: 0,
    lastUpdateState: 'fresh',
    connectionStatus: 'verbunden',

    init() {
      this._startTimer(new Date(startedAt));
      this._startLastUpdate();
      document.addEventListener('board-last-update', (e) => { this._lastUpdate = e.detail; });
      document.addEventListener('board-connection-status', (e) => { this.connectionStatus = e.detail; });
    },

    _startTimer(start) {
      const update = () => {
        const sec = Math.floor((Date.now() - start) / 1000);
        const m = String(Math.floor(sec / 60)).padStart(2, '0');
        const s = String(sec % 60).padStart(2, '0');
        this.timerDisplay = m + ':' + s;
        if (sec === 300 || sec === 301) showTimerAlert('Lagemeldung an RFL absetzen!', 'warn');
        if (sec === 600 || sec === 601) showTimerAlert('Spezialkräfte / Atemschutzsammelplatz prüfen!', 'alert');
      };
      update();
      setInterval(update, 1000);
    },

    _startLastUpdate() {
      const fmt = (d) => {
        const h = String(d.getHours()).padStart(2, '0');
        const m = String(d.getMinutes()).padStart(2, '0');
        const s = String(d.getSeconds()).padStart(2, '0');
        return `${h}:${m}:${s}`;
      };
      const tick = () => {
        this.lastUpdateDisplay = fmt(new Date(this._lastUpdate));
        this.lastUpdateAgeSec = Math.floor((Date.now() - this._lastUpdate) / 1000);
        this.lastUpdateState =
          this.lastUpdateAgeSec >= 300 ? 'stale' :
          this.lastUpdateAgeSec >= 60  ? 'warn'  : 'fresh';
      };
      tick();
      setInterval(tick, 1000);
    },
  };
}


/* ─── Incident Board WebSocket ──────────────────────────────────── */
function incidentBoard(incidentId, alarm, startedAt) {
  return {
    _ws: null,
    sidebarOpen: localStorage.getItem('sidebarOpen') === 'true',

    init() {
      this._clientId = sessionStorage.getItem('ecBoardClientId') || crypto.randomUUID();
      sessionStorage.setItem('ecBoardClientId', this._clientId);
      document.body.addEventListener('htmx:configRequest', (event) => {
        event.detail.headers['X-EC-Client'] = this._clientId;
      });
      document.body.addEventListener('htmx:afterRequest', (event) => {
        if (event.detail.successful) this._bumpLastUpdate();
      });
      this._connectWS(incidentId);
      this._setupKeyboard(incidentId);
      this._trackOpenModalEntity();
      window.addEventListener('ec:resync', () => this._resyncBoard(incidentId));
      this._keepaliveTimer = setInterval(() => {
        fetch('/api/v1/live/state', { credentials: 'same-origin', headers: { 'X-EC-Client': this._clientId } })
          .then((response) => { if (response.ok) this._bumpLastUpdate(); });
      }, 300000);
    },

    // Merkt sich, welche Karte gerade im #cardDetailModal offen ist (kind/uid aus der
    // aufgerufenen /detail-URL), damit WS-Events das offene Modal gezielt nachladen können.
    _trackOpenModalEntity() {
      const segToKind = { fahrzeug: 'vehicle', aufgabe: 'task', meldung: 'message', person: 'person' };
      document.body.addEventListener('htmx:afterSwap', (e) => {
        if (!e.target || e.target.id !== 'cardDetailBody') return;
        const path = e.detail && e.detail.requestConfig && e.detail.requestConfig.path;
        const m = path && path.match(/\/einsatz\/\d+\/(fahrzeug|aufgabe|meldung|person)\/(\d+)\/detail/);
        e.target.dataset.openKind = m ? segToKind[m[1]] : '';
        e.target.dataset.openUid = m ? m[2] : '';
      });
    },

    toggleSidebar() {
      this.sidebarOpen = !this.sidebarOpen;
      localStorage.setItem('sidebarOpen', String(this.sidebarOpen));
    },

    _bumpLastUpdate() {
      document.dispatchEvent(new CustomEvent('board-last-update', { detail: Date.now() }));
    },

    // ── Board-Events: gezielter HTMX-Swap statt Voll-Reload ────────────────
    _cardElId(kind, uid) {
      return `${kind === 'message' ? 'msg' : kind}-card-${uid}`;
    },

    _cardDetailUrlSegment(kind) {
      return { vehicle: 'fahrzeug', task: 'aufgabe', message: 'meldung', person: 'person' }[kind];
    },

    _swapCard(incidentId, kind, uid) {
      if (kind == null || uid == null) return;
      const el = document.getElementById(this._cardElId(kind, uid));
      if (!el) return;
      htmx.ajax('GET', `/einsatz/${incidentId}/karte/${kind}/${uid}`, { target: el, swap: 'outerHTML' });
    },

    _swapColumnBody(incidentId, columnId) {
      if (columnId == null) return;
      const zone = document.getElementById(`zone-${columnId}`);
      if (!zone) return;
      htmx.ajax('GET', `/einsatz/${incidentId}/spalte/${columnId}/inhalt`, { target: zone, swap: 'innerHTML' })
        .then(() => { if (window.reapplyMobileLane) window.reapplyMobileLane(); });
    },

    _swapColumn(incidentId, columnId) {
      if (columnId == null) return;
      const col = document.getElementById(`col-${columnId}`);
      if (!col) return;
      htmx.ajax('GET', `/einsatz/${incidentId}/spalte/${columnId}`, { target: col, swap: 'outerHTML' })
        .then(() => { if (window.reapplyMobileLane) window.reapplyMobileLane(); });
    },

    _swapKanban(incidentId) {
      const kanban = document.getElementById('kanban');
      if (!kanban) return;
      return htmx.ajax('GET', `/einsatz/${incidentId}/kanban`, { target: kanban, swap: 'innerHTML' })
        .then(() => { if (window.reapplyMobileLane) window.reapplyMobileLane(); });
    },

    _swapKopfleiste(incidentId) {
      // Reine OOB-Antwort (Alarm-Badge/Adresse, EL-vor-Ort, Lage-Ticker) — kein Haupt-Target nötig.
      return htmx.ajax('GET', `/einsatz/${incidentId}/kopfleiste`, { target: document.body, swap: 'none' })
        .then(() => { if (window.buildLaneDropdown) window.buildLaneDropdown(); });
    },

    _resyncBoard(incidentId) {
      const scroll = { x: window.scrollX, y: window.scrollY };
      const kanban = document.getElementById('kanban');
      const kanbanScroll = kanban ? { x: kanban.scrollLeft, y: kanban.scrollTop } : null;
      const focusedId = document.activeElement && document.activeElement.id;
      Promise.all([this._swapKanban(incidentId), this._swapKopfleiste(incidentId)]).finally(() => {
        window.scrollTo(scroll.x, scroll.y);
        if (kanban && kanbanScroll) {
          kanban.scrollLeft = kanbanScroll.x;
          kanban.scrollTop = kanbanScroll.y;
        }
        if (focusedId) document.getElementById(focusedId)?.focus({ preventScroll: true });
      });
    },

    _queueResync(incidentId) {
      const now = Date.now();
      const wait = Math.max(0, 5000 - (now - (this._lastResyncAt || 0)));
      if (wait === 0) {
        this._lastResyncAt = now;
        this._resyncBoard(incidentId);
        return;
      }
      clearTimeout(this._resyncTimer);
      this._resyncTimer = setTimeout(() => {
        this._lastResyncAt = Date.now();
        this._resyncBoard(incidentId);
      }, wait);
    },

    _refreshOpenModal(incidentId, kind, uid) {
      const body = document.getElementById('cardDetailBody');
      const modal = document.getElementById('cardDetailModal');
      if (!body || !modal || !modal.open) return;
      if (body.dataset.openKind !== kind || String(body.dataset.openUid) !== String(uid)) return;
      const seg = this._cardDetailUrlSegment(kind);
      if (!seg) return;
      htmx.ajax('GET', `/einsatz/${incidentId}/${seg}/${uid}/detail`, { target: '#cardDetailBody', swap: 'innerHTML' });
    },

    _handleBoardEvent(ev, incidentId) {
      switch (ev.type) {
        case 'vehicle_updated':
        case 'task_assigned':
        case 'message_assigned':
        case 'person_updated':
          this._swapCard(incidentId, ev.kind, ev.uid);
          this._refreshOpenModal(incidentId, ev.kind, ev.uid);
          if (ev.vehicle_uid != null) this._swapCard(incidentId, 'vehicle', ev.vehicle_uid);
          break;
        case 'task_updated':
        case 'message_updated':
        case 'task_cancelled':
          // Spalten-Swap statt Einzelkarte: Status-Wechsel kann die Karte ans
          // Spaltenende verschieben (sink_done_cards serverseitig).
          this._swapColumnBody(incidentId, ev.column_id);
          if (ev.kind && ev.uid != null) this._refreshOpenModal(incidentId, ev.kind, ev.uid);
          if (ev.vehicle_uid != null) this._swapCard(incidentId, 'vehicle', ev.vehicle_uid);
          break;
        case 'person_deleted': {
          const el = document.getElementById(this._cardElId('person', ev.uid));
          if (el) el.remove();
          break;
        }
        case 'vehicle_added':
        case 'vehicle_moved':
        case 'task_created':
        case 'message_created':
        case 'person_created':
        case 'ai_suggestions_ready':
        case 'card_moved':
          this._swapColumnBody(incidentId, ev.column_id);
          if (ev.source_column_id != null && ev.source_column_id !== ev.column_id) {
            this._swapColumnBody(incidentId, ev.source_column_id);
          }
          break;
        case 'column_renamed':
        case 'column_updated':
          this._swapColumn(incidentId, ev.column_id);
          break;
        case 'column_created':
        case 'column_deleted':
        case 'columns_reordered':
          this._swapKanban(incidentId);
          break;
        case 'alarm_type_changed':
        case 'address_updated':
        case 'incident_leader_changed':
        case 'ai_hints_ready':
          this._swapKopfleiste(incidentId);
          break;
        case 'lis_sync':
        case 'dibos_sync':
        case 'objektgefahren':
        case 'incident_geocoded':
        case 'incident_reopened':
        case 'log_updated':
          this._queueResync(incidentId);
          break;
        // Diese Typen werden im selben WS-Handler unterhalb des Board-Dispatchs
        // oder von der Lagefuehrungs-Komponente verarbeitet. Hier bleiben sie
        // explizit bekannt, damit sie keinen allgemeinen Fragment-Resync ausloesen.
        case 'incident_closed':
        case 'autoclose_warning':
        case 'autoclose_dismissed':
        case 'rsvp:changed':
        case 'message_due':
        case 'task_due':
        case 'troop_created':
        case 'troop_redeployed':
        case 'troop_started':
        case 'troop_back_pressure_reported':
        case 'troop_status_changed':
        case 'pressure_logged':
        case 'troop_objective_reached':
        case 'troop_meldung':
        case 'troop_warning':
        case 'troop_warning_acked':
        case 'troop_standort':
        case 'lagefuehrung.presence.changed':
        case 'lagefuehrung.feature.locked':
        case 'lagefuehrung.feature.unlocked':
        case 'lagefuehrung.vehicle.pinned':
        case 'lagefuehrung.feature.created':
        case 'lagefuehrung.feature.updated':
        case 'lagefuehrung.feature.deleted':
        case 'lagefuehrung.fuehrer_changed':
        case 'lagefuehrung.berechtigung.changed':
        case 'lagefuehrung.chronologie_changed':
          break;
        default:
          // Ein unbekanntes Event darf den Arbeitszustand des Einsatzleiters nicht
          // durch einen Voll-Reload zerstoeren. Fragmente still serverseitig abgleichen.
          window.ecDiagLog('ws:unknown_event', ev.type);
          this._queueResync(incidentId);
      }
    },

    _connectWS(id) {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${location.host}/ws/incident/${id}`;
      let disconnectedAt = null;
      let reconnectAttempt = 0;
      let pingInterval = null;
      let reconnectStopped = false;
      const connect = () => {
        const ws = new WebSocket(url);
        ws.onmessage = (e) => {
          if (e.data === 'pong') return;
          const ev = JSON.parse(e.data);
          if (ev.origin && ev.origin === this._clientId) return;
          this._bumpLastUpdate();
          this._handleBoardEvent(ev, id);
          if (ev.reload_breathing || ev.type === 'troop_created' || ev.type === 'troop_started' || ev.type === 'troop_status_changed') {
            if (document.getElementById('troopsGrid')) location.reload();
          }
          if (ev.type === 'pressure_logged') {
            window.dispatchEvent(new CustomEvent('breathing-pressure', {
              detail: {
                troopId: ev.troop_id,
                lowestPressure: ev.lowest_pressure ?? ev.pressure,
                memberId: ev.member_id ?? null,
                pressure: ev.pressure,
              }
            }));
          }
          if (ev.type === 'troop_warning') {
            window.dispatchEvent(new CustomEvent('breathing-warning', {
              detail: { troopId: ev.troop_id, kind: ev.kind }
            }));
          }
          if (ev.type === 'troop_warning_acked') {
            window.dispatchEvent(new CustomEvent('breathing-warning-acked', {
              detail: { troopId: ev.troop_id, kind: ev.kind }
            }));
          }
          if (ev.type === 'troop_meldung' || ev.type === 'troop_standort') {
            if (document.getElementById('troopsGrid')) location.reload();
          }
          if (ev.type === 'message_due' || ev.type === 'task_due') {
            const state = window.Alpine && Alpine.$data(document.body);
            if (state && state.addToast) {
              state.addToast((ev.type === 'task_due' ? 'Auftrag fällig: ' : 'Meldung fällig: ') + ev.title, 'warn');
            }
            try { new Audio('/static/audio/alarm.mp3').play().catch(() => {}); } catch (_) {}
          }
          if (ev.type === 'incident_closed') {
            window.location.href = `/archiv/${id}`;
          }
          if (ev.type === 'autoclose_warning') {
            this._showAutocloseBanner(id, ev.grace_minutes || 60);
          }
          if (ev.type === 'autoclose_dismissed') {
            const banner = document.getElementById('autocloseBanner');
            if (banner) banner.remove();
          }
          if (ev.type === 'rsvp:changed') {
            window.dispatchEvent(new CustomEvent('rsvp-refresh'));
          }
        };
        ws.onclose = (event) => {
          clearInterval(pingInterval);
          pingInterval = null;
          if (event.code === 4401 || event.code === 4403) {
            document.dispatchEvent(new CustomEvent('board-connection-status', { detail: 'Sitzung abgelaufen' }));
            reconnectStopped = true;
            this._showSessionExpiredBanner();
            return;
          }
          if (disconnectedAt === null) disconnectedAt = Date.now();
          document.dispatchEvent(new CustomEvent('board-connection-status', { detail: 'verbindet neu' }));
          const backoff = reconnectAttempt === 0
            ? 0
            : Math.min(1000 * 2 ** (reconnectAttempt - 1), 15000);
          reconnectAttempt++;
          const jitter = Math.random() * 500;
          setTimeout(() => { if (!reconnectStopped) connect(); }, backoff + jitter);
        };
        ws.onopen = () => {
          document.dispatchEvent(new CustomEvent('board-connection-status', { detail: 'verbunden' }));
          if (disconnectedAt !== null) {
            window.ecDiagLog('ws:reconnected', Date.now() - disconnectedAt);
            this._resyncBoard(id);
            disconnectedAt = null;
          }
          reconnectAttempt = 0;
        };
        this._ws = ws;
        pingInterval = setInterval(() => ws.readyState === 1 && ws.send('ping'), 30000);
      };
      connect();
    },

    _showSessionExpiredBanner() {
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
    },

    _showAutocloseBanner(incidentId, graceMinutes) {
      if (document.getElementById('autocloseBanner')) return;
      const banner = document.createElement('div');
      banner.id = 'autocloseBanner';
      banner.style.cssText = (
        'position:fixed;top:0;left:0;right:0;z-index:9999;'
        + 'background:#e65100;color:#fff;padding:12px 16px;'
        + 'display:flex;align-items:center;justify-content:center;gap:16px;'
        + 'box-shadow:0 2px 8px rgba(0,0,0,.3);font-weight:600;'
      );
      banner.innerHTML = (
        '<span>⏰ Dieser Einsatz läuft seit 48h — soll er offen bleiben?</span>'
        + ' <button type="button" id="autocloseKeepOpenBtn" '
        + '  style="background:#fff;color:#e65100;border:none;padding:6px 14px;'
        + '  border-radius:4px;font-weight:700;cursor:pointer;">'
        + '  Offen halten</button>'
        + ' <span style="font-size:.85rem;opacity:.85;">'
        + '  (Sonst Auto-Close in ' + graceMinutes + ' Min)</span>'
      );
      document.body.appendChild(banner);
      document.body.style.paddingTop = (banner.offsetHeight + 'px');
      document.getElementById('autocloseKeepOpenBtn').addEventListener('click', () => {
        fetch(`/einsatz/${incidentId}/autoclose/keepopen`, {
          method: 'POST',
          credentials: 'same-origin',
        }).then(() => {
          banner.remove();
          document.body.style.paddingTop = '';
        });
      });
    },

    _setupKeyboard(incidentId) {
      document.addEventListener('keydown', (e) => {
        if (!e.ctrlKey && !e.metaKey) return;
        switch (e.key.toLowerCase()) {
          case 'a': e.preventDefault(); document.getElementById('taskInput')?.focus(); break;
          case 'm': e.preventDefault(); document.getElementById('msgInput')?.focus(); break;
          case 'u': e.preventDefault(); window.open(`/archiv/${incidentId}`, '_blank'); break;
        }
      });
    },
  };
}


/* ─── Move vehicle via select ────────────────────────────────────── */
async function moveVehicle(vehicleId, columnId, incidentId) {
  if (!columnId) return;
  await fetch(`/einsatz/${incidentId}/fahrzeug/${vehicleId}/verschieben`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: `column_id=${columnId}`,
  });
}

async function assignTask(taskId, vehicleId, incidentId) {
  if (!vehicleId) return;
  await fetch(`/einsatz/${incidentId}/aufgabe/${taskId}/zuweisen`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: `vehicle_id=${vehicleId}`,
  });
}


/* ─── Timer alert popup ──────────────────────────────────────────── */
function showTimerAlert(msg, level) {
  const div = document.createElement('div');
  div.className = `timer-alert timer-alert--${level}`;
  div.innerHTML = `<strong>⏰ ${msg}</strong> <button onclick="this.parentNode.remove()">✕</button>`;
  document.body.appendChild(div);
  try { new Audio('/static/audio/alert.mp3').play().catch(() => {}); } catch (_) {}
  setTimeout(() => div.remove(), 15000);
}


/* ─── Voice Dictation (Web Speech API) ──────────────────────────── */
let recognition = null;

function startVoice(targetInputId) {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRec) {
    alert('Sprachdiktat wird von diesem Browser nicht unterstützt.\nBitte Chrome oder Edge verwenden.');
    return;
  }
  const input = document.getElementById(targetInputId);
  if (!input) return;

  if (recognition) { recognition.stop(); recognition = null; return; }

  recognition = new SpeechRec();
  recognition.lang = 'de-AT';
  recognition.continuous = false;
  recognition.interimResults = true;

  const btn = document.querySelector(`button[onclick="startVoice('${targetInputId}')"]`);
  if (btn) btn.classList.add('recording');

  recognition.onresult = (e) => {
    const transcript = Array.from(e.results).map(r => r[0].transcript).join('');
    input.value = transcript;
  };
  recognition.onend = () => {
    recognition = null;
    if (btn) btn.classList.remove('recording');
  };
  recognition.onerror = () => {
    recognition = null;
    if (btn) btn.classList.remove('recording');
  };
  recognition.start();
}


/* ─── PWA Service Worker Registration ───────────────────────────── */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    // /sw.js served from root → scope '/' covers all pages including /admin/*
    navigator.serviceWorker.register('/sw.js').then(reg => {
      // Alte /static/sw.js-Registrierung (Scope /static/) entfernen
      navigator.serviceWorker.getRegistrations().then(regs => {
        regs.forEach(r => { if (r !== reg && r.scope.includes('/static/')) r.unregister(); });
      });
    }).catch(() => {});
  });
}


/* ─── Card-Detail Modal: open on any swap into #cardDetailBody ───── */
document.addEventListener('htmx:afterSwap', (e) => {
  if (e.detail && e.detail.target && e.detail.target.id === 'cardDetailBody') {
    document.getElementById('cardDetailModal')?.showModal();
  }
});


/* ─── Offline: block writes with toast, resync on reconnect ────── */
function _showConnToast(msg) {
  const appEl = document.querySelector('[x-data="appState()"]');
  if (appEl && window.Alpine) Alpine.$data(appEl).addToast(msg, 'warn');
}

document.addEventListener('htmx:responseError', (e) => {
  const xhr = e.detail.xhr;
  if (xhr && xhr.status === 403) {
    let msg = 'Diese Aktion ist nicht erlaubt.';
    try { msg = JSON.parse(xhr.responseText).detail || msg; } catch {}
    _showConnToast(msg);
    e.preventDefault();
  } else if (xhr && (xhr.status === 503 || xhr.status === 0) && xhr.getResponseHeader && xhr.getResponseHeader('X-Offline') === '1') {
    _showConnToast('Aktion erfordert Verbindung — du bist offline.');
    e.preventDefault();
  }
});

// Echter Netzabriss/Timeout während des Sendens (kein HTTP-Response, daher
// nicht über htmx:responseError erfassbar) — sonst bleibt der Spinner hängen
// und der Nutzer merkt nicht, dass die Eingabe (z. B. Funkjournal, Lagemeldung)
// nicht angekommen ist (STAB-1).
document.addEventListener('htmx:sendError', (e) => {
  _showConnToast('Keine Verbindung — Aktion wurde nicht gesendet.');
});

document.addEventListener('htmx:timeout', (e) => {
  _showConnToast('Zeitüberschreitung — Aktion wurde eventuell nicht gesendet.');
});

// After SW intercepts a 503 for a mutating fetch (non-HTMX), also show a toast
document.addEventListener('DOMContentLoaded', () => {
  window.addEventListener('online', () => {
    // Board-Seiten gleichen ihre Fragmente ab; andere Seiten tun bewusst nichts.
    window.dispatchEvent(new CustomEvent('ec:resync'));
  });
});


/* ─── Utility: VAPID key conversion ─────────────────────────────── */
function urlBase64ToUint8Array(base64) {
  const padding = '='.repeat((4 - base64.length % 4) % 4);
  const b64 = (base64 + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(b64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

/* ─── CSP-Tranche 1 (Audit A1): Event-Delegation statt inline onclick ─────────
 * Ein document-weiter Listener ersetzt die mechanischen onclick-Klassen
 * (Dialog schliessen/oeffnen, Drucken). Delegation ueberlebt HTMX-Swaps,
 * weil der Listener am document haengt, nicht am Button.
 * Naechste Tranchen: weitere onclick-Klassen -> am Ende Nonce-CSP. */
document.addEventListener('click', function (e) {
  const el = e.target.closest('[data-ec-action]');
  if (!el) return;
  const action = el.getAttribute('data-ec-action');
  if (action === 'close-dialog') {
    const id = el.getAttribute('data-ec-dialog');
    const dlg = id ? document.getElementById(id) : el.closest('dialog');
    if (dlg && typeof dlg.close === 'function') dlg.close();
  } else if (action === 'open-dialog') {
    const id = el.getAttribute('data-ec-dialog');
    const dlg = id ? document.getElementById(id) : null;
    if (dlg && typeof dlg.showModal === 'function') dlg.showModal();
  } else if (action === 'print') {
    window.print();
  }
});
