/* Netzwerk-Helfer fuer das Atemschutz-Board. */
(function () {
  'use strict';

  function backoffMs(attempt) {
    return Math.min(30000, 1000 * Math.pow(2, attempt));
  }

  async function send(url, formData, { timeoutMs = 10000, fetchImpl = fetch } = {}) {
    const controller = new AbortController();
    let timer;
    try {
      return await Promise.race([
        (async function () {
          const response = await fetchImpl(url, {
            method: 'POST', body: formData, credentials: 'same-origin', signal: controller.signal
          });
          const message = (await response.text()).trim().slice(0, 200);
          return { ok: response.ok, status: response.status, message };
        })(),
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            controller.abort();
            reject(new Error('timeout'));
          }, timeoutMs);
        })
      ]);
    } catch (_) {
      return { ok: false, status: 0, message: 'Keine Verbindung zum Server' };
    } finally {
      clearTimeout(timer);
    }
  }

  function describeFailure(status, message) {
    if (!status) return 'Nicht gespeichert: keine Verbindung';
    const text = String(message || '').trim().slice(0, 200);
    return (status === 409 ? 'Status wurde bereits geändert: ' : 'Nicht gespeichert: ') + text;
  }

  function showError(text) {
    const old = document.getElementById('breathing-error-banner');
    if (old) old.remove();
    const banner = document.createElement('div');
    banner.id = 'breathing-error-banner';
    banner.setAttribute('role', 'alert');
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:10001;background:#7c1d1d;color:#fff;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;gap:12px;';
    const message = document.createElement('span');
    message.textContent = text;
    const close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('aria-label', 'Fehlermeldung schließen');
    close.textContent = 'Schließen';
    close.style.cssText = 'background:transparent;color:inherit;border:1px solid currentColor;padding:4px 12px;cursor:pointer;';
    close.addEventListener('click', () => banner.remove());
    banner.append(message, close);
    document.body.prepend(banner);
    if (typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') navigator.vibrate(200);
  }

  function showInfo(text) {
    const old = document.getElementById('breathing-info-banner');
    if (old) old.remove();
    const banner = document.createElement('div');
    banner.id = 'breathing-info-banner';
    banner.setAttribute('role', 'status');
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:10001;background:#e2e8f0;color:#1a202c;padding:12px 16px;';
    banner.textContent = text;
    document.body.prepend(banner);
    setTimeout(() => banner.remove(), 6000);
  }

  const reported = new WeakSet();
  function formFailed(event) {
    const detail = event.detail, xhr = detail.xhr;
    if (reported.has(xhr)) return;
    reported.add(xhr);
    const form = detail.elt?.closest('form') || event.target?.closest('form');
    const path = detail.requestConfig?.path || form?.getAttribute('hx-post') || '';
    if (!xhr.status) {
      if (/\/(starten|trupp|erneut-einsetzen|notfall\/aufheben)$/.test(path)) {
        showError('Diese Aktion ist nur online möglich.');
      } else if (form) {
        form.dispatchEvent(new CustomEvent('breathing-queued', { bubbles: true }));
      }
      return;
    }
    const correction = form?.querySelector('[name="korrektur_grund"]');
    if (correction && xhr.status === 409 && String(xhr.responseText).includes('Der Druck kann nicht steigen.')) {
      correction.closest('details').open = true;
      form.querySelector('[data-correction-error]').textContent = xhr.responseText;
      correction.focus();
      return;
    }
    showError(describeFailure(xhr.status, xhr.responseText));
  }

  function createConnTracker(now = Date.now) {
    let lostAt = null;
    return {
      onOpen() { lostAt = null; },
      onClose() { if (lostAt === null) lostAt = now(); },
      lostSeconds() { return lostAt === null ? null : Math.max(0, Math.floor((now() - lostAt) / 1000)); }
    };
  }

  const api = { backoffMs, send, describeFailure, showError, showInfo, formFailed, createConnTracker };
  if (typeof window !== 'undefined') window.ECPBreathingNet = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
