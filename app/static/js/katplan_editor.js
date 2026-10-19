/* Kapitelbaum und Rich-Text-Felder des Katastrophenplan-Editors. */
(function () {
  'use strict';
  let timer;
  let speichert = false;

  function initialisieren() {
    document.querySelectorAll('[data-kp-quill]').forEach(function (element) {
      if (element.kpQuill || !window.Quill) return;
      const feld = element.closest('.kp-quill-field');
      const input = feld.querySelector('input[name="beschreibung_html"]');
      const quill = new Quill(element, {
        theme: 'snow',
        modules: { toolbar: [
          [{ header: [3, 4, false] }],
          ['bold', 'italic', 'underline'],
          [{ list: 'bullet' }, { list: 'ordered' }],
          ['link'], ['clean']
        ] }
      });
      element.kpQuill = quill;
      quill.clipboard.dangerouslyPasteHTML(feld.querySelector('template').innerHTML);
      function synchronisieren() {
        input.value = quill.getText().trim() ? quill.getSemanticHTML() : '';
      }
      quill.on('text-change', synchronisieren);
      synchronisieren();
      element.kpSynchronisieren = synchronisieren;
    });
    const baum = document.getElementById('kp-baum');
    if (!baum || !window.Sortable) return;
    baum.querySelectorAll('.kp-element-liste, .kp-baum-liste, .kp-unterkapitel').forEach(function (liste) {
      if (liste.kpSortable) return;
      const elemente = liste.classList.contains('kp-element-liste');
      liste.kpSortable = new Sortable(liste, {
        group: elemente ? 'kp-elemente' : undefined,
        draggable: elemente ? '[data-element-id]' : '[data-abschnitt-id]',
        handle: '.kp-griff',
        delayOnTouchOnly: true,
        delay: 200,
        disabled: speichert,
        onEnd: speichern
      });
    });
  }

  function stand(baum) {
    const daten = { abschnitte: [], elemente: [] };
    baum.querySelectorAll('.kp-baum-liste, .kp-unterkapitel').forEach(function (liste) {
      Array.from(liste.children).filter(function (li) {
        return li.hasAttribute('data-abschnitt-id');
      }).forEach(function (li, index) {
        daten.abschnitte.push({ id: Number(li.dataset.abschnittId),
          parent_id: Number(liste.dataset.parentId) || null, sort: (index + 1) * 10 });
      });
    });
    baum.querySelectorAll('.kp-element-liste').forEach(function (liste) {
      Array.from(liste.children).forEach(function (li, index) {
        daten.elemente.push({ id: Number(li.dataset.elementId),
          abschnitt_id: Number(liste.dataset.abschnittId) || null, sort: (index + 1) * 10 });
      });
    });
    return daten;
  }

  async function speichern(event) {
    if (event.from === event.to && event.oldIndex === event.newIndex) return;
    const baum = document.getElementById('kp-baum');
    const listen = Array.from(baum.querySelectorAll('.kp-element-liste, .kp-baum-liste, .kp-unterkapitel'));
    speichert = true;
    listen.forEach(function (liste) { liste.kpSortable.option('disabled', true); });
    const token = baum.querySelector('input[name="_csrf"]').value;
    try {
      const antwort = await fetch('/katastrophenplaene/' + baum.dataset.planId + '/sortierung', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token, 'HX-Request': 'true' },
        body: JSON.stringify(stand(baum))
      });
      if (!antwort.ok) throw new Error('Sortierung konnte nicht gespeichert werden.');
      htmx.swap(baum, await antwort.text(), { swapStyle: 'outerHTML' });
    } catch (fehler) {
      // Die lokale Bewegung zurücknehmen, wenn der Server sie nicht bestätigt hat.
      event.item.remove();
      event.from.insertBefore(event.item, event.from.children[event.oldIndex] || null);
      window.alert(fehler.message || 'Sortierung konnte nicht gespeichert werden.');
    } finally {
      speichert = false;
      listen.forEach(function (liste) { liste.kpSortable.option('disabled', false); });
      initialisieren();
    }
  }

  document.addEventListener('htmx:afterSettle', function () {
    clearTimeout(timer);
    timer = setTimeout(initialisieren, 100);
  });
  function synchronisieren(event) {
    const formular = event.target.closest('form');
    if (!formular) return;
    formular.querySelectorAll('[data-kp-quill]').forEach(function (element) {
      if (!element.kpSynchronisieren) return;
      element.kpSynchronisieren();
      if (event.type === 'htmx:configRequest') {
        event.detail.parameters.beschreibung_html = formular.querySelector('[name="beschreibung_html"]').value;
      }
    });
  }
  document.addEventListener('submit', synchronisieren, true);
  document.addEventListener('htmx:configRequest', synchronisieren);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialisieren);
  else initialisieren();
})();
