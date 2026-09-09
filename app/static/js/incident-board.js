function boardIncidentId() {
  return document.getElementById('kanban')?.dataset.incidentId || '';
}

function openLagebild() {
  const dlg = document.getElementById('lagebildModal');
  if (!dlg) return;
  const comp = Alpine.$data(dlg);
  if (comp && !comp.text) comp.generate();
  dlg.showModal();
}

async function requestAiTaskSuggestions(incidentId) {
  const btn = document.getElementById('ki-tasks-btn');
  if (btn) { btn.disabled = true; btn.textContent = '✨ …'; }
  try {
    const csrf = document.cookie.match(/(?:^|;\s*)ec_csrf=([^;]+)/)?.[1] || '';
    const fd = new FormData();
    fd.append('_csrf', csrf);
    const r = await fetch(`/einsatz/${incidentId}/ki-aufgaben-vorschlaege`, {method: 'POST', body: fd});
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert(d.detail || 'Fehler beim Anfordern der Vorschläge');
    }
    // Board-Reload kommt via WebSocket-Broadcast
  } catch (e) {
    alert(e.message || 'Unbekannter Fehler');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<span class="di-icon">✅</span> KI-Auftragsvorschläge'; }
  }
}

async function regenerateLageHinweise(incidentId) {
  const triggerBtn = document.getElementById('ki-hints-btn');
  if (triggerBtn) { triggerBtn.disabled = true; triggerBtn.innerHTML = '<span class="di-icon">…</span> Hinweise werden geladen …'; }
  try {
    const r = await fetch(`/einsatz/${incidentId}/ki-lagehinweise`, {method: 'POST'});
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert(d.detail || 'Fehler beim Generieren der Hinweise');
    }
    // board reload comes via WebSocket broadcast
  } catch (e) {
    alert(e.message || 'Unbekannter Fehler');
  } finally {
    if (triggerBtn) { triggerBtn.disabled = false; triggerBtn.innerHTML = '<span class="di-icon">📋</span> KI-Hinweise neu laden'; }
  }
}

function aiLagebild(incidentId) {
  function _csrf() {
    return document.cookie.match(/(?:^|;\s*)ec_csrf=([^;]+)/)?.[1] || '';
  }
  return {
    incidentId,
    text: '',
    loading: false,
    error: '',
    async generate() {
      this.loading = true;
      this.error = '';
      this.text = '';
      try {
        const r = await fetch(`/einsatz/${incidentId}/ki-lagebild`, {
          method: 'POST',
          headers: {'X-CSRF-Token': _csrf()},
        });
        const data = await r.json();
        if (!r.ok) { this.error = data.detail || 'Fehler beim Erzeugen'; return; }
        this.text = data.text;
      } catch (e) {
        this.error = e.message || 'Unbekannter Fehler';
      } finally {
        this.loading = false;
      }
    },
    copy() {
      if (this.text) navigator.clipboard?.writeText(this.text);
    },
    async saveToJournal() {
      if (!this.text) return;
      try {
        const r = await fetch(`/einsatz/${incidentId}/ki-lagebild/journal`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-CSRF-Token': _csrf()},
          body: JSON.stringify({text: this.text}),
        });
        if (!r.ok) { this.error = 'Fehler beim Speichern'; return; }
        document.getElementById('lagebildModal')?.close();
        this.text = '';
        this.error = '';
      } catch (e) {
        this.error = e.message || 'Unbekannter Fehler';
      }
    },
  };
}

function _colToast(msg, type) {
  var appEl = document.querySelector('[x-data="appState()"]');
  if (appEl && window.Alpine) Alpine.$data(appEl).addToast(msg, type || 'warn');
}
function deleteColumn(colId, colTitle) {
  if (!confirm('Spalte „' + colTitle + '" wirklich löschen?\n\nNur möglich wenn keine Elemente zugeordnet sind.')) return;
  var csrfToken = document.cookie.match(/(?:^|;\s*)ec_csrf=([^;]+)/)?.[1] || '';
  fetch('/einsatz/' + boardIncidentId() + '/spalten/' + colId, {
    method: 'DELETE',
    headers: {'X-CSRF-Token': csrfToken},
  }).then(function(r) {
    if (!r.ok) {
      r.json().then(function(j) {
        _colToast(j.detail || 'Spalte konnte nicht gelöscht werden.', 'warn');
      }).catch(function() {
        _colToast('Spalte konnte nicht gelöscht werden.', 'warn');
      });
    }
  });
}

function _csrfToken() {
  return document.cookie.match(/(?:^|;\s*)ec_csrf=([^;]+)/)?.[1] || '';
}

