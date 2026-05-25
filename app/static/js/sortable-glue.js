/* ─── Sortable Glue – DnD for Kanban Board ───────────────────────── */

(function () {
  'use strict';

  // Drag-Hover-Tab-Switch state (mobile lane switching during drag)
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

  function initSortable() {
    const incidentId = getIncidentId();
    if (!incidentId) return;
    attachDragTabSwitch();

    // Column-level zones (vehicles + free tasks)
    document.querySelectorAll('.kanban-col__body.sortable-zone:not(.sortable-zone--vehicle)').forEach(zone => {
      destroyExistingSortable(zone);
      const columnId = zone.closest('[data-col-id]')?.dataset.colId;
      if (!columnId) return;

      zone._sortableInstance = new Sortable(zone, {
        group: { name: 'kanban', pull: true, put: true },
        animation: 150,
        handle: '.card',
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
        onEnd(evt) {
          _dragging = false;
          _hoverTabId = null;
          document.body.classList.remove('dnd-active');
          evt.item.removeAttribute('draggable');
          const card = evt.item;
          const kind = card.dataset.kind;
          const uid = card.dataset.uid;
          const toZone = evt.to;
          const toColumnId = toZone.closest('[data-col-id]')?.dataset.colId;
          const toVehicleId = toZone.dataset.vehicleId;
          const position = evt.newIndex;

          if (!uid || !kind) return;

          const payload = { kind, uid, position };
          if (toVehicleId) {
            payload.vehicle_id = toVehicleId;
          } else if (toColumnId) {
            payload.column_id = toColumnId;
          } else {
            return;
          }

          postMove(incidentId, payload);
        },
      });
    });

    // Vehicle-level task drop zones
    document.querySelectorAll('.sortable-zone--vehicle').forEach(zone => {
      destroyExistingSortable(zone);
      const vehicleId = zone.dataset.vehicleId;
      if (!vehicleId) return;

      zone._sortableInstance = new Sortable(zone, {
        group: { name: 'kanban', pull: false, put: true },
        animation: 150,
        delay: 150,
        touchStartThreshold: 4,
        preventOnFilter: false,
        filter: 'select,input,button,.task-check,a,label',
        onStart() {
          _dragging = true;
          document.body.classList.add('dnd-active');
        },
        onAdd(evt) {
          _dragging = false;
          _hoverTabId = null;
          document.body.classList.remove('dnd-active');
          const card = evt.item;
          const uid = card.dataset.uid;
          const kind = card.dataset.kind;
          if (!['task', 'message', 'person'].includes(kind) || !uid) return;
          postMove(incidentId, { kind, uid, vehicle_id: vehicleId, position: evt.newIndex });
        },
        onEnd() {
          _dragging = false;
          _hoverTabId = null;
          document.body.classList.remove('dnd-active');
        },
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSortable);
  } else {
    initSortable();
  }

  // Re-init after HTMX swaps (board reload, OOB swaps, htmx:load)
  document.body.addEventListener('htmx:afterSwap', () => setTimeout(initSortable, 50));
  document.body.addEventListener('htmx:oobAfterSwap', () => setTimeout(initSortable, 50));
  document.body.addEventListener('htmx:load', () => setTimeout(initSortable, 50));
})();
