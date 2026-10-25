/* Explizite Dokumentübernahme. Berechtigungen und Fassungen bestimmt der Server. */
(function (root) {
  'use strict';
  let dialog, config, generation = 0, busy = false, optionsReady = false;
  const $ = selector => dialog.querySelector(selector);
  function status(message) { $('[data-ed-status]').textContent = message; }
  function internal(value) {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin) throw Error('Nur interne Dokumentquellen sind verfügbar.');
    return url.pathname + url.search;
  }
  async function api(url, body) {
    const csrf = document.cookie.split('; ').find(c => c.startsWith('ec_csrf='))?.slice(8) || '';
    const response = await fetch(url, {method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: body ? {'Content-Type': 'application/json', 'X-CSRF-Token': decodeURIComponent(csrf)} : {},
      body: body ? JSON.stringify(body) : undefined});
    let result;
    try { result = await response.json(); } catch (_) { throw Error('Bitte Anmeldung und Verbindung prüfen.'); }
    if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : 'Bitte Auswahl prüfen.');
    return result;
  }
  root.documentTransferSupported = value => {
    if (document.body.dataset.documentTransfer !== 'true') return false;
    const url = new URL(value, location.href);
    return url.origin === location.origin && (
      /^\/plaene\/(?:anhang\/[\w-]+|quelle\/[\w-]+\/(?:original|seite\/[1-9]\d*\/(?:pdf|bild))|[1-9]\d*\/pdf)$/.test(url.pathname) ||
      /^\/nachschlagewerk-cache\/rettungskarten\/[1-9]\d*\/original.pdf$/.test(url.pathname));
  };
  function init() {
    if (dialog) return;
    dialog = document.createElement('dialog'); dialog.className = 'dp'; dialog.id = 'documentTransferDialog';
    dialog.setAttribute('aria-labelledby', 'ed-title');
    dialog.innerHTML = `<form method="dialog" class="dp-close"><h2 id="ed-title">Zum Einsatz hinzufügen</h2>
      <button aria-label="Übernahmedialog schließen">${root.ecpIcon('x', {size: 'sm'})}</button></form>
      <p>Die freigegebene Quelle wird als unveränderliche Einsatzfassung übernommen.</p>
      <label>Umfang <select data-ed-mode><option value="selection">Ausgewählte Seiten</option>
        <option value="all">Gesamtes Dokument</option><option value="multiple">Mehrere Dokumente / Sammlung</option></select></label>
      <label data-ed-pages-label>Seiten <input data-ed-pages maxlength="2048" placeholder="z. B. 4–6"></label>
      <div data-ed-documents hidden></div>
      <label>Aktiver Einsatz <select data-ed-target></select></label>
      <label>Auftrag (optional) <select data-ed-order></select></label>
      <fieldset data-ed-recipients><legend>Einheiten (optional)</legend><div data-ed-units></div>
        <label><input type="checkbox" data-ed-order-share>Für alle aktuell und künftig aktiven Einheiten des ausgewählten Auftrags freigeben</label>
        <label><input type="checkbox" data-ed-confidential>Ich bestätige ausdrücklich die Weitergabe eventuell vertraulicher Unterlagen an diese Empfänger.</label>
      </fieldset>
      <p>Ohne ausdrückliche Freigabe erscheint das Dokument ausschließlich in der Dokumentenverwaltung des Einsatzes.</p>
      <p data-ed-status role="status" aria-live="polite"></p>
      <div class="dp-actions"><button type="button" data-ed-submit>Zum Einsatz hinzufügen</button>
      <a data-ed-result hidden>Dokumente im Einsatz öffnen</a></div>`;
    document.body.append(dialog);
    dialog.addEventListener('close', () => { generation++; });
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    $('[data-ed-mode]').addEventListener('change', controls);
    $('[data-ed-target]').addEventListener('change', loadOptions);
    $('[data-ed-submit]').addEventListener('click', submit);
  }
  function controls() {
    const mode = $('[data-ed-mode]').value;
    $('[data-ed-pages-label]').hidden = mode !== 'selection';
    $('[data-ed-documents]').hidden = mode !== 'multiple';
    $('[data-ed-submit]').disabled = busy || !optionsReady;
    dialog.querySelectorAll('input,select').forEach(el => { el.disabled = busy; });
    $('.dp-close button').disabled = busy;
  }
  async function loadOptions() {
    const run = ++generation;
    optionsReady = false; controls();
    $('[data-ed-order-share]').checked = false; $('[data-ed-confidential]').checked = false;
    $('[data-ed-order]').replaceChildren(new Option('Keiner', ''));
    $('[data-ed-units]').replaceChildren();
    const value = $('[data-ed-target]').value;
    if (!value) { status('Kein aktiver Einsatz verfügbar.'); return; }
    try {
      const data = await api('/einsatz-dokumente/optionen/' + value);
      if (run !== generation) return;
      $('[data-ed-order]').append(...data.auftraege.map(o => new Option(o.titel, o.id)));
      $('[data-ed-recipients]').hidden = !value.startsWith('lage/');
      data.einheiten.forEach(unit => {
        const label = document.createElement('label'), check = document.createElement('input');
        check.type = 'checkbox'; check.value = unit.id; check.dataset.edUnit = '';
        label.append(check, document.createTextNode(unit.titel)); $('[data-ed-units]').append(label);
      });
      optionsReady = true; status(''); controls();
    } catch (error) { if (run === generation) { status(error.message); controls(); } }
  }
  async function submit() {
    if (busy || !optionsReady) return;
    const run = generation;
    busy = true; controls(); status('Dokumentfassung wird gesichert …');
    try {
      const mode = $('[data-ed-mode]').value;
      const documents = mode === 'multiple'
        ? [...$('[data-ed-documents]').children].filter(row => row.querySelector('[type=checkbox]').checked)
          .map(row => ({url: internal(config.items[Number(row.dataset.index)].url), pages: row.querySelector('[type=text]').value}))
        : [{url: internal(config.items[config.index].url), pages: mode === 'all' ? 'alle' : $('[data-ed-pages]').value}];
      if (!documents.length) throw Error('Bitte mindestens ein Dokument markieren.');
      const [art, ident] = $('[data-ed-target]').value.split('/');
      const result = await api('/einsatz-dokumente/uebernehmen', {documents, art, einsatz_id: Number(ident),
        auftrag_id: Number($('[data-ed-order]').value) || null,
        einheit_ids: [...dialog.querySelectorAll('[data-ed-unit]:checked')].map(el => Number(el.value)),
        auftrag_freigeben: $('[data-ed-order-share]').checked,
        vertraulich_bestaetigt: $('[data-ed-confidential]').checked});
      if (run !== generation) return;
      optionsReady = false; status(`${result.ids.length} Dokument(e) übernommen.`);
      $('[data-ed-result]').href = result.url; $('[data-ed-result]').hidden = false;
      document.body.dispatchEvent(new CustomEvent('dokumente-aktualisiert', {bubbles: true}));
    } catch (error) { if (run === generation) status(error.message); }
    finally { if (run === generation) { busy = false; controls(); } }
  }
  root.openDocumentTransfer = async function (value) {
    init();
    // Bei noch laufender Übertragung keine zweite Übernahme auslösen.
    if (busy) { if (!dialog.open) dialog.showModal(); return; }
    const source = value.items[value.index];
    const items = value.items.filter(i => root.documentTransferSupported(i.url));
    config = {...value, items, index: Math.max(0, items.indexOf(source))};
    $('[data-ed-result]').hidden = true; optionsReady = false;
    $('[data-ed-mode]').value = value.collection ? 'multiple' : 'selection';
    $('[data-ed-pages]').value = value.pages || 'alle';
    const list = $('[data-ed-documents]'); list.replaceChildren();
    items.forEach((item, index) => {
      const row = document.createElement('div'); row.dataset.index = index;
      const label = document.createElement('label'), check = document.createElement('input'); check.type = 'checkbox';
      check.checked = !!value.collection || index === config.index;
      label.append(check, document.createTextNode(item.title || 'Dokument'));
      const pages = document.createElement('input'); pages.type = 'text'; pages.maxLength = 2048;
      pages.value = index === config.index ? value.pages || 'alle' : 'alle';
      pages.setAttribute('aria-label', `Seiten für ${item.title || 'Dokument'}`);
      row.append(label, pages); list.append(row);
    });
    $('[data-ed-target]').replaceChildren(); status('Aktive Einsätze werden geladen …'); controls();
    if (!dialog.open) dialog.showModal();
    const run = ++generation;
    try {
      const data = await api('/einsatz-dokumente/ziele');
      if (run !== generation) return;
      $('[data-ed-target]').replaceChildren(...data.ziele.map(z => new Option(z.titel, `${z.art}/${z.id}`)));
      await loadOptions();
    } catch (error) { if (run === generation) status(error.message); }
  };
  document.addEventListener('click', async event => {
    const print = event.target.closest('[data-ed-print]');
    if (print) {
      await root.openDocumentViewer([{url: print.dataset.edPrint, title: print.dataset.edTitle,
        selectionUrl: print.dataset.edPrint}], 0);
      document.querySelector('[data-dv-action="select-all"]')?.click();
      document.querySelector('[data-dv-action="print"]')?.click();
    }
    const collection = event.target.closest('[data-ed-collection]');
    if (collection) {
      collection.disabled = true;
      try {
        const data = await api(collection.dataset.edCollection);
        await root.openDocumentTransfer({items: data.documents, index: 0, pages: 'alle', collection: true});
      } catch (error) { root.alert(error.message); }
      finally { collection.disabled = false; }
    }
  });
})(window);
