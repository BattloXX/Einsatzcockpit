/* Dauerhafte Diagnose-Sonde fuer unerwartete Voll-Reloads. */
(function () {
  'use strict';

  window.__ecLoadId = crypto.randomUUID();

  const STORAGE_KEY = 'ec_diag_log';
  const MAX_ENTRIES = 200;
  const enabled = new URLSearchParams(location.search).get('diag') === '1'
    || localStorage.getItem('ec_diag') !== null;

  function readLog() {
    try {
      const value = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]');
      return Array.isArray(value) ? value : [];
    } catch (_) {
      return [];
    }
  }

  window.ecDiagLog = function (kategorie, detail) {
    if (!enabled) return;
    const entries = readLog();
    entries.push({ zeit: new Date().toISOString(), kategorie, detail: detail == null ? null : String(detail) });
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(-MAX_ENTRIES)));
  };

  window.ecReload = function (grund) {
    const entries = readLog();
    const last = entries.length ? entries[entries.length - 1].kategorie : null;
    window.ecDiagLog('reload', `${grund}; vorher=${last || 'keines'}`);
    location.reload();
  };

  window.ecDiagDump = function () {
    console.table(readLog());
  };

  if (!enabled) return;
  const navigation = performance.getEntriesByType('navigation')[0];
  window.ecDiagLog('pageload', navigation ? navigation.type : 'unbekannt');
  window.addEventListener('online', () => window.ecDiagLog('online'));
  window.addEventListener('offline', () => window.ecDiagLog('offline'));
  document.addEventListener('visibilitychange', () => window.ecDiagLog('visibilitychange', document.visibilityState));
  ['htmx:responseError', 'htmx:sendError', 'htmx:timeout'].forEach(type => {
    document.addEventListener(type, () => window.ecDiagLog(type));
  });
})();