function wetterPanelError(el) {
  // Fehler-/Retry-Handling fürs Wetter-Fragment: ohne dies blieb "Lade…" bei
  // einem fehlgeschlagenen Request (z. B. schlechte Verbindung) dauerhaft stehen.
  const span = document.createElement('span');
  span.style.cssText = 'font-size:.75rem;color:var(--text-muted);cursor:pointer;';
  span.textContent = '⚠ Wetterdaten nicht geladen — tippen zum erneuten Versuch';
  span.onclick = () => { if (window.htmx) htmx.trigger(el, 'retry-wetter'); };
  el.innerHTML = '';
  el.appendChild(span);
}

function renameColumn(colId, currentTitle) {
  const newTitle = prompt('Neuer Name für den Abschnitt:', currentTitle);
  if (newTitle === null) return;
  const trimmed = newTitle.trim();
  if (!trimmed || trimmed === currentTitle) return;
  const body = new URLSearchParams({title: trimmed});
  fetch('/einsatz/' + boardIncidentId() + '/spalten/' + colId + '/titel', {
    method: 'POST',
    headers: {'X-CSRF-Token': _csrfToken(), 'Content-Type': 'application/x-www-form-urlencoded'},
    body,
  }).then(function(r) {
    if (!r.ok) { _colToast('Umbenennen fehlgeschlagen.', 'warn'); }
  });
}

function setSectionLeader(colId, memberId) {
  const body = new URLSearchParams({member_id: memberId || ''});
  fetch('/einsatz/' + boardIncidentId() + '/spalten/' + colId + '/abschnittsleiter', {
    method: 'POST',
    headers: {'X-CSRF-Token': _csrfToken(), 'Content-Type': 'application/x-www-form-urlencoded'},
    body,
  }).then(function(r) {
    if (!r.ok) { _colToast('Abschnittsleiter konnte nicht gesetzt werden.', 'warn'); }
  });
}

function promptSectionLeaderFreitext(colId) {
  const name = prompt('Abschnittsleiter (Freitext-Name):');
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) return;
  const body = new URLSearchParams({full_name: trimmed});
  fetch('/einsatz/' + boardIncidentId() + '/spalten/' + colId + '/abschnittsleiter-freitext', {
    method: 'POST',
    headers: {'X-CSRF-Token': _csrfToken(), 'Content-Type': 'application/x-www-form-urlencoded'},
    body,
  }).then(function(r) {
    if (!r.ok) { _colToast('Abschnittsleiter konnte nicht gesetzt werden.', 'warn'); }
  });
}

function openVehicleWizard(colId) {
  // Öffnet den Einheiten-Wizard mit der Ziel-Spalte, aus der der Button geklickt wurde,
  // damit neu hinzugefügte Einheiten direkt in diesem Abschnitt landen (nicht immer "Disponiert").
  document.querySelectorAll('.vehicle-wizard-column-id').forEach(function(el) {
    el.value = colId || '';
  });
  document.getElementById('vehicleWizard').showModal();
}

function openQuickAddTask(colId) {
  const dlg = document.getElementById('quickAddTaskDialog');
  if (!dlg) return;
  const comp = Alpine.$data(dlg);
  if (comp) { comp.colId = colId; comp.vehicleId = ''; }
  dlg.showModal();
}

function openQuickAddTaskForVehicle(vehicleId, colId) {
  const dlg = document.getElementById('quickAddTaskDialog');
  if (!dlg) return;
  const comp = Alpine.$data(dlg);
  if (comp) { comp.colId = colId; comp.vehicleId = String(vehicleId); }
  dlg.showModal();
}

function openQuickAddMsgForVehicle(vehicleId) {
  const dlg = document.getElementById('quickAddMsgDialog');
  if (!dlg) return;
  const comp = Alpine.$data(dlg);
  if (comp) comp.vehicleId = String(vehicleId);
  dlg.showModal();
}

function openPersonWizard(columnId) {
  const dlg = document.getElementById('personWizard');
  if (!dlg) return;
  const comp = Alpine.$data(dlg);
  if (comp) comp.columnId = String(columnId);
  dlg.showModal();
}

function personWizard() {
  return {
    step: 0, gender: 'Unbekannt', group: 'Erwachsen', quickName: '',
    get ageOptions() {
      if (this.group === 'Kind') return ['0–4 Jahre','5–9 Jahre','10–14 Jahre','15–17 Jahre'];
      if (this.group === 'Ältere Person') return ['65–69 Jahre','70–74 Jahre','75–79 Jahre','80+ Jahre'];
      return ['18–24 Jahre','25–34 Jahre','35–44 Jahre','45–54 Jahre','55–64 Jahre'];
    }
  };
}

