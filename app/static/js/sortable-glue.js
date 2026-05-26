/* ─── Sortable Glue – DnD für das Kanban-Board ──────────────────────────────
 *
 * ARCHITEKTUR – BITTE VOR ÄNDERUNGEN LESEN:
 *
 *  1. NUR onEnd verwenden, KEIN onAdd.
 *     onEnd feuert immer auf der SOURCE-Liste (egal ob Reorder oder Cross-Zone).
 *     onAdd würde zusätzlich auf der DESTINATION feuern → doppelter POST.
 *
 *  2. handle: '.card' gilt NUR für Spalten-Zonen.
 *     In Fahrzeug-Zonen (sortable-zone--vehicle) kein handle setzen,
 *     damit das ganze Mini-Item-Element draggable ist.
 *
 *  3. Re-Init nach HTMX-Swaps läuft über scheduleInit() mit 100 ms Debounce,
 *     damit schnell aufeinander folgende HTMX-Events nicht mehrfach initialisieren.
 *     _NICHT_ auf setTimeout(initSortable, 0) oder direkt reagieren.
 *
 *  4. Neue Kartentypen (kind) müssen in postMove() als eigener case ergänzt
 *     werden (analog 'task', 'message', 'vehicle', 'person').
 *
 *  5. Sortable-Instanzen werden über zone._sortableInstance verfolgt.
 *     Bei DOM-Replacement durch HTMX wird das alte Element (incl. Instanz) GC'd;
 *     das neue Element bekommt beim nächsten scheduleInit() eine frische Instanz.
 *
 *  6. postMove() sendet keinen CSRF-Header – der Endpoint ist rate-limited per
 *     Session-Cookie. Falls CSRF-Middleware eingebaut wird, muss hier ein
 *     X-CSRF-Token-Header ergänzt werden.
 */

