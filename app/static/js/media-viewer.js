// Kompatible Aufrufe verwenden denselben zentralen PDF-/Bildviewer.
(function () {
  window.openMediaViewer = function (url, kind, filename) {
    if (kind !== 'video') {
      const parsed = new URL(url, location.href);
      const supported = /^\/plaene\/(?:anhang\/[^/]+|quelle\/[^/]+\/original|\d+\/pdf)$/.test(parsed.pathname);
      return window.openDocumentViewer([{url, kind, title: filename,
        selectionUrl: supported ? url : null}]);
    }
    let dialog = document.getElementById('mediaViewer');
    if (!dialog) {
      dialog = document.createElement('dialog'); dialog.id = 'mediaViewer'; dialog.className = 'media-viewer';
      dialog.setAttribute('aria-label', 'Video');
      dialog.innerHTML = `<div class="media-viewer__toolbar">
        <span class="media-viewer__name" id="mediaViewerName"></span>
        <div class="media-viewer__actions">
          <a id="mediaViewerDownload" download class="btn btn--secondary btn--sm">${ecpIcon('download', {size: 'sm'})} Download</a>
          <button type="button" class="media-viewer__close" aria-label="Schließen">${ecpIcon('x', {size: 'xs'})}</button>
        </div></div><div class="media-viewer__content"><video class="media-viewer__video"></video></div>`;
      dialog.querySelector('.media-viewer__close').addEventListener('click', () => dialog.close());
      dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
      dialog.addEventListener('close', () => { const video = dialog.querySelector('video'); video.pause(); video.removeAttribute('src'); });
      document.body.appendChild(dialog);
    }
    const video = dialog.querySelector('video'); video.src = url; video.controls = true; video.playsInline = true;
    video.autoplay = true; video.preload = 'metadata';
    dialog.querySelector('#mediaViewerName').textContent = filename || '';
    const download = new URL(url, location.href); download.searchParams.set('download', '1');
    dialog.querySelector('#mediaViewerDownload').href = download.href;
    dialog.querySelector('#mediaViewerDownload').download = filename || '';
    if (!dialog.open) dialog.showModal();
  };
  window.closeMediaViewer = function () {
    window.closeDocumentViewer();
    const dialog = document.getElementById('mediaViewer'); if (dialog?.open) dialog.close();
  };
  window.openImageLightbox = function (items, startIdx) {
    if (typeof items === 'string') items = [items];
    if (!Array.isArray(items) || !items.length) return;
    return window.openDocumentViewer(items.map(item => typeof item === 'string'
      ? {url: item, kind: 'image', title: 'Bild'}
      : {url: item.src, edit: item.edit, kind: 'image', title: item.title || 'Bild'}), startIdx || 0);
  };
  // ── Bild drucken ──────────────────────────────────────────────────────────
  // Druckt ein einzelnes Bild ueber ein verstecktes iframe (kein Popup-Blocker,
  // isoliert vom restlichen Seiteninhalt). Bevorzugt wird die annotierte Version,
  // da der Aufrufer bereits die /bild-URL uebergibt.
  window.printImage = function (url) {
    if (!url) return;
    const f = document.createElement('iframe');
    f.setAttribute('aria-hidden', 'true');
    f.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;';
    document.body.appendChild(f);
    const doc = f.contentWindow.document;
    doc.open();
    doc.write(
      '<!doctype html><html><head><meta charset="utf-8"><title>Drucken</title>' +
      '<style>@page{margin:8mm}html,body{margin:0;padding:0;background:#fff}' +
      'img{max-width:100%;max-height:100vh;display:block;margin:0 auto;object-fit:contain}</style>' +
      '</head><body><img src="' + String(url).replace(/"/g, '&quot;') + '"></body></html>'
    );
    doc.close();
    const img = doc.querySelector('img');
    const fire = () => {
      setTimeout(() => {
        try { f.contentWindow.focus(); f.contentWindow.print(); } catch (e) { /* ignore */ }
        setTimeout(() => { try { f.remove(); } catch (e) { /* ignore */ } }, 1500);
      }, 60);
    };
    if (img.complete) fire();
    else { img.onload = fire; img.onerror = fire; }
  };

})();