function vehicleWizard(incidentId) {
  return {
    incidentId,
    query: '',
    gkMemberId: '',
    gkFreeText: '',
    noteText: '',
    suggestions: [],
    selectedId: '',
    async loadSuggestions() {
      try {
        const url = `/einsatz/${this.incidentId}/fahrzeug-vorschlaege` +
                    (this.query ? `?q=${encodeURIComponent(this.query)}` : '');
        const r = await fetch(url, { credentials: 'same-origin' });
        if (!r.ok) { this.suggestions = []; return; }
        const data = await r.json();
        this.suggestions = data.items || [];
        // Falls die bisherige Auswahl in der neuen Vorschlagsliste nicht mehr existiert
        // (z. B. nach Suche), Auswahl zurücksetzen.
        if (this.selectedId && !this.suggestions.some(s => s.id === this.selectedId)) {
          this.selectedId = '';
        }
      } catch (e) { this.suggestions = []; }
    }
  };
}

function lageTicker(hints, aiFlags) {
  return {
    hints,
    aiFlags: aiFlags || [],
    idx: 0,
    _timer: null,
    get currentHint() { return this.hints[this.idx] || ''; },
    get currentIsAI() { return this.aiFlags[this.idx] || false; },
    init() {
      if (this.hints.length > 1) {
        this._timer = setInterval(() => { this.idx = (this.idx + 1) % this.hints.length; }, 8000);
      }
    },
    destroy() {
      if (this._timer) clearInterval(this._timer);
      this._timer = null;
    }
  };
}

// ── Push-Notification Deep-Link: ?open_task=ID oder ?open_msg=ID ────────────
(function () {
  var params = new URLSearchParams(location.search);
  var taskId = params.get('open_task');
  var msgId  = params.get('open_msg');
  if (!taskId && !msgId) return;
  var incId = boardIncidentId();
  var path = taskId
    ? ('/einsatz/' + incId + '/aufgabe/' + taskId + '/detail')
    : ('/einsatz/' + incId + '/meldung/' + msgId + '/detail');
  // URL bereinigen ohne Reload
  history.replaceState(null, '', location.pathname);
  document.addEventListener('DOMContentLoaded', function () {
    var once = function () {
      document.getElementById('cardDetailModal').showModal();
      document.body.removeEventListener('htmx:afterSwap', once);
    };
    document.body.addEventListener('htmx:afterSwap', once);
    htmx.ajax('GET', path, { target: '#cardDetailBody', swap: 'innerHTML' });
  });
})();

// message_due Toast + Ton wird via incidentBoard._connectWS in app.js behandelt

