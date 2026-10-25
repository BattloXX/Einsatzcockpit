/* Gemeinsamer Viewer: keine PDF-Browserplugins, keine dauerhafte Speicherung von Dokumentdaten. */
(function (root) {
  'use strict';
  function parsePages(value, count) {
    if (String(value).length > 2048) throw Error('Ungültige Seitenauswahl.');
    let text = String(value).trim().replace(/^(?:seiten?|s\.)\s*/i, '').replace(/\bund\b/gi, ',')
      .replace(/\s/g, '').replace(/[–—]/g, '-');
    if (text.length > 2048 || !Number.isInteger(count) || count < 1) throw Error('Ungültige Seitenauswahl.');
    if (/^(alle|all|\*)$/i.test(text)) text = `1-${count}`;
    const result = new Set();
    for (const part of text.split(',')) {
      const match = /^([1-9]\d*)(?:-([1-9]\d*))?$/.exec(part);
      if (!match) throw Error('Seiten z. B. als 2, 5–8 oder 3–5, 12–15 eingeben.');
      const first = Number(match[1]), last = Number(match[2] || match[1]);
      if (!Number.isSafeInteger(last) || first > last || last > count) throw Error(`Bitte Seiten zwischen 1 und ${count} auswählen.`);
      if (last - first + 1 > 2000 || result.size + last - first + 1 > 4000) throw Error('Höchstens 2000 Seiten auf einmal auswählen.');
      for (let page = first; page <= last; page++) result.add(page);
    }
    if (result.size > 2000) throw Error('Höchstens 2000 Seiten auf einmal auswählen.');
    return [...result].sort((a, b) => a - b);
  }
  function compressPages(pages) {
    const ranges = [];
    for (let i = 0; i < pages.length; i++) {
      const start = pages[i];
      while (i + 1 < pages.length && pages[i + 1] === pages[i] + 1) i++;
      ranges.push(start === pages[i] ? String(start) : `${start}-${pages[i]}`);
    }
    return ranges.join(',');
  }
  if (typeof module !== 'undefined') module.exports = {parsePages, compressPages};
  if (!root.document) return;
  let library, viewer;
  const base = '/static/vendor/pdfjs/6.4.299/';
  function pdfLibrary() {
    if (!library) library = import(base + 'pdf.min.js').then(pdf => {
      pdf.GlobalWorkerOptions.workerSrc = base + 'pdf.worker.min.js';
      return pdf;
    }).catch(error => { library = null; throw error; });
    return library;
  }
  function localUrl(value) {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)) throw Error('Dieses Dokument kann nur über einen geschützten Dateilink geöffnet werden.');
    return url;
  }
  function icon(name) { return root.ecpIcon(name, {size: 'sm'}); }
  function button(action, label, name) {
    return `<button type="button" data-dv-action="${action}" aria-label="${label}" title="${label}">${icon(name)}</button>`;
  }
  class DocumentViewer {
    constructor() {
      this.dialog = document.createElement('dialog');
      this.dialog.id = 'documentViewer';
      this.dialog.className = 'dv';
      this.dialog.setAttribute('aria-labelledby', 'dv-name');
      this.dialog.innerHTML = `
        <header class="dv-toolbar">
          <div class="dv-heading"><h2 id="dv-name"></h2><span data-dv-position></span></div>
          <div class="dv-controls">
            ${button('document-prev', 'Vorheriges Dokument', 'chevron-left')}
            ${button('document-next', 'Nächstes Dokument', 'chevron-right')}
            ${button('documents', 'Dokumentenliste anzeigen', 'files')}
            ${button('page-prev', 'Vorherige Seite', 'arrow-left')}
            <label class="dv-page">Seite <input data-dv-page type="number" min="1" inputmode="numeric" aria-label="Sprung zu Seite"> <span data-dv-count></span></label>
            ${button('page-next', 'Nächste Seite', 'arrow-right')}
            ${button('print', 'Drucken', 'printer')}
            <button type="button" data-dv-action="add" disabled title="Einsatzübernahme für diese Auswahl ist noch nicht verfügbar." aria-label="Zum Einsatz hinzufügen">${icon('copy')}</button>
            ${button('close', 'Dokumentenviewer schließen', 'x')}
          </div>
        </header>
        <div class="dv-tools">
          ${button('zoom-out', 'Verkleinern', 'minus')}<output data-dv-zoom>100 %</output>
          ${button('zoom-in', 'Vergrößern', 'plus')}${button('fit', 'Seite einpassen', 'maximize')}
          ${button('rotate', '90 Grad drehen', 'rotate')}${button('fullscreen', 'Vollbild umschalten', 'arrows-maximize')}
          ${button('thumbnails', 'Miniaturseiten anzeigen', 'layout-grid')}
          <a data-dv-original target="_blank" rel="noopener">Original öffnen</a>
          <a data-dv-download download>Download</a><a data-dv-edit hidden>Bearbeiten</a>
          <details class="dv-selection"><summary>Seitenauswahl</summary>
            <div class="dv-selection-fields">
              <button type="button" data-dv-action="select-current">Aktuelle Seite</button>
              <button type="button" data-dv-action="select-all">Gesamtes Dokument</button>
              <label>Seiten <input data-dv-selection maxlength="2048" placeholder="z. B. 3–5, 12–15"></label>
              <span data-dv-selected aria-live="polite"></span>
              <a data-dv-excerpt target="_blank" rel="noopener" hidden>Auswahl als PDF öffnen</a>
            </div>
          </details>
        </div>
        <p class="dv-status" data-dv-status role="status" aria-live="polite"></p>
        <div class="dv-body">
          <aside class="dv-documents" aria-label="Dokumentenliste" hidden><ol></ol></aside>
          <aside class="dv-thumbnails" aria-label="Miniaturseiten" hidden></aside>
          <main class="dv-stage" tabindex="0" aria-label="Dokumentseite"><div class="dv-paper"></div></main>
        </div>`;
      this.shell = document.createElement('div'); this.shell.className = 'dv-shell';
      this.shell.append(...this.dialog.childNodes); this.dialog.appendChild(this.shell);
      document.body.appendChild(this.dialog);
      this.$ = selector => this.dialog.querySelector(selector);
      this.stage = this.$('.dv-stage');
      this.paper = this.$('.dv-paper');
      this.states = new Map();
      this.generation = 0;
      this.renderGeneration = 0;
      this.dialog.addEventListener('click', event => {
        const action = event.target.closest('[data-dv-action]');
        if (action) this.action(action.dataset.dvAction);
      });
      this.$('[data-dv-page]').addEventListener('change', event => this.goPage(Number(event.target.value)));
      this.$('[data-dv-page]').addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); this.goPage(Number(event.target.value)); }
      });
      this.$('[data-dv-selection]').addEventListener('input', event => {
        this.state.selection = event.target.value; this.updateSelection();
      });
      this.dialog.addEventListener('cancel', event => { event.preventDefault(); this.close(); });
      this.dialog.addEventListener('close', () => { if (!this.dialog.open) this.cleanup(); });
      this.dialog.addEventListener('keydown', event => {
        if (/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
        const step = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
        if (step) { event.preventDefault(); event.altKey ? this.load(this.index + step) : this.goPage(this.state.page + step); }
      });
      this.dialog.addEventListener('fullscreenchange', () => this.render());
      this.resize = new ResizeObserver(() => { clearTimeout(this.resizeTimer); this.resizeTimer = setTimeout(() => this.render(), 100); });
      this.resize.observe(this.stage);
      this.touch();
    }
    status(message) { this.$('[data-dv-status]').textContent = message; }
    async open(items, index = 0, options = {}) {
      this.cleanup();
      this.owner = document.body.dataset.kontaktCacheOwner || 'session';
      this.items = items.map(item => ({...item, url: localUrl(item.url).href}));
      if (!this.items.length) return;
      this.options = options;
      this.opener = document.activeElement;
      const list = this.$('.dv-documents ol'); list.replaceChildren();
      this.items.forEach((item, i) => {
        const li = document.createElement('li'), link = document.createElement('button');
        link.type = 'button'; link.textContent = `${i + 1}. ${item.title || 'Dokument'}`;
        link.addEventListener('click', () => this.load(i)); li.appendChild(link); list.appendChild(li);
      });
      this.$('.dv-documents').hidden = true;
      this.$('.dv-thumbnails').hidden = true;
      if (!this.dialog.open) this.dialog.showModal();
      await this.load(Math.max(0, Math.min(index, this.items.length - 1)));
    }
    stopDocument() {
      this.renderGeneration++;
      this.renderTask?.cancel(); this.renderTask = null;
      this.observer?.disconnect();
      if (this.loading) { this.loading.destroy().catch(() => {}); this.loading = null; }
      else if (this.pdf) this.pdf.destroy().catch(() => {});
      this.pdf = null; this.image = null;
    }
    async load(index) {
      if (index < 0 || index >= this.items.length) return;
      const generation = ++this.generation;
      this.stopDocument(); this.index = index; this.count = 0;
      this.dialog.dataset.renderedPage = '';
      this.paper.replaceChildren(); this.$('.dv-thumbnails').replaceChildren();
      this.paper.style.transform = '';
      const item = this.item = this.items[index];
      const identity = new URL(item.url); identity.hash = '';
      this.key = `ecp-dv-page:${this.owner}:${identity.href}`;
      if (!this.states.has(this.key)) {
        let page = Number(item.page) || Number(new URL(item.url).hash.match(/page=(\d+)/)?.[1]);
        if (!page) {
          try { page = Number(sessionStorage.getItem(this.key)); } catch (_) { /* privater Browser */ }
        }
        page = page || 1;
        this.states.set(this.key, {page, zoom: 1, rotation: 0, selection: null});
      }
      this.state = this.states.get(this.key);
      this.$('#dv-name').textContent = item.title || 'Dokument';
      this.$('[data-dv-position]').textContent = `Dokument ${index + 1} von ${this.items.length}`;
      this.$('[data-dv-original]').href = item.originalUrl ? localUrl(item.originalUrl).href : item.url;
      const download = new URL(item.url); download.searchParams.set('download', '1');
      this.$('[data-dv-download]').href = download.href;
      this.$('[data-dv-edit]').hidden = !item.edit;
      if (item.edit) this.$('[data-dv-edit]').href = localUrl(item.edit).href;
      this.status('Dokument wird geladen …'); this.update();
      try {
        if (item.kind === 'image') {
          const img = new Image(); img.src = item.url; await img.decode();
          if (generation !== this.generation) return;
          this.image = img; this.count = 1;
        } else {
          const pdf = await pdfLibrary();
          if (generation !== this.generation) return;
          this.loading = pdf.getDocument({url: item.url, cMapUrl: base + 'cmaps/', cMapPacked: true,
            standardFontDataUrl: base + 'standard_fonts/', wasmUrl: base + 'wasm/', iccUrl: base + 'iccs/',
            isEvalSupported: false, enableXfa: false});
          const loaded = await this.loading.promise;
          if (generation !== this.generation) return;
          this.pdf = loaded;
          this.count = this.pdf.numPages;
        }
        this.state.page = Math.max(1, Math.min(this.state.page, this.count));
        this.items[index].pageCount = this.count;
        this.$('.dv-documents ol').children[index].firstChild.textContent = `${index + 1}. ${item.title || 'Dokument'} (${this.count} ${this.count === 1 ? 'Seite' : 'Seiten'})`;
        this.status(''); this.update(); await this.render();
        if (!this.$('.dv-thumbnails').hidden) this.thumbnails();
      } catch (error) {
        if (generation !== this.generation) return;
        this.count = 0; this.status('Dokument nicht verfügbar, beschädigt oder ohne Zugriffsberechtigung. Bitte erneut versuchen oder das Original öffnen.');
        this.update();
      }
    }
    update() {
      const disabled = !this.count;
      for (const action of ['page-prev', 'page-next', 'zoom-in', 'zoom-out', 'fit', 'rotate', 'thumbnails', 'select-current', 'select-all']) {
        this.$(`[data-dv-action="${action}"]`).disabled = disabled;
      }
      this.$('[data-dv-action="document-prev"]').disabled = this.index === 0;
      this.$('[data-dv-action="document-next"]').disabled = this.index >= this.items.length - 1;
      this.$('[data-dv-action="page-prev"]').disabled = disabled || this.state.page <= 1;
      this.$('[data-dv-action="page-next"]').disabled = disabled || this.state.page >= this.count;
      const page = this.$('[data-dv-page]'); page.value = disabled ? '' : this.state.page; page.max = this.count; page.disabled = disabled;
      this.$('[data-dv-count]').textContent = `von ${this.count || '…'}`;
      this.$('[data-dv-zoom]').textContent = `${Math.round(this.state.zoom * 100)} %`;
      this.$('[data-dv-selection]').disabled = disabled;
      this.$('[data-dv-selection]').value = this.state.selection === null ? String(this.state.page) : this.state.selection;
      this.$('.dv-documents ol').querySelectorAll('button').forEach((button, i) => {
        if (i === this.index) button.setAttribute('aria-current', 'true'); else button.removeAttribute('aria-current');
      });
      this.updateSelection();
    }
    updateSelection() {
      const output = this.$('[data-dv-selected]'), link = this.$('[data-dv-excerpt]');
      link.hidden = true;
      this.$('[data-dv-action="print"]').disabled = true;
      this.$('[data-dv-action="add"]').disabled = true;
      if (!this.count) { this.selected = []; this.selectionUrl = null; output.textContent = ''; return; }
      try {
        this.selected = parsePages(this.state.selection === null ? String(this.state.page) : this.state.selection, this.count);
        output.textContent = `Ausgewählt: ${compressPages(this.selected)} (${this.selected.length} ${this.selected.length === 1 ? 'Seite' : 'Seiten'})`;
        this.$('[data-dv-action="print"]').disabled = false;
        this.$('[data-dv-action="add"]').disabled = typeof this.options.onAdd !== 'function' &&
          !(root.openDocumentTransfer && root.documentTransferSupported?.(this.item.url));
        this.selectionUrl = null;
        if (this.item.kind !== 'image' && this.item.selectionUrl) {
          const url = localUrl(this.item.selectionUrl);
          url.searchParams.set('auswahl', compressPages(this.selected)); this.selectionUrl = url.href;
          link.href = url.href; link.hidden = false;
        }
      } catch (error) { this.selected = []; output.textContent = error.message; }
    }
    async render() {
      if (!this.count || !this.dialog.open || !this.stage.clientWidth) return;
      const generation = ++this.renderGeneration, documentGeneration = this.generation;
      this.renderTask?.cancel();
      try {
        let page, width, height;
        const rotation = this.state.rotation;
        if (this.pdf) {
          page = await this.pdf.getPage(this.state.page);
          const view = page.getViewport({scale: 1, rotation: (page.rotate + rotation) % 360});
          width = view.width; height = view.height;
        } else {
          width = this.image.naturalWidth; height = this.image.naturalHeight;
          if (rotation % 180) [width, height] = [height, width];
        }
        if (generation !== this.renderGeneration || documentGeneration !== this.generation) return;
        const fit = Math.min((this.stage.clientWidth - 32) / width, (this.stage.clientHeight - 32) / height);
        const scale = Math.max(.05, fit) * this.state.zoom;
        const cssWidth = width * scale, cssHeight = height * scale;
        const density = Math.min(root.devicePixelRatio || 1, 2, Math.sqrt(16000000 / (cssWidth * cssHeight)));
        const canvas = document.createElement('canvas');
        canvas.setAttribute('aria-label', `Seite ${this.state.page} von ${this.count}`);
        canvas.width = Math.max(1, Math.floor(cssWidth * density)); canvas.height = Math.max(1, Math.floor(cssHeight * density));
        canvas.style.width = `${cssWidth}px`; canvas.style.height = `${cssHeight}px`;
        this.paper.replaceChildren(canvas);
        const context = canvas.getContext('2d');
        if (page) {
          this.renderTask = page.render({canvasContext: context,
            viewport: page.getViewport({scale, rotation: (page.rotate + rotation) % 360}), transform: [density, 0, 0, density, 0, 0]});
          await this.renderTask.promise;
        } else {
          context.translate(canvas.width / 2, canvas.height / 2); context.rotate(rotation * Math.PI / 180);
          context.scale(scale * density, scale * density);
          context.drawImage(this.image, -this.image.naturalWidth / 2, -this.image.naturalHeight / 2);
        }
        if (generation === this.renderGeneration) {
          this.dialog.dataset.renderedPage = this.state.page;
          this.dialog.dataset.rotation = this.state.rotation;
          try { sessionStorage.setItem(this.key, String(this.state.page)); } catch (_) { /* privater Browser */ }
        }
      } catch (error) {
        if (error.name !== 'RenderingCancelledException' && generation === this.renderGeneration) this.status('Diese Seite konnte nicht dargestellt werden. Bitte eine andere Seite wählen oder das Original öffnen.');
      }
    }
    goPage(page) {
      if (!this.count || !Number.isInteger(page) || page < 1 || page > this.count) {
        this.status(`Bitte eine Seite zwischen 1 und ${this.count || 1} eingeben.`); return;
      }
      this.state.page = page; this.stage.scrollTo(0, 0); this.status(''); this.update(); this.render();
    }
    action(action) {
      if (action === 'close') return this.close();
      if (action === 'document-prev' || action === 'document-next') return this.load(this.index + (action.endsWith('prev') ? -1 : 1));
      if (action === 'documents') { this.$('.dv-documents').hidden = !this.$('.dv-documents').hidden; return; }
      if (action === 'fullscreen') {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => this.status('Vollbild konnte nicht beendet werden.'));
        else if (this.shell.requestFullscreen) this.shell.requestFullscreen().catch(() => this.status('Vollbild ist in diesem Browser nicht verfügbar.'));
        else this.status('Der Viewer nutzt bereits die gesamte verfügbare Bildschirmfläche.');
        return;
      }
      if (!this.count) return;
      if (action === 'page-prev' || action === 'page-next') return this.goPage(this.state.page + (action.endsWith('prev') ? -1 : 1));
      if (action === 'thumbnails') {
        const panel = this.$('.dv-thumbnails'); panel.hidden = !panel.hidden;
        if (!panel.hidden) this.thumbnails(); return;
      }
      if (action === 'select-current' || action === 'select-all') {
        this.state.selection = action === 'select-all' ? 'alle' : String(this.state.page); this.update(); return;
      }
      if (action === 'print') return this.print();
      if (action === 'add') {
        if (this.selected.length && typeof this.options.onAdd === 'function') this.options.onAdd(this.selection());
        else if (this.selected.length && root.openDocumentTransfer && root.documentTransferSupported?.(this.item.url)) {
          root.openDocumentTransfer({items: this.items, index: this.index,
            pages: compressPages(this.selected), page: this.state.page});
        }
        return;
      }
      if (action === 'zoom-in') this.state.zoom = Math.min(5, this.state.zoom + .25);
      if (action === 'zoom-out') this.state.zoom = Math.max(.5, this.state.zoom - .25);
      if (action === 'fit') this.state.zoom = 1;
      if (action === 'rotate') this.state.rotation = (this.state.rotation + 90) % 360;
      this.update(); this.render();
    }
    selection() { return {document: {...this.item}, pages: [...this.selected], page: this.state.page}; }
    print() {
      if (!this.selected.length) return;
      if (!this.dialog.dispatchEvent(new CustomEvent('documentviewer:print', {bubbles: true, cancelable: true, detail: this.selection()}))) return;
      if (root.openDocumentPrint && root.documentPrintSupported(this.item.url)) {
        const selections = {};
        for (const item of this.items) {
          const identity = new URL(item.url); identity.hash = '';
          const state = this.states.get(`ecp-dv-page:${this.owner}:${identity.href}`);
          if (state?.selection) selections[item.url] = state.selection;
        }
        return root.openDocumentPrint({items: this.items, index: this.index, page: this.state.page,
          selection: this.state.selection, selections});
      }
      if (this.item.kind === 'image') return root.printImage(this.item.url);
      const url = this.selectionUrl || (this.selected.length === this.count ? this.item.url : null);
      if (!url) { this.status('Für diesen Dateilink ist nur der Druck des gesamten Originals verfügbar.'); return; }
      const frame = document.createElement('iframe'); frame.className = 'dv-print'; frame.title = 'Druckvorschau';
      this.dialog.querySelectorAll('.dv-print').forEach(previous => previous.remove());
      frame.src = url;
      frame.onload = () => {
        try { frame.contentWindow.focus(); frame.contentWindow.print(); }
        catch (_) { this.status('Bitte „Auswahl als PDF öffnen“ verwenden und im PDF-Menü drucken.'); }
      };
      this.dialog.appendChild(frame);
      frame.contentWindow?.addEventListener('afterprint', () => frame.remove(), {once: true});
      // Die PDF-Datei bleibt als Alternative im sichtbaren Auswahllink erreichbar.
    }
    thumbnails() {
      this.observer?.disconnect();
      const panel = this.$('.dv-thumbnails'); panel.replaceChildren();
      const generation = this.generation;
      let queue = Promise.resolve();
      this.observer = new IntersectionObserver(entries => {
        for (const entry of entries) if (entry.isIntersecting) {
          this.observer.unobserve(entry.target);
          queue = queue.then(async () => {
            if (generation !== this.generation) return;
            const pageNumber = Number(entry.target.dataset.page), canvas = entry.target.querySelector('canvas');
            try {
              if (this.pdf) {
                const page = await this.pdf.getPage(pageNumber), viewport = page.getViewport({scale: .15});
                if (generation !== this.generation) return;
                canvas.width = viewport.width; canvas.height = viewport.height;
                await page.render({canvasContext: canvas.getContext('2d'), viewport}).promise;
              } else { canvas.width = 100; canvas.height = 100 * this.image.naturalHeight / this.image.naturalWidth; canvas.getContext('2d').drawImage(this.image, 0, 0, canvas.width, canvas.height); }
            } catch (_) { /* Seitennummer bleibt auch ohne Vorschaubild bedienbar. */ }
          });
        }
      }, {root: panel, rootMargin: '100px'});
      for (let nr = 1; nr <= this.count; nr++) {
        const button = document.createElement('button'); button.type = 'button'; button.dataset.page = nr;
        button.setAttribute('aria-label', `Seite ${nr} anzeigen`);
        const canvas = document.createElement('canvas'); canvas.setAttribute('aria-hidden', 'true');
        button.append(canvas, document.createTextNode(`Seite ${nr}`));
        button.addEventListener('click', () => this.goPage(nr)); panel.appendChild(button); this.observer.observe(button);
      }
    }
    touch() {
      let start, pinch;
      const distance = touches => Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY);
      this.stage.addEventListener('touchstart', event => {
        if (!this.count) return;
        if (event.touches.length === 2) { pinch = {distance: distance(event.touches), zoom: this.state.zoom}; start = null; }
        else if (event.touches.length === 1) start = {x: event.touches[0].clientX, y: event.touches[0].clientY};
      }, {passive: true});
      this.stage.addEventListener('touchmove', event => {
        if (pinch && event.touches.length === 2) {
          event.preventDefault();
          this.state.zoom = Math.min(5, Math.max(.5, pinch.zoom * distance(event.touches) / pinch.distance));
          this.paper.style.transform = `scale(${this.state.zoom / pinch.zoom})`;
        }
      }, {passive: false});
      this.stage.addEventListener('touchend', event => {
        if (pinch) { this.paper.style.transform = ''; pinch = null; this.update(); this.render(); return; }
        if (!start || event.touches.length || this.state.zoom > 1) return;
        const dx = event.changedTouches[0].clientX - start.x, dy = event.changedTouches[0].clientY - start.y;
        start = null;
        if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.3) {
          const next = this.state.page + (dx < 0 ? 1 : -1); if (next >= 1 && next <= this.count) this.goPage(next);
        }
      }, {passive: true});
      this.stage.addEventListener('touchcancel', () => {
        start = null; pinch = null; this.paper.style.transform = ''; this.update(); this.render();
      }, {passive: true});
    }
    cleanup() {
      this.generation++; this.stopDocument(); this.paper.replaceChildren(); this.count = 0;
      this.$('.dv-thumbnails').replaceChildren();
      this.dialog.querySelectorAll('.dv-print').forEach(frame => frame.remove());
      this.states.clear(); this.options = {};
    }
    close() {
      if (document.fullscreenElement === this.shell) document.exitFullscreen().catch(() => {});
      this.cleanup();
      this.dialog.close(); this.opener?.focus();
    }
  }
  root.openDocumentViewer = function (items, index, options) {
    if (!viewer) viewer = new DocumentViewer();
    return viewer.open(items, index, options).catch(() => viewer.status('Das Dokument kann nicht geöffnet werden. Bitte den ursprünglichen Dateilink verwenden.'));
  };
  root.closeDocumentViewer = () => viewer?.close();
  root.documentViewerSelection = () => viewer?.selection();
  root.documentViewerPages = {parse: parsePages, compress: compressPages};

  document.addEventListener('click', event => {
    const link = event.target.closest('[data-dv-document]');
    if (!link || event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const group = link.closest('[data-dv-group]') || link.closest('.kp-anhaenge-inhalt') || link.parentElement;
    const links = [...group.querySelectorAll('[data-dv-document]')];
    const items = links.map(anchor => ({url: anchor.href, kind: anchor.dataset.dvKind || 'pdf',
      title: anchor.dataset.dvTitle || anchor.textContent.trim() || anchor.querySelector('img')?.alt || 'Dokument',
      page: anchor.dataset.dvPage, originalUrl: anchor.dataset.dvOriginal,
      selectionUrl: anchor.dataset.dvSelectionUrl}));
    event.preventDefault(); root.openDocumentViewer(items, links.indexOf(link));
  });
})(typeof window === 'undefined' ? globalThis : window);
