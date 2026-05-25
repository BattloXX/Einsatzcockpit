/* ─── Media Upload Helpers ───────────────────────────────────────────
 * Eigenständiger XHR-Upload mit echtem upload.onprogress-Tracking.
 * Ersetzt den alten htmx:xhr:progress-Ansatz, der nur Download-Progress
 * lieferte und bei kleinen Dateien unsichtbar blieb.
 * ────────────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  // Limits müssen exakt mit settings.MAX_UPLOAD_BYTES_IMAGE übereinstimmen.
  const IMAGE_MAX_BYTES = 10 * 1024 * 1024;   // 10 MB
  const IMAGE_MAX_DIM   = 2560;                // längste Kante in px
  const IMAGE_QUALITY   = 0.85;                // JPEG-Quality

  /* ── Upload-Fortschrittsbalken ─────────────────────────────────── */
  const track = document.createElement('div');
  track.id = 'upload-progress-track';
  Object.assign(track.style, {
    position: 'fixed', top: '0', left: '0', width: '100%', height: '6px',
    background: 'rgba(0,0,0,.35)', zIndex: '9999',
    opacity: '0', pointerEvents: 'none',
    transition: 'opacity .25s ease',
  });

  const fill = document.createElement('div');
  Object.assign(fill.style, {
    height: '100%', width: '0%',
    background: 'var(--red, #b71921)',
    transition: 'width .15s ease',
  });
  track.appendChild(fill);

  const label = document.createElement('span');
  Object.assign(label.style, {
    position: 'fixed', top: '7px', left: '50%',
    transform: 'translateX(-50%)',
    fontSize: '11px', fontWeight: '700', color: '#fff',
    textShadow: '0 1px 3px rgba(0,0,0,.8)',
    zIndex: '10000', pointerEvents: 'none', opacity: '0',
    transition: 'opacity .25s ease',
    whiteSpace: 'nowrap',
  });
  document.head.appendChild(label);

  function initBar() {
    if (!document.body.contains(track)) document.body.appendChild(track);
  }
  if (document.body) {
    initBar();
  } else {
    document.addEventListener('DOMContentLoaded', initBar);
  }

  let _hideTimer = null;

  function progressShow(pct, text) {
    clearTimeout(_hideTimer);
    track.style.opacity = '1';
    label.style.opacity = '1';
    fill.style.width = pct + '%';
    if (text) label.textContent = text;
  }

  function progressDone() {
    fill.style.width = '100%';
    label.textContent = '';
    _hideTimer = setTimeout(() => {
      track.style.opacity = '0';
      label.style.opacity = '0';
      setTimeout(() => { fill.style.width = '0%'; }, 300);
    }, 500);
  }

  /* ── CSRF-Token aus Cookie ────────────────────────────────────── */
  function readCsrf() {
    const cookies = (document.cookie || '').split(/;\s*/);
    for (const c of cookies) {
      const i = c.indexOf('=');
      if (i === -1) continue;
      if (c.slice(0, i).trim() === 'fwwo_csrf') return decodeURIComponent(c.slice(i + 1));
    }
    return null;
  }

  /* ── Kern-Upload via XHR ─────────────────────────────────────── */
  function uploadForm(formEl, files) {
    const action = formEl.getAttribute('hx-post') || formEl.action;
    const targetSel = formEl.getAttribute('hx-target');
    const swapMode  = formEl.getAttribute('hx-swap') || 'innerHTML';

    if (!action) return;

    progressShow(2, 'Vorbereitung…');

    const fd = new FormData();
    const inputName = formEl.querySelector('input[type="file"]')?.name || 'files';
    for (const f of files) fd.append(inputName, f);

    const xhr = new XMLHttpRequest();

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      const pct = Math.min(92, Math.round(e.loaded / e.total * 100));
      const kb = Math.round(e.total / 1024);
      progressShow(pct, pct + ' % · ' + kb + ' KB');
    };

    xhr.onload = function () {
      progressDone();
      if (!targetSel || swapMode === 'none') return;
      const target = document.querySelector(targetSel);
      if (!target) return;
      if (swapMode === 'outerHTML') {
        target.outerHTML = xhr.responseText;
      } else {
        target.innerHTML = xhr.responseText;
        // Re-bind HTMX and Alpine on injected content.
        if (window.htmx) htmx.process(target);
        if (window.Alpine) Alpine.initTree(target);
      }
    };

    xhr.onerror = xhr.onabort = function () {
      progressDone();
    };

    xhr.open('POST', action, true);
    xhr.setRequestHeader('HX-Request', 'true');
    xhr.setRequestHeader('HX-Current-URL', location.href);
    const csrf = readCsrf();
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);

    xhr.send(fd);
  }

  /* ── Öffentliche Helfer ──────────────────────────────────────── */
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

    progressShow(3, 'Komprimiere…');

    const out = [];
    for (const f of files) {
      if (f.type && f.type.startsWith('image/') && f.size > IMAGE_MAX_BYTES) {
        try {
          let compressed = await compressImage(f, IMAGE_MAX_DIM, IMAGE_QUALITY);
          if (compressed.size > IMAGE_MAX_BYTES) {
            compressed = await compressImage(f, 1920, 0.75);
          }
          out.push(compressed);
        } catch (e) {
          console.warn('image compression failed, sending original', e);
          out.push(f);
        }
      } else {
        out.push(f);
      }
    }

    const form = inputEl.closest('form');
    if (!form) return;
    uploadForm(form, out);
  };

  // Kamera-Schnellupload (ohne komprimieren, direktes Foto)
  window.quickCameraUpload = async function (inputEl) {
    const files = Array.from(inputEl.files || []);
    if (!files.length) return;
    progressShow(5, 'Lade hoch…');
    const form = inputEl.closest('form');
    if (!form) return;
    // Bilder trotzdem komprimieren falls nötig.
    const out = [];
    for (const f of files) {
      if (f.type && f.type.startsWith('image/') && f.size > IMAGE_MAX_BYTES) {
        try { out.push(await compressImage(f, IMAGE_MAX_DIM, IMAGE_QUALITY)); }
        catch (e) { out.push(f); }
      } else {
        out.push(f);
      }
    }
    uploadForm(form, out);
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
