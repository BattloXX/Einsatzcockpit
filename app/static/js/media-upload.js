/* ─── Media Upload Helpers ───────────────────────────────────────────
 * Reparieren des mobilen Foto-Uploads (Android/Chrome): capture="environment"
 * blockierte oft die Galerie. Stattdessen zwei Buttons "Kamera"/"Galerie"
 * + clientseitige Bild-Kompression auf serverseitige Limits.
 * ────────────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  // Limits müssen exakt mit settings.MAX_UPLOAD_BYTES_IMAGE übereinstimmen.
  const IMAGE_MAX_BYTES = 10 * 1024 * 1024;   // 10 MB
  const IMAGE_MAX_DIM   = 2560;                // längste Kante in px
  const IMAGE_QUALITY   = 0.85;                // JPEG-Quality

  /* ── Upload-Fortschrittsbalken ─────────────────────────────────── */
  const bar = document.createElement('div');
  bar.id = 'upload-progress-bar';
  Object.assign(bar.style, {
    position: 'fixed', top: '0', left: '0', width: '0%', height: '3px',
    background: 'var(--red, #b71921)', zIndex: '9999',
    transition: 'width .2s ease, opacity .4s ease',
    opacity: '0', pointerEvents: 'none',
  });
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(bar));

  let _hideTimer = null;

  function progressShow(pct) {
    clearTimeout(_hideTimer);
    bar.style.opacity = '1';
    bar.style.width = pct + '%';
  }

  function progressDone() {
    bar.style.width = '100%';
    _hideTimer = setTimeout(() => {
      bar.style.opacity = '0';
      setTimeout(() => { bar.style.width = '0%'; }, 450);
    }, 350);
  }

  // HTMX XHR progress → update bar (capped at 90 so "done" is visually distinct)
  document.addEventListener('htmx:xhr:progress', function (e) {
    if (e.detail && e.detail.loaded && e.detail.total) {
      const pct = Math.min(90, Math.round(e.detail.loaded / e.detail.total * 100));
      progressShow(pct);
    }
  });

  // HTMX request finished → fill to 100 % and hide
  document.addEventListener('htmx:afterRequest', function (e) {
    if (bar.style.opacity === '1') progressDone();
  });

  /* ── Public helpers ──────────────────────────────────────────────── */
  window.openCamera = function (inputId) {
    const inp = document.getElementById(inputId);
    if (!inp) return;
    inp.setAttribute('capture', 'environment');
    inp.click();
  };

  window.openGallery = function (inputId) {
    const inp = document.getElementById(inputId);
    if (!inp) return;
    inp.removeAttribute('capture');
    inp.click();
  };

  window.compressAndSubmit = async function (inputEl) {
    const files = Array.from(inputEl.files || []);
    if (!files.length) return;

    progressShow(5);  // sofort sichtbar während Kompression läuft

    const out = new DataTransfer();
    for (const f of files) {
      if (f.type && f.type.startsWith('image/') && f.size > IMAGE_MAX_BYTES) {
        try {
          let compressed = await compressImage(f, IMAGE_MAX_DIM, IMAGE_QUALITY);
          if (compressed.size > IMAGE_MAX_BYTES) {
            compressed = await compressImage(f, 1920, 0.75);
          }
          out.items.add(compressed);
        } catch (e) {
          console.warn('image compression failed, sending original', e);
          out.items.add(f);
        }
      } else {
        out.items.add(f);  // PDF/Video unverändert, Server prüft Limits.
      }
    }
    inputEl.files = out.files;

    progressShow(15);  // Kompression fertig, Upload beginnt gleich

    const form = inputEl.closest('form');
    if (!form) return;
    if (window.htmx) {
      htmx.trigger(form, 'submit');
    } else {
      form.requestSubmit();
    }
  };

  async function compressImage(file, maxDim, quality) {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, maxDim / Math.max(bitmap.width, bitmap.height));
    const w = Math.round(bitmap.width * scale);
    const h = Math.round(bitmap.height * scale);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(bitmap, 0, 0, w, h);
    const blob = await new Promise(res => canvas.toBlob(res, 'image/jpeg', quality));
    if (!blob) throw new Error('toBlob returned null');
    const name = (file.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg';
    return new File([blob], name, { type: 'image/jpeg', lastModified: Date.now() });
  }
})();
