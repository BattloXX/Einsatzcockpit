/* Druckdialog für geprüfte Viewer-Pakete; Stationsdruck nutzt bestehende ECP-PrintJobs. */
(function (root) {
  'use strict';
  let dialog, context, prepared, printers = [], submitted, poll, busy = false, generation = 0, requestId;
  const $ = selector => dialog.querySelector(selector);
  const text = value => document.createTextNode(value);
  function message(value) { $('[data-dp-status]').textContent = value; }
  function csrf() { return document.cookie.split('; ').find(c => c.startsWith('ec_csrf='))?.slice(8) || ''; }
  async function api(url, body) {
    const response = await fetch(url, {method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: body ? {'Content-Type': 'application/json', 'X-CSRF-Token': decodeURIComponent(csrf())} : {},
      body: body ? JSON.stringify(body) : undefined});
    let data;
    try { data = await response.json(); } catch (_) { throw Error('Antwort nicht lesbar. Bitte Anmeldung und Verbindung prüfen.'); }
    if (!response.ok) {
      const error = Error(typeof data.detail === 'string' ? data.detail : 'Druckauswahl oder Druckoptionen sind ungültig.');
      error.status = response.status; throw error;
    }
    return data;
  }
  function init() {
    if (dialog) return;
    dialog = document.createElement('dialog'); dialog.id = 'documentPrintDialog'; dialog.className = 'dp';
    dialog.setAttribute('aria-labelledby', 'dp-title');
    dialog.innerHTML = `<form method="dialog" class="dp-close">
        <h2 id="dp-title">Drucken</h2><button aria-label="Druckdialog schließen">${root.ecpIcon('x', {size: 'sm'})}</button></form>
      <p data-dp-name></p>
      <label>Seiten auswählen <select data-dp-mode>
        <option value="current">Aktuelle Seite</option><option value="all">Gesamtes Dokument</option>
        <option value="range">Seitenbereich</option><option value="free">Frei gewählte Seiten</option>
        <option value="multiple">Mehrere ausgewählte Dokumente</option></select></label>
      <label data-dp-pages-wrap hidden>Seiten <input data-dp-pages maxlength="2048" placeholder="z. B. 3–5,9,12–14"></label>
      <div data-dp-documents hidden><p>Dokumente markieren und in der gewünschten Druckreihenfolge anordnen.</p><ol></ol></div>
      <fieldset><legend>Druckziel</legend>
        <label><input type="radio" name="dp-target" value="local" checked> Auf diesem Gerät drucken</label>
        <label><input type="radio" name="dp-target" value="station"> Über Stationsdrucker drucken</label>
      </fieldset>
      <p data-dp-station-note></p>
      <div data-dp-station hidden>
        <label>Stationsdrucker <select data-dp-printer></select></label>
        <label>Kopien <input data-dp-copies type="number" min="1" max="20" value="1" inputmode="numeric"></label>
        <p data-dp-printer-note></p>
      </div>
      <div class="dp-options">
        <label>Papierformat <select data-dp-format><option value="original">Originalformat</option>
          <option value="A4">A4 – vollständig einpassen</option><option value="A3">A3 – vollständig einpassen</option></select></label>
        <label>Ausrichtung <select data-dp-orientation><option value="original">Originalausrichtung</option>
          <option value="portrait">Hochformat</option><option value="landscape">Querformat</option></select></label>
      </div>
      <p data-dp-format-note></p><p data-dp-status role="status" aria-live="polite"></p>
      <div class="dp-links"><a data-dp-open target="_blank" rel="noopener" hidden>PDF öffnen</a>
        <a data-dp-download download hidden>PDF herunterladen</a></div>
      <div class="dp-actions"><button type="button" data-dp-preview>PDF vorbereiten</button>
        <button type="button" data-dp-submit>Drucken</button>
        <button type="button" data-dp-poll hidden>Status prüfen</button></div>`;
    document.body.appendChild(dialog);
    dialog.addEventListener('close', () => { generation++; clearTimeout(poll); });
    dialog.addEventListener('change', () => { invalidate(); controls(); });
    dialog.addEventListener('input', () => { invalidate(); controls(); });
    $('[data-dp-preview]').addEventListener('click', () => execute(false));
    $('[data-dp-submit]').addEventListener('click', () => execute(true));
    $('[data-dp-poll]').addEventListener('click', () => status(generation));
  }
  function invalidate() {
    if (submitted) return;
    prepared = null; requestId = null;
    $('[data-dp-open]').hidden = true; $('[data-dp-download]').hidden = true;
    message('');
  }
  function target() { return $('input[name="dp-target"]:checked').value; }
  function controls() {
    const mode = $('[data-dp-mode]').value, station = target() === 'station';
    $('[data-dp-pages-wrap]').hidden = !['free', 'range'].includes(mode);
    $('[data-dp-documents]').hidden = mode !== 'multiple';
    $('[data-dp-station]').hidden = !station;
    const printer = printers.find(p => p.id === Number($('[data-dp-printer]').value));
    $('[data-dp-printer-note]').textContent = printer?.detail || 'Kein Stationsdrucker verfügbar.';
    const format = $('[data-dp-format]').value;
    $('[data-dp-format-note]').textContent = format === 'original'
      ? 'Die PDF-Seitengrößen bleiben erhalten. Stationsdruck: Inhalt mit 6,35 mm Sicherheitsrand einpassen; Treiber können zusätzlich skalieren. Gemischte Papiergrößen benötigen dort eine ausdrückliche Auswahl A4/A3.'
      : `Formatumwandlung: Alle Seiten werden vollständig auf ${format} eingepasst. Seiten können dabei skaliert werden.`;
    const unsupported = station && format !== 'original' && !printer?.media.includes(format);
    if (unsupported) $('[data-dp-printer-note]').textContent = `Der ausgewählte Drucker unterstützt ${format} laut Meldung nicht.`;
    $('[data-dp-submit]').textContent = station ? 'Druckauftrag senden' : 'Drucken';
    $('[data-dp-submit]').disabled = busy || !!submitted || (station && (!printer?.ready || unsupported));
    $('[data-dp-preview]').disabled = busy || !!submitted;
    dialog.querySelectorAll('input,select,[data-dp-up],[data-dp-down]').forEach(el => { el.disabled = busy || !!submitted; });
    // Nicht freigeschaltete Stationsoption wird durch allgemeines Entsperren nie aktiviert.
    $('input[value="station"]').disabled = busy || !!submitted || !context.stationAllowed;
    $('[data-dp-poll]').hidden = !submitted;
  }
  function documents() {
    const mode = $('[data-dp-mode]').value;
    if (mode === 'multiple') {
      const result = [...$('[data-dp-documents] ol').children].filter(row => row.querySelector('input[type="checkbox"]').checked)
        .map(row => ({url: internal(context.items[Number(row.dataset.index)].url), pages: row.querySelector('input[type="text"]').value}));
      if (!result.length) throw Error('Bitte mindestens ein Dokument markieren.');
      return result;
    }
    return [{url: internal(context.items[context.index].url), pages: mode === 'all' ? 'alle' :
      mode === 'current' ? String(context.page) : $('[data-dp-pages]').value}];
  }
  function internal(value) {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin) throw Error('Nur interne Dokumentreferenzen sind zulässig.');
    return url.pathname + url.search;
  }
  async function prepare(run) {
    if (prepared) return prepared;
    const data = await api('/druck/viewer/vorbereiten', {documents: documents(),
      format: $('[data-dp-format]').value, orientation: $('[data-dp-orientation]').value});
    if (run !== generation || !dialog.open) return null;
    prepared = data;
    $('[data-dp-open]').href = data.pdf_url; $('[data-dp-open]').hidden = false;
    $('[data-dp-download]').href = data.pdf_url + '&download=true'; $('[data-dp-download]').hidden = false;
    message(`${data.page_count} Seiten vorbereitet. ${data.note}`);
    return data;
  }
  async function execute(print) {
    if (busy || submitted) return;
    const run = generation;
    busy = true; controls(); message('Druckfähiges PDF wird zusammengestellt …');
    try {
      const data = await prepare(run);
      if (!data || run !== generation || !dialog.open) return;
      if (print && target() === 'station') {
        requestId ||= crypto.randomUUID();
        const result = await api('/druck/viewer/auftrag', {token: data.token,
          printer_id: Number($('[data-dp-printer]').value), copies: Number($('[data-dp-copies]').value), request_id: requestId});
        if (run !== generation) return;
        submitted = result; showStatus(result); poll = setTimeout(() => status(run), 1200);
      } else if (print) {
        local(data.pdf_url);
      }
    } catch (error) {
      if (run === generation) {
        if (error.status === 410 || (error.status === 409 && error.message.includes('Dokument wurde geändert'))) {
          prepared = null; $('[data-dp-open]').hidden = true; $('[data-dp-download]').hidden = true;
        }
        message(error.message + (requestId ? ' Bei unklarer Übertragung erneut dieselbe Aktion verwenden; es wird kein zweiter Auftrag angelegt.' : ''));
      }
    } finally { if (run === generation) { busy = false; controls(); } }
  }
  function local(url) {
    dialog.querySelectorAll('[data-dp-frame]').forEach(frame => frame.remove());
    const frame = document.createElement('iframe'); frame.dataset.dpFrame = ''; frame.title = 'Druck-PDF';
    frame.className = 'dp-print-frame';
    const run = generation;
    message('Druck-PDF wird geöffnet. Falls kein Systemdruckdialog erscheint: PDF öffnen oder herunterladen und über das Gerät drucken.');
    frame.onload = () => {
      if (run !== generation || !dialog.open) return;
      try {
        if (frame.contentDocument && frame.contentDocument.contentType !== 'application/pdf' &&
            !frame.contentDocument.querySelector('embed[type="application/pdf"],object[type="application/pdf"]')) {
          prepared = null; $('[data-dp-open]').hidden = true; $('[data-dp-download]').hidden = true;
          message('Druck-PDF nicht verfügbar. Bitte „PDF vorbereiten“ erneut verwenden und die Auswahl prüfen.');
          return;
        }
        frame.contentWindow.focus(); frame.contentWindow.print();
        message('Systemdruckdialog angefordert. Drucker und Kopien dort auswählen. Der tatsächliche Druckabschluss ist auf diesem Gerät nicht automatisch prüfbar. Bei Bedarf PDF öffnen oder herunterladen.');
      } catch (_) { message('Der Systemdruckdialog ist hier nicht direkt erreichbar. Bitte PDF öffnen oder herunterladen und über das Gerät drucken.'); }
    };
    frame.src = url; dialog.appendChild(frame);
  }
  function showStatus(data) {
    dialog.dataset.confirmed = data.confirmed ? 'true' : 'false';
    const labels = {queued: 'Eingereiht', sent: 'An Druckdienst übergeben',
      printing: data.phase === 'spooled' ? 'Im Windows-Spooler' : data.label,
      done: 'Gedruckt (vom Spooler abgeschlossen)'};
    const details = [data.phase ? `Phase: ${data.phase}` : '', data.spool_ref ? `Spool-Ref: ${data.spool_ref}` : '',
      data.error ? `Fehler / Hinweis: ${data.error}` : ''].filter(Boolean);
    message(`Auftrag ${data.job_id}: ${labels[data.status] || data.label}${details.length ? ' – ' + details.join(' – ') : ''}`);
  }
  async function status(run) {
    clearTimeout(poll);
    if (!submitted || run !== generation || !dialog.open) return;
    try {
      const result = await api(`/druck/viewer/auftrag/${submitted.job_id}`);
      if (run !== generation) return;
      showStatus(result); submitted = result;
      if (!['done', 'failed', 'uncertain', 'canceled'].includes(result.status)) poll = setTimeout(() => status(run), 1500);
    } catch (_) {
      if (run === generation) message('Druckstatus derzeit nicht abrufbar. Bitte „Status prüfen“ verwenden und keinen neuen Auftrag senden.');
    }
  }
  root.openDocumentPrint = async function (config) {
    init(); generation++; clearTimeout(poll); const run = generation;
    context = {...config, stationAllowed: false}; prepared = null; submitted = null; busy = false; requestId = null;
    dialog.dataset.confirmed = 'false';
    $('[data-dp-name]').textContent = config.items[config.index].title || 'Dokument';
    $('[data-dp-mode]').value = config.selection !== null ? 'free' : 'current';
    $('[data-dp-pages]').value = config.selection || String(config.page);
    $('[data-dp-format]').value = 'original'; $('[data-dp-orientation]').value = 'original';
    $('[data-dp-copies]').value = '1'; $('input[value="local"]').checked = true;
    $('[data-dp-open]').hidden = true; $('[data-dp-download]').hidden = true;
    $('[data-dp-printer]').replaceChildren(); $('[data-dp-station-note]').textContent = 'Stationsdrucker werden geprüft …';
    dialog.querySelectorAll('[data-dp-frame]').forEach(frame => frame.remove());
    const list = $('[data-dp-documents] ol'); list.replaceChildren();
    config.items.forEach((item, index) => {
      const row = document.createElement('li'); row.dataset.index = index;
      const label = document.createElement('label'), check = document.createElement('input'); check.type = 'checkbox';
      check.checked = index === config.index; label.append(check, text(item.title || `Dokument ${index + 1}`));
      const pages = document.createElement('input'); pages.type = 'text'; pages.maxLength = 2048;
      pages.value = config.selections?.[item.url] || 'alle'; pages.setAttribute('aria-label', `Seiten für ${item.title || index + 1}`);
      row.append(label, pages);
      for (const [direction, name, icon] of [[-1, 'Nach oben', 'arrow-up'], [1, 'Nach unten', 'arrow-down']]) {
        const button = document.createElement('button'); button.type = 'button'; button.setAttribute('aria-label', name);
        button.dataset[direction < 0 ? 'dpUp' : 'dpDown'] = ''; button.innerHTML = root.ecpIcon(icon, {size: 'sm'});
        button.addEventListener('click', () => {
          const sibling = direction < 0 ? row.previousElementSibling : row.nextElementSibling;
          if (sibling) direction < 0 ? sibling.before(row) : sibling.after(row);
          invalidate();
        }); row.appendChild(button);
      }
      list.appendChild(row);
    });
    message(''); if (!dialog.open) dialog.showModal(); controls();
    try {
      const data = await api('/druck/viewer/drucker');
      if (run !== generation) return;
      context.stationAllowed = data.allowed; printers = data.printers;
      $('[data-dp-station-note]').textContent = data.note || (printers.some(p => p.ready) ? '' : 'Keine aktuelle Bereitschaft eines Stationsdruckers. Lokaler Druck ist möglich.');
      $('[data-dp-printer]').replaceChildren(...printers.map(p => new Option(`${p.name} – ${p.ready ? 'bereit' : 'offline'}`, String(p.id))));
      const ready = printers.find(p => p.ready); if (ready) $('[data-dp-printer]').value = ready.id;
    } catch (_) { $('[data-dp-station-note]').textContent = 'Stationsdrucker nicht abrufbar. Lokaler Druck ist möglich.'; }
    controls();
  };
  root.documentPrintSupported = function (value) {
    const url = new URL(value, location.href);
    return url.origin === location.origin && (
      /^\/plaene\/(?:anhang\/[\w-]+|quelle\/[\w-]+\/(?:original|seite\/[1-9]\d*\/(?:pdf|bild))|[1-9]\d*\/pdf)$/.test(url.pathname) ||
      /^\/objekt-medien\/(?:dokument\/[1-9]\d*\/original|seite\/[1-9]\d*\/pdf)$/.test(url.pathname) ||
      /^\/einsatz-dokumente\/datei\/[1-9]\d*$/.test(url.pathname) ||
      /^\/nachschlagewerk-cache\/rettungskarten\/[1-9]\d*\/original.pdf$/.test(url.pathname));
  };
})(window);
