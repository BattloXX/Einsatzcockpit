/* ─── Alpine.js Global App State ────────────────────────────────── */
document.addEventListener('alpine:init', () => {
  Alpine.data('appState', () => ({
    toasts: [],
    newIncidentAlert: null,
    mobileMenuOpen: false,
    _ws: null,

    init() {
      this._connectGlobal();
      this._registerPush();
      // Schließe Mobile-Menü bei Navigation (Link-Klick auf Anker innerhalb des Panels)
      document.addEventListener('click', (e) => {
        const link = e.target.closest('.mobile-menu__link');
        if (link) this.mobileMenuOpen = false;
      });
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
      if (detail.is_exercise) {
        this.addToast('[ÜBUNG] Neuer Einsatz: ' + detail.alarm, 'warn');
      } else {
        this.newIncidentAlert = detail;
        try { new Audio('/static/audio/alarm.mp3').play().catch(() => {}); } catch (_) {}
      }
    },

    _connectGlobal() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${location.host}/ws/global`;
      const connect = () => {
        const ws = new WebSocket(url);
        ws.onmessage = (e) => {
          if (e.data === 'pong') return;
          const ev = JSON.parse(e.data);
          if (ev.type === 'incident_created') {
            const customEv = new CustomEvent('incident-created', { detail: ev, bubbles: true });
            document.body.dispatchEvent(customEv);
          }
        };
        ws.onclose = () => setTimeout(connect, 3000);
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
              if (sub) return;
              // Only subscribe if user has granted permission
              if (Notification.permission === 'granted') {
                sw.pushManager.subscribe({
                  userVisibleOnly: true,
                  applicationServerKey: urlBase64ToUint8Array(publicKey),
                }).then(sub => {
                  fetch('/push/subscribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(sub),
                  });
                }).catch(() => {});
              }
            });
          });
        }).catch(() => {});
    },
  }));
});


/* ─── Incident Header (timer + last-update only, no WS) ─────────── */
function headerState(startedAt) {
  return {
    timerDisplay: '00:00',
    _lastUpdate: Date.now(),
    lastUpdateDisplay: '–',
    lastUpdateAgeSec: 0,
    lastUpdateState: 'fresh',

    init() {
      this._startTimer(new Date(startedAt));
      this._startLastUpdate();
      document.addEventListener('board-last-update', (e) => { this._lastUpdate = e.detail; });
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
      this._connectWS(incidentId);
      this._setupKeyboard(incidentId);
    },

    toggleSidebar() {
      this.sidebarOpen = !this.sidebarOpen;
      localStorage.setItem('sidebarOpen', String(this.sidebarOpen));
    },

    _bumpLastUpdate() {
      document.dispatchEvent(new CustomEvent('board-last-update', { detail: Date.now() }));
    },

    _connectWS(id) {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${location.host}/ws/incident/${id}`;
      let firstConnect = true;
      const connect = () => {
        const ws = new WebSocket(url);
        ws.onmessage = (e) => {
          if (e.data === 'pong') return;
          const ev = JSON.parse(e.data);
          this._bumpLastUpdate();
          if (ev.reload_board) {
            const modal = document.getElementById('cardDetailModal');
            if (modal && modal.open) {
              modal.addEventListener('close', () => location.reload(), { once: true });
            } else {
              location.reload();
            }
          }
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
        };
        ws.onclose = () => {
          setTimeout(() => {
            connect();
            if (!firstConnect) location.reload(); // server-wins after reconnect
          }, 3000);
        };
        ws.onopen = () => { firstConnect = false; };
        this._ws = ws;
        setInterval(() => ws.readyState === 1 && ws.send('ping'), 30000);
      };
      connect();
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
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  });
}


/* ─── Card-Detail Modal: open on any swap into #cardDetailBody ───── */
document.addEventListener('htmx:afterSwap', (e) => {
  if (e.detail && e.detail.target && e.detail.target.id === 'cardDetailBody') {
    document.getElementById('cardDetailModal')?.showModal();
  }
});


/* ─── Offline: block writes with toast, reload on reconnect ─────── */
document.addEventListener('htmx:responseError', (e) => {
  const xhr = e.detail.xhr;
  if (xhr && xhr.status === 403) {
    let msg = 'Diese Aktion ist nicht erlaubt.';
    try { msg = JSON.parse(xhr.responseText).detail || msg; } catch {}
    const appEl = document.querySelector('[x-data="appState()"]');
    if (appEl && window.Alpine) Alpine.$data(appEl).addToast(msg, 'warn');
    e.preventDefault();
  } else if (xhr && (xhr.status === 503 || xhr.status === 0) && xhr.getResponseHeader && xhr.getResponseHeader('X-Offline') === '1') {
    const appEl = document.querySelector('[x-data="appState()"]');
    if (appEl && window.Alpine) {
      Alpine.$data(appEl).addToast('Aktion erfordert Verbindung — du bist offline.', 'warn');
    }
    e.preventDefault();
  }
});

// After SW intercepts a 503 for a mutating fetch (non-HTMX), also show a toast
document.addEventListener('DOMContentLoaded', () => {
  // Check if this page load itself was served from SW offline cache
  if (document.getElementById('offline-banner')) return; // banner already in HTML
  window.addEventListener('online', () => {
    // Reconnected: reload to get fresh server state
    location.reload();
  });
});


/* ─── Utility: VAPID key conversion ─────────────────────────────── */
function urlBase64ToUint8Array(base64) {
  const padding = '='.repeat((4 - base64.length % 4) % 4);
  const b64 = (base64 + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(b64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}
