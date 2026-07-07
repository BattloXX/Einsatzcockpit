// In-App-Lightbox fuer Bilder / PDFs / Videos.
// Aufruf: openMediaViewer(url, kind, filename)
//   kind: "image" | "pdf" | "video"
// Schliessen: ESC oder Klick auf x, Klick auf den Backdrop ausserhalb des Inhalts.
//
// Mehrere geoeffnete <dialog>s sind erlaubt - der Browser stackt sie korrekt
// im Top-Layer. Wir bauen den Viewer als Sibling von <body>, nicht innerhalb
// eines bereits offenen Modals.

(function () {
  function ensureViewer() {
    let dlg = document.getElementById('mediaViewer');
    if (dlg) return dlg;
    dlg = document.createElement('dialog');
    dlg.id = 'mediaViewer';
    dlg.className = 'media-viewer';
    dlg.innerHTML = `
      <div class="media-viewer__toolbar">
        <span class="media-viewer__name" id="mediaViewerName"></span>
        <div class="media-viewer__actions">
          <a id="mediaViewerDownload" href="#" download class="btn btn--secondary btn--sm" title="Datei herunterladen">⬇ Download</a>
          <button type="button" class="media-viewer__close" aria-label="Schliessen">×</button>
        </div>
      </div>
      <div class="media-viewer__content" id="mediaViewerContent"></div>
    `;
    document.body.appendChild(dlg);
    // Schliess-Button
    dlg.querySelector('.media-viewer__close').addEventListener('click', closeMediaViewer);
    // Klick auf den Dialog (= auf das Backdrop ausserhalb des Inhalts) schliesst.
    dlg.addEventListener('click', (e) => {
      if (e.target === dlg) closeMediaViewer();
    });
    // ESC schliesst (zusaetzlich zum Browser-Default fuer <dialog>).
    dlg.addEventListener('cancel', (e) => {
      e.preventDefault();
      closeMediaViewer();
    });
    return dlg;
  }

  window.openMediaViewer = function (url, kind, filename) {
    try {
      const dlg = ensureViewer();
      const content = dlg.querySelector('#mediaViewerContent');
      const dl = dlg.querySelector('#mediaViewerDownload');
      const name = dlg.querySelector('#mediaViewerName');
      content.innerHTML = '';

      if (kind === 'image') {
        const img = document.createElement('img');
        img.src = url;
        img.alt = filename || '';
        img.className = 'media-viewer__image';
        content.appendChild(img);
      } else if (kind === 'pdf') {
        const iframe = document.createElement('iframe');
        iframe.src = url;
        iframe.className = 'media-viewer__iframe';
        iframe.setAttribute('title', filename || 'PDF');
        // Falls der Browser PDF nicht inline rendern kann: Fallback-Link einblenden
        iframe.addEventListener('error', () => {
          content.innerHTML =
            '<div style="color:#fff;padding:24px;text-align:center;">' +
            'PDF kann nicht eingebettet werden. ' +
            '<a href="' + url + '" target="_blank" rel="noopener" style="color:#9ec5ff;">' +
            'In neuem Tab öffnen</a>.</div>';
        });
        content.appendChild(iframe);
      } else if (kind === 'video') {
        const video = document.createElement('video');
        video.src = url;
        video.controls = true;
        video.autoplay = true;
        video.preload = 'metadata';
        video.playsInline = true;
        video.className = 'media-viewer__video';
        content.appendChild(video);
      } else {
        content.textContent = 'Unbekannter Dateityp.';
      }

      // Download-Link nutzt ?download=1 -> Server schickt Content-Disposition: attachment
      const dlUrl = url + (url.includes('?') ? '&' : '?') + 'download=1';
      dl.href = dlUrl;
      dl.setAttribute('download', filename || '');
      name.textContent = filename || '';

      if (dlg.open) dlg.close();
      dlg.showModal();
    } catch (err) {
      // Falls showModal scheitert (z. B. weil schon ein Dialog im Top-Layer ist und
      // der Browser den zweiten verweigert), fallen wir auf Tab-Open zurueck.
      console.error('[media-viewer] open failed:', err);
      window.open(url, '_blank', 'noopener');
    }
  };

  window.closeMediaViewer = function () {
    const dlg = document.getElementById('mediaViewer');
    if (!dlg) return;
    const content = dlg.querySelector('#mediaViewerContent');
    // Video/Audio anhalten, sonst spielt's im Hintergrund weiter.
    content.querySelectorAll('video,audio').forEach((el) => {
      try { el.pause(); } catch (e) { /* ignore */ }
    });
    content.innerHTML = '';
    if (dlg.open) dlg.close();
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

  // ── Bild-Lightbox (Galerie) ───────────────────────────────────────────────
  // Eigener Top-Layer-<dialog> als Sibling von <body> -> immer viewport-zentriert
  // (auch aus einem bereits offenen Modal heraus, wo position:fixed sonst je nach
  // Browser am Modal klebt) und liegt korrekt ueber dem Modal.
  //   openImageLightbox(items, startIdx)
  //   items: [{src, edit}] oder ["url", ...] (src = anzuzeigende Bild-URL,
  //          edit = optionaler Link zum Annotations-Editor)
  function ensureImgLightbox() {
    let dlg = document.getElementById('imgLightbox');
    if (dlg) return dlg;
    dlg = document.createElement('dialog');
    dlg.id = 'imgLightbox';
    dlg.className = 'media-viewer';
    dlg.innerHTML = `
      <div class="media-viewer__toolbar">
        <span class="media-viewer__name" id="imgLbName"></span>
        <div class="media-viewer__actions">
          <a id="imgLbEdit" href="#" class="btn btn--secondary btn--sm" title="Bearbeiten" style="display:none;">✏️ Bearbeiten</a>
          <button type="button" id="imgLbPrint" class="btn btn--secondary btn--sm" title="Bild drucken">🖨 Drucken</button>
          <a id="imgLbDownload" href="#" download class="btn btn--secondary btn--sm" title="Herunterladen">⬇</a>
          <button type="button" class="media-viewer__close" id="imgLbClose" aria-label="Schliessen">×</button>
        </div>
      </div>
      <div class="media-viewer__content" id="imgLbContent" style="position:relative;">
        <button type="button" id="imgLbPrev" aria-label="Vorheriges Bild"
                style="position:absolute;left:10px;top:50%;transform:translateY(-50%);background:rgba(0,0,0,.5);border:none;color:#fff;font-size:34px;line-height:1;cursor:pointer;padding:8px 14px;border-radius:8px;z-index:2;">‹</button>
        <img id="imgLbImage" class="media-viewer__image" alt="">
        <button type="button" id="imgLbNext" aria-label="Naechstes Bild"
                style="position:absolute;right:10px;top:50%;transform:translateY(-50%);background:rgba(0,0,0,.5);border:none;color:#fff;font-size:34px;line-height:1;cursor:pointer;padding:8px 14px;border-radius:8px;z-index:2;">›</button>
      </div>
    `;
    document.body.appendChild(dlg);
    const imgEl = dlg.querySelector('#imgLbImage');
    const editEl = dlg.querySelector('#imgLbEdit');
    const dlEl = dlg.querySelector('#imgLbDownload');
    const prev = dlg.querySelector('#imgLbPrev');
    const next = dlg.querySelector('#imgLbNext');

    dlg._render = function () {
      const it = dlg._items[dlg._idx] || {};
      const src = (typeof it === 'string') ? it : (it.src || '');
      const edit = (typeof it === 'object') ? it.edit : null;
      imgEl.src = src;
      dlg._cur = src;
      if (edit) { editEl.style.display = ''; editEl.href = edit; }
      else { editEl.style.display = 'none'; }
      dlEl.href = src + (src.includes('?') ? '&' : '?') + 'download=1';
      const multi = dlg._items.length > 1;
      prev.style.visibility = (multi && dlg._idx > 0) ? 'visible' : 'hidden';
      next.style.visibility = (multi && dlg._idx < dlg._items.length - 1) ? 'visible' : 'hidden';
    };
    dlg._go = function (d) {
      dlg._idx = Math.max(0, Math.min(dlg._idx + d, dlg._items.length - 1));
      dlg._render();
    };
    prev.addEventListener('click', (e) => { e.stopPropagation(); dlg._go(-1); });
    next.addEventListener('click', (e) => { e.stopPropagation(); dlg._go(1); });
    dlg.querySelector('#imgLbClose').addEventListener('click', () => { if (dlg.open) dlg.close(); });
    dlg.querySelector('#imgLbPrint').addEventListener('click', () => window.printImage(dlg._cur));
    // Klick aufs Backdrop (Dialog selbst) schliesst; Klick aufs Bild nicht.
    dlg.addEventListener('click', (e) => { if (e.target === dlg) { if (dlg.open) dlg.close(); } });
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); if (dlg.open) dlg.close(); });
    dlg.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') dlg._go(-1);
      else if (e.key === 'ArrowRight') dlg._go(1);
    });
    return dlg;
  }

  window.openImageLightbox = function (items, startIdx) {
    try {
      if (typeof items === 'string') items = [items];
      if (!Array.isArray(items) || !items.length) return;
      const dlg = ensureImgLightbox();
      dlg._items = items;
      dlg._idx = Math.max(0, Math.min(startIdx || 0, items.length - 1));
      dlg._render();
      if (dlg.open) dlg.close();
      dlg.showModal();
    } catch (err) {
      console.error('[img-lightbox] open failed:', err);
      const it = items && items[0];
      const u = (typeof it === 'string') ? it : (it && it.src);
      if (u) window.open(u, '_blank', 'noopener');
    }
  };
})();
