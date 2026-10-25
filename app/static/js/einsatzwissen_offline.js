/* Explizite Offlinefreigabe: verschlüsselte Kerntexte, Tab-Schlüssel, feste Ablaufzeit. */
(function (root) {
  'use strict';
  const prefix = 'ec-einsatzwissen-v1', maxBytes = 2000000;
  function validPackage(data, now, owner) {
    return data && data.owner === owner && Number.isFinite(data.erstellt) && Number.isFinite(data.gueltig_bis)
      && now >= data.erstellt && now < data.gueltig_bis && data.gueltig_bis - data.erstellt <= 900000
      && Array.isArray(data.eintraege) && data.eintraege.length > 0 && data.eintraege.length <= 20;
  }
  if (typeof module !== 'undefined') module.exports = {validPackage};
  if (!root.document) return;
  const area = document.querySelector('[data-ew-offline-inhalt]');
  let busy = false, generation = 0, queued = false, queuedId, disconnected = false;
  function status(text) { document.querySelectorAll('[data-ew-status]').forEach(e => { e.textContent = text; }); }
  function clear() {
    generation++;
    localStorage.removeItem(prefix); sessionStorage.removeItem(prefix);
    sessionStorage.removeItem(prefix + '-clock');
    area?.replaceChildren();
  }
  function encode(bytes) { return btoa(Array.from(bytes, c => String.fromCharCode(c)).join('')); }
  function decode(text) { return Uint8Array.from(atob(text), c => c.charCodeAt(0)); }
  async function key(raw, usage) { return crypto.subtle.importKey('raw', decode(raw), 'AES-GCM', false, [usage]); }
  function identity() { return JSON.parse(sessionStorage.getItem(prefix) || 'null'); }
  function clockNow() {
    const now = Date.now(), seen = Number(sessionStorage.getItem(prefix + '-clock') || 0);
    if (now < seen - 1000) { clear(); throw Error('Gerätezeit geändert. Bitte online neu freigeben.'); }
    sessionStorage.setItem(prefix + '-clock', String(Math.max(now, seen)));
    return Math.max(now, seen);
  }
  async function read() {
    const currentGeneration = generation;
    const ticket = identity(), stored = JSON.parse(localStorage.getItem(prefix) || 'null');
    if (!ticket || !stored) return null;
    if (localStorage.getItem(prefix + '-owner') !== ticket.context) { clear(); return null; }
    const data = JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt(
      {name: 'AES-GCM', iv: decode(stored.iv)}, await key(ticket.key, 'decrypt'), decode(stored.data))));
    if (generation !== currentGeneration) return null;
    if (!validPackage(data, clockNow(), ticket.owner)) { clear(); return null; }
    return data;
  }
  function textElement(tag, text) { const e = document.createElement(tag); e.textContent = text; return e; }
  function values(value) {
    if (Array.isArray(value)) return value.map(values).join('\n');
    if (value && typeof value === 'object') return Object.values(value).map(values).join(' · ');
    return String(value ?? '');
  }
  function render(data) {
    if (!area) return;
    area.replaceChildren();
    if (!data) { status('Keine gültigen lokalen Inhalte in diesem Tab. Bitte online bereithalten.'); return; }
    const size = new TextEncoder().encode(JSON.stringify(data)).length;
    status(`Stand: ${new Date(data.erstellt).toLocaleString('de-AT')} · gültig bis ${new Date(data.gueltig_bis).toLocaleTimeString('de-AT')} · ${Math.ceil(size / 1024)} KB von höchstens 2 MB`);
    for (const entry of data.eintraege) {
      const section = document.createElement('section');
      section.append(textElement('h2', `${entry.titel} · Version ${entry.version}`));
      if (entry.pruefung_faellig) section.append(textElement('strong', 'Prüfung fällig – Stand vor Verwendung prüfen.'));
      if (entry.gefahren.length) section.append(textElement('p', entry.gefahren.join(' · ')));
      for (const group of entry.bereiche) {
        section.append(textElement('h3', group.titel));
        if (!group.elemente.length) section.append(textElement('p', 'Keine gesonderten Angaben hinterlegt.'));
        for (const item of group.elemente) {
          const article = document.createElement('article');
          article.append(textElement('h4', item.titel), textElement('p', item.text));
          for (const [label, value] of Object.entries(item.daten)) {
            if (value) article.append(textElement('p', `${label.replaceAll('_', ' ')}: ${values(value)}`));
          }
          section.append(article);
        }
      }
      area.append(section);
    }
  }
  async function sync(addId) {
    if (busy) { queued = true; queuedId = addId || queuedId; return; }
    busy = true;
    const currentGeneration = generation;
    try {
      const previous = await read();
      const ids = [...new Set([...(previous?.eintraege.map(e => e.id) || []), ...(addId ? [Number(addId)] : [])])];
      if (!ids.length) { render(null); return; }
      if (ids.length > 20) throw Error('Höchstens 20 Einträge. Bitte lokale Inhalte zuerst löschen.');
      status('Berechtigungen und Freigabestand werden geprüft …');
      const response = await fetch('/einsatzwissen/offline-paket?ids=' + ids.join(','), {cache: 'no-store'});
      if (generation !== currentGeneration) return;
      if (response.headers.get('X-Offline') === '1') throw TypeError('Keine Verbindung');
      if (!response.ok || response.redirected || !response.headers.get('content-type')?.includes('application/json')) {
        clear();
        throw Error('Lokale Freigabe nicht verfügbar oder entzogen. Bitte online neu öffnen.');
      }
      const data = await response.json();
      disconnected = false;
      if (generation !== currentGeneration) return;
      const context = document.body.dataset.ewOwner || identity()?.context;
      if (!context || !validPackage(data, clockNow(), context.split(':').slice(0, 2).join(':'))) {
        clear(); throw Error('Offlineberechtigung ungültig. Bitte neu anmelden.');
      }
      const bytes = new TextEncoder().encode(JSON.stringify(data));
      if (bytes.length > maxBytes) throw Error('Lokale Inhalte überschreiten 2 MB. Bitte Auswahl verkleinern.');
      // Jede Synchronisierung erneuert den Tab-Schlüssel. Andere Tabs verwerfen
      // bei einer Speicheränderung nur ihren eigenen Schlüssel.
      const raw = crypto.getRandomValues(new Uint8Array(32)), iv = crypto.getRandomValues(new Uint8Array(12));
      const encrypted = await crypto.subtle.encrypt({name: 'AES-GCM', iv}, await key(encode(raw), 'encrypt'), bytes);
      if (generation !== currentGeneration) return;
      localStorage.setItem(prefix + '-owner', context);
      localStorage.setItem(prefix, JSON.stringify({iv: encode(iv), data: encode(new Uint8Array(encrypted))}));
      sessionStorage.setItem(prefix, JSON.stringify({key: encode(raw), owner: data.owner, context, expires: data.gueltig_bis}));
      await Promise.all(['/static/einsatzwissen-offline.html', '/static/css/einsatzwissen.css',
        '/static/js/einsatzwissen_offline.js'].map(url => fetch(url)));
      if (generation !== currentGeneration) return;
      render(data);
      if (!area) status(`Kerninhalte bereit · Version ${data.eintraege.find(e => e.id === Number(addId))?.version || 'aktuell'} · gültig bis ${new Date(data.gueltig_bis).toLocaleTimeString('de-AT')}.`);
    } catch (error) {
      if (error instanceof TypeError) disconnected = true;
      // Nur Verbindungsfehler dürfen eine noch gültige Offlinefreigabe weiterverwenden.
      const existing = await read().catch(() => { clear(); return null; });
      render(existing);
      status(error instanceof TypeError ? (existing ? 'Keine Verbindung. Vorhandene Freigabe gilt nur bis zur angezeigten Ablaufzeit.' : 'Keine Verbindung und keine gültigen lokalen Inhalte. Bitte online bereithalten.') : error.message);
    } finally {
      busy = false;
      if (queued) {
        const nextId = queuedId;
        queued = false; queuedId = undefined;
        void sync(nextId);
      }
    }
  }
  try {
    const context = document.body.dataset.ewOwner;
    if (context && context !== localStorage.getItem(prefix + '-owner')) {
      clear(); localStorage.setItem(prefix + '-owner', context);
    }
    const beforeSwitch = root.ecKontowechselVorbereiten;
    root.ecKontowechselVorbereiten = function () { clear(); beforeSwitch?.(); };
    document.addEventListener('click', event => {
      const link = event.target.closest('a[href]');
      if (link && /^\/(logout|account-switch)(\/|\?|$)/.test(new URL(link.href).pathname)) clear();
      const save = event.target.closest('[data-ew-save]');
      if (save) void sync(save.dataset.ewSave);
      if (event.target.closest('[data-ew-sync]')) void sync();
      if (event.target.closest('[data-ew-clear]')) { clear(); render(null); }
    });
    root.addEventListener('storage', event => {
      if (event.key === prefix || event.key === prefix + '-owner') {
        generation++;
        sessionStorage.removeItem(prefix);
        area?.replaceChildren();
        if (area) status('Lokale Freigabe in einem anderen Tab geändert. Bitte online neu bereithalten.');
      }
    });
    root.addEventListener('online', () => void sync());
    root.addEventListener('pageshow', () => void read().then(render).catch(() => { clear(); render(null); }));
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) void read().then(render).catch(() => { clear(); render(null); });
    });
    setInterval(() => {
      try { if (identity() && clockNow() >= identity().expires) { clear(); render(null); } }
      catch (_) { clear(); render(null); }
    }, 1000);
    setInterval(() => { if (navigator.onLine && identity()) void sync(); }, 60000);
    // Nach einer Offline-Neunavigation liefern manche Browser kein online-Ereignis.
    setInterval(() => { if (disconnected && navigator.onLine && identity() && !busy) void sync(); }, 5000);
    if (identity()) void sync(); else render(null);
  } catch (_) { status('Lokale Speicherung ist in diesem Browser nicht verfügbar.'); }
})(typeof window === 'undefined' ? globalThis : window);
