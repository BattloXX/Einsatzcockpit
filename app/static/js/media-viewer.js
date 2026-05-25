// In-App-Lightbox für Bilder / PDFs / Videos.
// Aufruf: openMediaViewer(url, kind, filename)
//   kind: "image" | "pdf" | "video"
// Schließen: ESC oder Klick auf ×, Klick außerhalb des Inhalts.

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
          <button type="button" class="media-viewer__close" aria-label="Schließen" onclick="closeMediaViewer()">×</button>
        </div>
      </div>
      <div class="media-viewer__content" id="mediaViewerContent"></div>
    `;
    document.body.appendChild(dlg);
    // Klick außerhalb des Inhalts schließt den Viewer.
    dlg.addEventListener('click', (e) => {
      if (e.target === dlg) closeMediaViewer();
    });
    return dlg;
  }

  window.openMediaViewer = function (url, kind, filename) {
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
      content.appendChild(iframe);
    } else if (kind === 'video') {
      const video = document.createElement('video');
      video.src = url;
      video.controls = true;
      video.autoplay = true;
      video.preload = 'metadata';
      video.className = 'media-viewer__video';
      content.appendChild(video);
    } else {
      content.textContent = 'Unbekannter Dateityp.';
    }

    // Download-Link nutzt ?download=1 → Server schickt Content-Disposition: attachment
    const dlUrl = url + (url.includes('?') ? '&' : '?') + 'download=1';
    dl.href = dlUrl;
    dl.setAttribute('download', filename || '');
    name.textContent = filename || '';

    if (!dlg.open) dlg.showModal();
  };

  window.closeMediaViewer = function () {
    const dlg = document.getElementById('mediaViewer');
    if (!dlg) return;
    const content = dlg.querySelector('#mediaViewerContent');
    // Video/Audio anhalten, sonst spielt's im Hintergrund weiter.
    content.querySelectorAll('video,audio').forEach((el) => { try { el.pause(); } catch (e) {} });
    content.innerHTML = '';
    if (dlg.open) dlg.close();
  };
})();