// ── Mobile Lane Select ───────────────────────────────────────────────────────
(function () {
  const LANE_KEY = 'board_active_lane';
  const IS_MOBILE = () => window.matchMedia('(max-width: 900px)').matches;

  const TASK_PRIO    = { open: 0, in_progress: 1, done: 20, cancelled: 30 };
  const MESSAGE_PRIO = { meldung: 0, achtung: 1, hinweis: 2, information: 3,
                         open: 0, in_progress: 1,
                         erledigt: 20, done: 20, storniert: 30, cancelled: 30 };
  const PERSON_PRIO  = { gefunden: 0, versorgt: 1, abtransportiert: 2, verstorben: 3 };

  function cardSortKey(card, lane) {
    const status = (card.dataset.status || '').toLowerCase();
    if (lane === 'tasks')    return TASK_PRIO[status]    ?? 10;
    if (lane === 'messages') return MESSAGE_PRIO[status] ?? 10;
    if (lane === 'persons')  return PERSON_PRIO[status]  ?? 5;
    return 0;
  }

  function mobileSortLane(lane) {
    if (!IS_MOBILE() || lane === 'vehicles' || (lane && lane.startsWith('col-'))) return;
    document.querySelectorAll(`.kanban-col[data-lane="${lane}"]`).forEach(col => {
      const zone = col.querySelector('.kanban-col__body');
      if (!zone) return;
      const cards = Array.from(zone.querySelectorAll(':scope > .card'));
      if (cards.length < 2) return;
      cards.sort((a, b) => cardSortKey(a, lane) - cardSortKey(b, lane));
      cards.forEach(c => zone.appendChild(c));
    });
  }

  function setSelectValue(lane) {
    var sel = document.getElementById('mobile-lane-select');
    if (sel && sel.value !== lane) sel.value = lane;
  }

  function applyLane(lane) {
    if (!lane) return false;
    const tab = document.querySelector(`.board-tab[data-lane="${lane}"]`);
    const hasCol = document.querySelectorAll(`.kanban-col[data-lane="${lane}"]`).length > 0;
    if (!tab && !hasCol) return false;
    document.querySelectorAll('.board-tab').forEach(t => t.classList.toggle('active', t === tab));
    document.querySelectorAll('.kanban-col[data-lane]').forEach(col => {
      if (col.dataset.lane === lane) col.setAttribute('data-lane-active', '');
      else col.removeAttribute('data-lane-active');
    });
    mobileSortLane(lane);
    setSelectValue(lane);
    return true;
  }

  function buildLaneDropdown() {
    var sel = document.getElementById('mobile-lane-select');
    if (!sel) return;
    var laneNames = {
      'tasks': '📋 Aufträge', 'messages': '📨 Meldungen',
      'persons': '👥 Personen'
    };
    var added = new Set();
    var items = [];
    ['tasks', 'messages', 'persons'].forEach(function(lane) {
      var cols = document.querySelectorAll('.kanban-col[data-lane="' + lane + '"]');
      if (!cols.length) return;
      var count = 0;
      cols.forEach(function(col) {
        var c = col.querySelector('.kanban-col__count');
        if (c) count += parseInt(c.textContent) || 0;
      });
      items.push({ lane: lane, label: laneNames[lane], count: count });
      added.add(lane);
    });
    document.querySelectorAll('.kanban-col[data-lane]').forEach(function(col) {
      var lane = col.dataset.lane;
      if (!lane || added.has(lane)) return;
      var titleEl = col.querySelector('.kanban-col__title');
      var label = titleEl ? titleEl.textContent.trim() : lane;
      var c = col.querySelector('.kanban-col__count');
      var count = c ? parseInt(c.textContent) || 0 : 0;
      items.push({ lane: lane, label: label, count: count });
      added.add(lane);
    });
    sel.innerHTML = '';
    items.forEach(function(info) {
      var opt = document.createElement('option');
      opt.value = info.lane;
      opt.textContent = info.label + (info.count ? ' (' + info.count + ')' : '');
      sel.appendChild(opt);
    });
    var cur = 'tasks';
    try { cur = localStorage.getItem(LANE_KEY) || 'tasks'; } catch(e) {}
    sel.value = cur;
  }
  // Für Re-Aufbau nach gezieltem HTMX-Swap der Kopfleiste (enthält #mobile-lane-select)
  window.buildLaneDropdown = buildLaneDropdown;
  // Für Re-Anwendung nach Spalten-Swaps: neu eingefügte .kanban-col-Knoten haben das
  // serverseitige data-lane-active nur auf der Default-Lane "tasks" gesetzt.
  window.reapplyMobileLane = function () {
    var lane = 'tasks';
    try { lane = localStorage.getItem(LANE_KEY) || 'tasks'; } catch (e) { /* noop */ }
    applyLane(lane);
    buildLaneDropdown();
  };

  // Die Kopfleiste wird als OOB-Fragment ersetzt. Unabhaengig vom Ausloeser
  // danach die gespeicherte Mobile-Lane samt Select-Optionen wiederherstellen.
  document.addEventListener('htmx:oobAfterSwap', function (e) {
    var target = e.detail && e.detail.target;
    if ((target && target.id === 'incidentHeaderAlarm') || e.target.id === 'incidentHeaderAlarm') {
      window.reapplyMobileLane();
    }
  });

  // Initial lane setup
  try {
    const saved = localStorage.getItem(LANE_KEY);
    if (!applyLane(saved)) applyLane('tasks');
  } catch (e) { applyLane('tasks'); }

  // Build select options after DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', buildLaneDropdown);
  } else {
    buildLaneDropdown();
  }

  // Native select change handler
  document.addEventListener('change', function (e) {
    if (e.target.id !== 'mobile-lane-select') return;
    const lane = e.target.value;
    if (applyLane(lane)) {
      try { localStorage.setItem(LANE_KEY, lane); } catch (e) { /* noop */ }
    }
  });

  // Click handler for board-tab buttons (desktop fallback)
  document.addEventListener('click', function (e) {
    const tab = e.target.closest('.board-tab');
    if (!tab) return;
    const lane = tab.dataset.lane;
    if (!applyLane(lane)) return;
    try { localStorage.setItem(LANE_KEY, lane); } catch (e) { /* noop */ }
  });
})();

// Explicit exports for Jinja inline handlers and Alpine x-data expressions.
window.openLagebild = openLagebild;
window.requestAiTaskSuggestions = requestAiTaskSuggestions;
window.regenerateLageHinweise = regenerateLageHinweise;
window.aiLagebild = aiLagebild;
window.deleteColumn = deleteColumn;
window.wetterPanelError = wetterPanelError;
window.renameColumn = renameColumn;
window.setSectionLeader = setSectionLeader;
window.promptSectionLeaderFreitext = promptSectionLeaderFreitext;
window.openVehicleWizard = openVehicleWizard;
window.openQuickAddTask = openQuickAddTask;
window.openQuickAddTaskForVehicle = openQuickAddTaskForVehicle;
window.openQuickAddMsgForVehicle = openQuickAddMsgForVehicle;
window.openPersonWizard = openPersonWizard;
window.personWizard = personWizard;
window.vehicleWizard = vehicleWizard;
window.lageTicker = lageTicker;
