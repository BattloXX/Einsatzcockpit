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

  /* ── Rückmeldung für Upload-Fehler ───────────────────────────── */
  function showUploadToast(message) {
    const appEl = document.querySelector('[x-data="appState()"]');
    if (appEl && window.Alpine) {
      Alpine.$data(appEl).addToast(message, 'warn');
    } else if (!appEl) {
      alert(message);
    }
  }

  /* ── CSRF-Token aus Cookie ────────────────────────────────────── */
  function readCsrf() {
    const cookies = (document.cookie || '').split(/;\s*/);
    for (const c of cookies) {
      const i = c.indexOf('=');
      if (i === -1) continue;
      if (c.slice(0, i).trim() === 'ec_csrf') return decodeURIComponent(c.slice(i + 1));
    }
    return null;
  }

  /* ── Kern-Upload via XHR ─────────────────────────────────────── */
  function uploadForm(formEl, files, inputEl) {
    const action = formEl.getAttribute('hx-post') || formEl.action;
    const targetSel = formEl.getAttribute('hx-target');
    const swapMode  = formEl.getAttribute('hx-swap') || 'innerHTML';

    if (!action) return;

    progressShow(2, 'Vorbereitung…');

    const fd = new FormData();
    const inputName = formEl.querySelector('input[type="file"]')?.name || 'files';
    for (const f of files) fd.append(inputName, f);

    const xhr = new XMLHttpRequest();
    xhr.timeout = 180000;

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      const pct = Math.min(92, Math.round(e.loaded / e.total * 100));
      const kb = Math.round(e.total / 1024);
      progressShow(pct, pct + ' % · ' + kb + ' KB');
    };

    xhr.onload = function () {
      progressDone();

      const redirect = xhr.getResponseHeader('HX-Redirect');
      if (redirect) {
        location.href = redirect;
        return;
      }
      if (xhr.status === 401) {
        showUploadToast('Sitzung abgelaufen – bitte neu anmelden.');
        location.href = '/login';
        return;
      }
      if (xhr.status === 503 && xhr.getResponseHeader('X-Offline') === '1') {
        showUploadToast('Upload erfordert Verbindung – du bist offline.');
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        let detail = xhr.status === 413 ? 'Datei zu groß' : String(xhr.status);
        try {
          const response = JSON.parse(xhr.responseText);
          if (response && typeof response.detail === 'string') detail = response.detail;
        } catch (e) {
          // Für nicht-JSON-Fehler bleibt der Statuscode sichtbar.
        }
        showUploadToast('Upload fehlgeschlagen: ' + detail);
        return;
      }

      try {
        const errors = JSON.parse(decodeURIComponent(xhr.getResponseHeader('X-Upload-Errors') || '[]'));
        if (Array.isArray(errors)) {
          for (const error of errors) {
            if (typeof error === 'string') showUploadToast('Upload fehlgeschlagen: ' + error);
          }
        }
      } catch (e) {
        // Ein fehlerhafter optionaler Header darf den erfolgreichen Upload nicht stören.
      }

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

    xhr.onerror = function () {
      progressDone();
      showUploadToast('Upload fehlgeschlagen – keine Verbindung.');
    };
    xhr.onabort = progressDone;
    xhr.ontimeout = function () {
      progressDone();
      showUploadToast('Upload abgebrochen (Zeitüberschreitung) – bitte erneut versuchen.');
    };

    xhr.open('POST', action, true);
    xhr.setRequestHeader('HX-Request', 'true');
    xhr.setRequestHeader('HX-Current-URL', location.href);
    const csrf = readCsrf();
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);

    if (inputEl) inputEl.value = '';
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

    const out = await compressUploadFiles(files);

    const form = inputEl.closest('form');
    if (!form) return;
    uploadForm(form, out, inputEl);
  };

  // Kamera-Schnellupload mit derselben Bildaufbereitung wie der normale Upload.
  window.quickCameraUpload = async function (inputEl) {
    const files = Array.from(inputEl.files || []);
    if (!files.length) return;
    progressShow(5, 'Lade hoch…');
    const form = inputEl.closest('form');
    if (!form) return;
    const out = await compressUploadFiles(files);
    uploadForm(form, out, inputEl);
  };

  // Komprimiert Bilder im File-Input in-place (via DataTransfer), OHNE selbst zu submiten.
  // Aufruf im onchange-Handler, bevor htmx das Form absendet.
  window.compressFilesInPlace = async function (inputEl) {
    const files = Array.from(inputEl.files || []);
    if (!files.length) return;
    const dt = new DataTransfer();
    for (const f of await compressUploadFiles(files)) dt.items.add(f);
    inputEl.files = dt.files;
  };

  function isCompressibleImage(file) {
    return file.type && file.type.startsWith('image/') &&
      file.type !== 'image/gif' && file.type !== 'image/svg+xml';
  }

  async function compressUploadFiles(files) {
    const out = [];
    for (const file of files) {
      if (!isCompressibleImage(file)) {
        out.push(file);
        continue;
      }
      try {
        let result = await compressImage(file, IMAGE_MAX_DIM, IMAGE_QUALITY);
        if (result.size >= file.size && file.size <= IMAGE_MAX_BYTES) result = file;
        if (result.size > IMAGE_MAX_BYTES) {
          const retry = await compressImage(file, 1920, 0.75);
          if (retry.size < file.size || file.size > IMAGE_MAX_BYTES) result = retry;
        }
        out.push(result);
      } catch (e) {
        console.warn('Komprimierung fehlgeschlagen, Original wird gesendet', e);
        out.push(file);
      }
    }
    return out;
  }

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
