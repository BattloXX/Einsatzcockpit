/* Gemeinsame Serverzeit fuer Timer und Offline-Ereignisse. */
(function () {
  'use strict';
  let offsetMs = 0;
  const api = {
    sync(serverIso, localNowMs) {
      const serverMs = Date.parse(serverIso);
      const localMs = localNowMs === undefined ? Date.now() : localNowMs;
      if (Number.isFinite(serverMs) && Number.isFinite(localMs)) offsetMs = serverMs - localMs;
    },
    now() { return Date.now() + offsetMs; },
    setOffsetMs(ms) { if (Number.isFinite(ms)) offsetMs = ms; }
  };
  if (typeof window !== 'undefined') window.ECPServerClock = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
