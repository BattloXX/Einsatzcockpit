/* ═══════════════════════════════════════════════════════════════════════════
 * sortable-glue.js  –  Drag & Drop für das Kanban-Board
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ⚠️  ACHTUNG – DIESE DATEI NICHT OHNE VOLLSTÄNDIGES LESEN ÄNDERN ⚠️
 *
 * Falsche Änderungen hier führen zu:
 *   • Doppelten POST-Requests (Karte springt zurück)
 *   • Drag & Drop bricht nach erstem Ziehen ab
 *   • Touch/Tablet-Drag funktioniert nicht mehr
 *   • HTMX-Re-Renders zerstören Sortable-Instanzen dauerhaft
 *
 * ARCHITEKTUR – PFLICHTLEKTÜRE VOR JEDER ÄNDERUNG:
 *
 *  1. NUR onEnd verwenden, KEIN onAdd.
 *     onEnd feuert immer auf der SOURCE-Liste (egal ob Reorder oder Cross-Zone).
 *     onAdd würde zusätzlich auf der DESTINATION feuern → doppelter POST.
 *     Jede andere Kombination ist bereits getestet und führt zu Bugs.
 *
 *  2. evt.item.removeAttribute('draggable') NICHT aufrufen.
 *     SortableJS verwaltet das draggable-Attribut intern. Externe Manipulation
 *     bricht den nächsten Drag auf Desktop-Browsern (getesteter Regression-Bug).
 *
 *  3. delayOnTouchOnly: true ist PFLICHT.
 *     Ohne dieses Flag gilt delay:200 auch für Mausklicks → Klick auf Karten
 *     fühlt sich eingefroren an und öffnet Details nicht zuverlässig.
 *
 *  4. handle: '.card' gilt NUR für Spalten-Zonen.
 *     In Fahrzeug-Zonen (sortable-zone--vehicle) kein handle setzen,
 *     damit das ganze Mini-Item-Element draggable ist.
 *
 *  5. Re-Init nach HTMX-Swaps läuft über scheduleInit() mit 150 ms Debounce.
 *     _NICHT_ auf setTimeout(initSortable, 0) oder direkt auf HTMX-Events reagieren.
 *     Schnell aufeinanderfolgende Events (afterSwap + oobAfterSwap + load)
 *     würden sonst initSortable() 3× aufrufen → Race-Condition.
 *
 *  6. Sortable-Instanzen werden über zone._sortableInstance verfolgt.
 *     Bei DOM-Replacement durch HTMX wird das alte Element (inkl. Instanz) GC'd;
 *     das neue Element bekommt beim nächsten scheduleInit() eine frische Instanz.
 *     destroyExistingSortable() nur als Sicherheitsnetz, nicht als Haupt-Cleanup.
 *
 *  7. postMove() sendet keinen CSRF-Header – Endpoint ist per Session-Cookie
 *     rate-limited. Falls CSRF-Middleware ergänzt wird, X-CSRF-Token-Header hier
 *     eintragen (analog zu csrf.js).
 *
 *  8. Tablet-Kompatibilität: fallbackTolerance + touchStartThreshold sind
 *     bewusst gesetzt, um Scroll von Drag zu unterscheiden. Nicht erhöhen
 *     (DnD unbrauchbar) und nicht senken (Scroll bricht ab).
 * ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  // ── Drag-Hover-Tab-Switch (mobile/Tablet Lane-Wechsel während Drag) ──────────
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
      // ⚠️ evt.item.removeAttribute('draggable') NICHT aufrufen –
      //    SortableJS verwaltet dieses Attribut selbst; externe Manipulation
      //    bricht den nächsten Drag (getesteter Bug, nicht entfernen!).

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
          if (kind === 'vehicle') return; // Fahrzeug auf Fahrzeug ergibt keinen Sinn
          postMove(incidentId, { kind, uid, vehicle_id: vehicleId, position });
          return;
        }

        // Drop auf Spalten-Zone — auch vollständige Zone-Reihenfolge mitsenden
        const toColumnId = toZone.closest('[data-col-id]')?.dataset.colId;
        if (!toColumnId) return;

        // Alle Karten in der Ziel-Zone in aktueller DOM-Reihenfolge sammeln
        const zoneCards = Array.from(toZone.querySelectorAll('[data-kind][data-uid]'));
        const zoneOrder = JSON.stringify(
          zoneCards.map(c => ({ kind: c.dataset.kind, id: parseInt(c.dataset.uid, 10) }))
        );
        postMove(incidentId, { kind, uid, column_id: toColumnId, position, zone_order: zoneOrder });
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
      initAll();
    }, 150);
  }


  // ── Haupt-Initialisierung ────────────────────────────────────────────────────
  function initSortable() {
    const incidentId = getIncidentId();
    if (!incidentId) return;
    attachDragTabSwitch();

    const onEnd = makeOnEnd(incidentId);

    // ⚠️ Optionen: Werte hier sind für Desktop UND Tablet/Touch kalibriert.
    //    Änderungen an delay, delayOnTouchOnly, touchStartThreshold oder
    //    fallbackTolerance können DnD auf einer oder beiden Plattformen brechen.
    const commonOpts = {
      group: { name: 'kanban', pull: true, put: true },
      animation: 150,
      ghostClass: 'card--ghost',
      chosenClass: 'card--chosen',
      dragClass: 'card--drag',
      // Delay NUR für Touch – Mausklicks dürfen nicht verzögert werden
      delay: 200,
      delayOnTouchOnly: true,
      // Touch: 8px Threshold unterscheidet Scroll von Drag (Tablet-kalibriert)
      touchStartThreshold: 8,
      // Fallback-Toleranz für Browser ohne nativem DnD (iPad Safari, etc.)
      fallbackTolerance: 5,
      fallbackOnBody: true,
      forceFallback: false, // Nur bei DnD-Problemen auf spez. Geräten auf true setzen
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
        // ⚠️ handle NICHT entfernen – sonst sind interaktive Elemente in Karten nicht klickbar
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

  // ── Spalten-Drag (ganze Kanban-Spalten verschieben) ─────────────────────────
  function initColumnSortable() {
    const kanban = document.getElementById('kanban');
    if (!kanban) return;
    const incidentId = getIncidentId();
    if (!incidentId) return;

    if (kanban._columnSortableInstance) {
      try { kanban._columnSortableInstance.destroy(); } catch (e) { /* noop */ }
      kanban._columnSortableInstance = null;
    }

    kanban._columnSortableInstance = new Sortable(kanban, {
      group:          'kanban-columns',
      animation:      150,
      handle:         '.kanban-col__header',
      draggable:      '.kanban-col[data-col-id]',
      filter:         'button, a, select, input, .kanban-col__add-btn',
      preventOnFilter: true,
      ghostClass:     'kanban-col--ghost',
      chosenClass:    'kanban-col--chosen',
      delay:          150,
      delayOnTouchOnly: false,
      touchStartThreshold: 8,
      fallbackTolerance: 5,
      fallbackOnBody: true,
      onEnd: function () {
        const cols = Array.from(kanban.querySelectorAll('.kanban-col[data-col-id]'));
        const ids = cols.map(function (c) { return parseInt(c.dataset.colId, 10); });
        fetch('/einsatz/' + incidentId + '/spalten/reihenfolge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: 'column_order=' + encodeURIComponent(JSON.stringify(ids)),
          credentials: 'same-origin',
        }).catch(function (err) {
          console.warn('[sortable-glue] Spalten-Reihenfolge fehlgeschlagen:', err);
        });
      },
    });
  }

  // ── Startpunkt ───────────────────────────────────────────────────────────────
  function initAll() {
    initSortable();
    initColumnSortable();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }

  // Re-Init nach HTMX-Swaps – alle Events laufen durch denselben Debounce,
  // sodass nur ein einziger initSortable()-Aufruf erfolgt.
  // ⚠️ Keine weiteren Event-Listener hier ergänzen ohne den Debounce zu erhöhen.
  document.body.addEventListener('htmx:afterSwap',    scheduleInit);
  document.body.addEventListener('htmx:oobAfterSwap', scheduleInit);
  document.body.addEventListener('htmx:load',         scheduleInit);
  document.body.addEventListener('htmx:afterSettle',  scheduleInit);
})();
