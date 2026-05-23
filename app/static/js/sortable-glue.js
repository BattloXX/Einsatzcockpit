/* ─── Sortable Glue – DnD for Kanban Board ───────────────────────── */

(function () {
  'use strict';

  // incidentId is read from the board wrapper attribute set in board.html
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

  function initSortable() {
    const incidentId = getIncidentId();
    if (!incidentId) return;

    // Column-level zones (vehicles + free tasks)
    document.querySelectorAll('.kanban-col__body.sortable-zone:not(.sortable-zone--vehicle)').forEach(zone => {
      const columnId = zone.closest('[data-col-id]')?.dataset.colId;
      if (!columnId) return;

      new Sortable(zone, {
        group: { name: 'kanban', pull: true, put: true },
        animation: 150,
        handle: '.card',
        ghostClass: 'card--ghost',
        chosenClass: 'card--chosen',
        dragClass: 'card--drag',
        // Prevent drag when clicking on interactive elements
        filter: 'select,input,button,.task-check',
        onEnd(evt) {
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
      const vehicleId = zone.dataset.vehicleId;
      if (!vehicleId) return;

      new Sortable(zone, {
        group: { name: 'kanban', pull: false, put: true },
        animation: 150,
        filter: 'select,input,button,.task-check',
        onAdd(evt) {
          const card = evt.item;
          const uid = card.dataset.uid;
          const kind = card.dataset.kind;
          if (kind !== 'task' || !uid) return;
          postMove(incidentId, { kind: 'task', uid, vehicle_id: vehicleId, position: evt.newIndex });
        },
      });
    });
  }

  // Init after DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSortable);
  } else {
    initSortable();
  }

  // Re-init after HTMX swaps (board reload)
  document.body.addEventListener('htmx:afterSwap', () => {
    setTimeout(initSortable, 50);
  });
})();