(function () {
  'use strict';

  // ── Drag-Hover-Tab-Switch (mobile Lane-Wechsel während Drag) ────────────────
  let _dragging = false;
  let _hoverTabId = null;
  let _hoverStart = 0;
  const TAB_HOLD_MS = 500;

  function getIncidentId() {
    const el = document.getElementById('kanban') || document.querySelector('[data-incident-id]');
    return el ? (el.dataset.incidentId || null) : null;
  }

  function postMove(incidentId, payload) {
    const body = new URLSearchParams(payload);
    return fetch(`/einsatz/${incidentId}/karte/verschieben`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
      credentials: 'same-origin',
    }).catch(function (err) {
      console.warn('[sortable-glue] postMove fehlgeschlagen:', err);
    });
  }

  function onPointerMoveForTabSwitch(e) {
    if (!_dragging) return;
    const t = e.touches ? e.touches[0] : e;
    if (!t || t.clientX === undefined) return;
    const el = document.elementFromPoint(t.clientX, t.clientY);
    const tab = el ? el.closest('.board-tab') : null;
    if (!tab) { _hoverTabId = null; return; }
    if (tab.dataset.lane !== _hoverTabId) {
      _hoverTabId = tab.dataset.lane;
      _hoverStart = Date.now();
    } else if (Date.now() - _hoverStart > TAB_HOLD_MS && !tab.classList.contains('active')) {
      tab.click();
      _hoverStart = Date.now() + 999999;
    }
  }

  function attachDragTabSwitch() {
    if (window._dndTabSwitchAttached) return;
    window._dndTabSwitchAttached = true;
    document.addEventListener('touchmove', onPointerMoveForTabSwitch, { passive: true });
    document.addEventListener('pointermove', onPointerMoveForTabSwitch);
  }

  function destroyExistingSortable(zone) {
    if (zone._sortableInstance) {
      try { zone._sortableInstance.destroy(); } catch (e) { /* noop */ }
      zone._sortableInstance = null;
    }
  }

  // ── Einheitlicher onEnd-Handler für Spalten- UND Fahrzeug-Zonen ─────────────
  function makeOnEnd(incidentId) {
    return function (evt) {
      _dragging = false;
      _hoverTabId = null;
      document.body.classList.remove('dnd-active');
      evt.item.removeAttribute('draggable');

      try {
        const card = evt.item;
        const kind = card.dataset.kind;
        const uid = card.dataset.uid;
        if (!uid || !kind) return;

        // Reorder ohne Positionsänderung → nichts tun
        if (evt.from === evt.to && evt.oldIndex === evt.newIndex) return;

        const toZone = evt.to;
        const position = evt.newIndex;

        // Drop auf Fahrzeug-Zone (innerhalb einer Fahrzeug-Karte)
        if (toZone.classList.contains('sortable-zone--vehicle')) {
          const vehicleId = toZone.dataset.vehicleId;
          if (!vehicleId) return;
          // Ein Fahrzeug auf ein anderes Fahrzeug zu droppen ergibt keinen Sinn
          if (kind === 'vehicle') return;
          postMove(incidentId, { kind, uid, vehicle_id: vehicleId, position });
          return;
        }

        // Drop auf Spalten-Zone
        const toColumnId = toZone.closest('[data-col-id]')?.dataset.colId;
        if (!toColumnId) return;
        postMove(incidentId, { kind, uid, column_id: toColumnId, position });
      } catch (err) {
        console.warn('[sortable-glue] onEnd-Fehler:', err);
      }
    };
  }

  // ── Debounce-Helper ──────────────────────────────────────────────────────────
  // Verhindert, dass schnell aufeinander folgende HTMX-Events (afterSwap,
  // oobAfterSwap, load) mehrfach initSortable() aufrufen.
  let _initTimer = null;
  function scheduleInit() {
    if (_initTimer) clearTimeout(_initTimer);
    _initTimer = setTimeout(function () {
      _initTimer = null;
      initSortable();
    }, 100);
  }

  // ── Haupt-Initialisierung ────────────────────────────────────────────────────
  function initSortable() {
    const incidentId = getIncidentId();
    if (!incidentId) return;
    attachDragTabSwitch();

    const onEnd = makeOnEnd(incidentId);
    const commonOpts = {
      group: { name: 'kanban', pull: true, put: true },
      animation: 150,
      ghostClass: 'card--ghost',
      chosenClass: 'card--chosen',
      dragClass: 'card--drag',
      delay: 150,
      touchStartThreshold: 4,
      preventOnFilter: false,
      filter: 'select,input,button,.task-check,a,label',
      onStart() {
        _dragging = true;
        document.body.classList.add('dnd-active');
      },
      onEnd,
    };

    // Spalten-Zonen (Fahrzeuge + freie Aufträge + Meldungen + Personen)
    document.querySelectorAll('.kanban-col__body.sortable-zone:not(.sortable-zone--vehicle)').forEach(function (zone) {
      destroyExistingSortable(zone);
      const columnId = zone.closest('[data-col-id]')?.dataset.colId;
      if (!columnId) return;

      zone._sortableInstance = new Sortable(zone, {
        ...commonOpts,
        // Spalte: ganze .card als Drag-Griff (Vehicle/Task/Message/Person-Karten)
        handle: '.card',
      });
    });

    // Fahrzeug-Drop-Zonen (innerhalb von Fahrzeug-Karten)
    document.querySelectorAll('.sortable-zone--vehicle').forEach(function (zone) {
      destroyExistingSortable(zone);
      const vehicleId = zone.dataset.vehicleId;
      if (!vehicleId) return;

      zone._sortableInstance = new Sortable(zone, {
        ...commonOpts,
        // Mini-Items im Fahrzeug haben keine .card-Klasse — kein handle setzen,
        // damit das ganze Mini-Item-Element draggable ist.
        handle: undefined,
      });
    });
  }

  // ── Startpunkt ───────────────────────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSortable);
  } else {
    initSortable();
  }

  // Re-Init nach HTMX-Swaps – alle drei Events laufen durch denselben
  // Debounce, sodass nur ein einziger initSortable()-Aufruf erfolgt.
  document.body.addEventListener('htmx:afterSwap', scheduleInit);
  document.body.addEventListener('htmx:oobAfterSwap', scheduleInit);
  document.body.addEventListener('htmx:load', scheduleInit);
  document.body.addEventListener('htmx:afterSettle', scheduleInit);
})();
