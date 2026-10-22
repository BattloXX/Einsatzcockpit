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
      const antwort = await fetch('/plaene/' + baum.dataset.planId + '/sortierung', {
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

/* Anhaenge: sequenzielle Uploads mit Versions-OOB und zugängliche Fotogalerie. */
(function () {
  'use strict';
  const uploads = new Set();
  document.addEventListener('click', function (event) {
    const button = event.target.closest('[data-kp-kamera-oeffnen]');
    if (button) button.closest('form').querySelector('[data-kp-kamera]').click();
  });
  document.addEventListener('change', function (event) {
    const form = event.target.closest('[data-kp-mehrfach-upload]');
    if (!form) return;
    if (event.target.matches('[data-kp-kamera]')) form.querySelector('[name="datei"]').value = '';
    if (event.target.matches('[name="datei"]')) form.querySelector('[data-kp-kamera]').value = '';
  });
  document.addEventListener('submit', async function (event) {
    const form = event.target.closest('[data-kp-mehrfach-upload]');
    if (!form) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const kamera = form.querySelector('[data-kp-kamera]');
    const dateien = Array.from(kamera.files.length ? kamera.files : form.querySelector('[name="datei"]').files);
    const bereich = form.closest('[data-kp-anhaenge]');
    const zielId = bereich.id;
    if (uploads.has(zielId)) return;
    const grunddaten = new FormData(form);
    if (!dateien.length) {
      bereich.querySelector('[data-kp-upload-status]').textContent = 'Bitte eine Datei auswählen.';
      return;
    }
    const button = form.querySelector('[type="submit"]');
    uploads.add(zielId);
    button.disabled = true;
    function status(text) {
      const aktuell = document.getElementById(zielId);
      if (aktuell) {
        aktuell.querySelector('[data-kp-upload-status]').textContent = text;
        aktuell.querySelectorAll('[data-kp-mehrfach-upload] [type="submit"]').forEach(function (b) {
          b.disabled = uploads.has(zielId);
        });
      }
    }
    try {
      for (let index = 0; index < dateien.length; index += 1) {
        grunddaten.set('datei', dateien[index]);
        status(index + '/' + dateien.length + ' hochgeladen');
        const antwort = await fetch(form.action, {
          method: 'POST', body: grunddaten,
          headers: { 'HX-Request': 'true', 'X-CSRF-Token': grunddaten.get('_csrf') }
        });
        const html = await antwort.text();
        if (html.includes('id="' + zielId + '"')) {
          htmx.swap('#' + zielId, html, { swapStyle: 'outerHTML' });
        } else {
          throw new Error('Upload fehlgeschlagen (' + antwort.status + ').');
        }
        if (!antwort.ok) {
          status(index + '/' + dateien.length + ' hochgeladen. Upload abgebrochen.');
          break;
        }
        status((index + 1) + '/' + dateien.length + ' hochgeladen');
      }
    } catch (fehler) {
      status(fehler.message || 'Upload fehlgeschlagen.');
    } finally {
      uploads.delete(zielId);
      button.disabled = false;
      const aktuell = document.getElementById(zielId);
      if (aktuell) aktuell.querySelectorAll('[data-kp-mehrfach-upload] [type="submit"]').forEach(function (b) {
        b.disabled = false;
      });
    }
  }, true);

  let dialog;
  let fotos = [];
  let index = 0;
  let ausloeser;
  function zeigen(schritt) {
    index = (index + schritt + fotos.length) % fotos.length;
    const link = fotos[index];
    dialog.querySelector('img').src = link.href;
    dialog.querySelector('img').alt = link.querySelector('img').alt;
    dialog.querySelector('p').textContent = link.querySelector('img').alt;
  }
  document.addEventListener('click', function (event) {
    const link = event.target.closest('[data-kp-lightbox]');
    if (!link) return;
    event.preventDefault();
    ausloeser = link;
    fotos = Array.from(link.closest('.kp-fotogalerie').querySelectorAll('[data-kp-lightbox]'));
    index = fotos.indexOf(link);
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.className = 'kp-lightbox';
      dialog.setAttribute('aria-label', 'Fotogalerie');
      dialog.innerHTML = '<button type="button" class="btn" data-schliessen>Schließen (Esc)</button>' +
        '<img alt=""><p></p><div><button type="button" class="btn" data-zurueck aria-label="Vorheriges Foto">←</button> ' +
        '<button type="button" class="btn" data-weiter aria-label="Nächstes Foto">→</button></div>';
      document.body.appendChild(dialog);
      dialog.querySelector('[data-schliessen]').addEventListener('click', function () { dialog.close(); });
      dialog.querySelector('[data-zurueck]').addEventListener('click', function () { zeigen(-1); });
      dialog.querySelector('[data-weiter]').addEventListener('click', function () { zeigen(1); });
      dialog.addEventListener('click', function (e) { if (e.target === dialog) dialog.close(); });
      dialog.addEventListener('close', function () { if (ausloeser) ausloeser.focus(); });
      dialog.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault();
          zeigen(e.key === 'ArrowLeft' ? -1 : 1);
        }
      });
    }
    zeigen(0);
    dialog.showModal();
  });
})();
