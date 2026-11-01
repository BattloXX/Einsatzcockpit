/* Gemeinsame, seiteneffektfreie URL-Pruefung fuer iOS-Deep-Links. */
(function () {
  'use strict';

  function _resolveDeepLink(url, origin) {
    if (typeof url !== 'string' || /[\\\x00-\x1f\x7f-\x9f]/.test(url)) return null;
    try {
      const parsed = new URL(url);
      let path;
      if (parsed.protocol === 'https:'
          && ['einsatzcockpit.com', 'www.einsatzcockpit.com'].includes(parsed.host)
          && !parsed.username && !parsed.password) {
        path = parsed.pathname + parsed.search + parsed.hash;
      } else if (parsed.protocol === 'einsatzcockpit:') {
        path = parsed.searchParams.get('path');
      } else {
        return null;
      }
      if (!path || !path.startsWith('/') || path.startsWith('//')
          || /[\\\x00-\x1f\x7f-\x9f]/.test(path)) return null;
      if (new URL(origin + path).origin !== origin) return null;
      return path;
    } catch (_) {
      return null;
    }
  }

  const api = { _resolveDeepLink };
  if (typeof module !== 'undefined') module.exports = api;
  if (typeof window !== 'undefined') window.ECPDeepLink = api;
})();
