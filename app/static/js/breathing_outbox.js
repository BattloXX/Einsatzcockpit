/* Persistente Atemschutz-Outbox, ohne DOM-Zugriffe. */
(function () {
  'use strict';

  function zufallsUuid(kryptografie = globalThis.crypto) {
    if (kryptografie && typeof kryptografie.randomUUID === 'function') return kryptografie.randomUUID();
    const bytes = new Uint8Array(16);
    if (kryptografie && typeof kryptografie.getRandomValues === 'function') kryptografie.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  function createOutbox({ indexedDB, fetch, now = Date.now, uuid = zufallsUuid,
    csrfToken = () => '', incidentId, offsetMs = () => 0, onChange,
    dbName = `ec-asuew-${incidentId}`, openTimeoutMs = 4000, requestTimeoutMs = 15000,
    localStorage: storage = undefined }) {
    const key = `ec-asuew-outbox-${incidentId}`;
    try { if (storage === undefined) storage = globalThis.localStorage; } catch (_) {}
    let memory = [], database, opening, fallback = false, running = null;
    let mutations = Promise.resolve();
    const listeners = new Set(onChange ? [onChange] : []);
    const millis = () => new Date(now()).getTime();
    const copy = value => JSON.parse(JSON.stringify(value));
    const sorted = items => items.sort((a, b) => Date.parse(a.erfasst_at) - Date.parse(b.erfasst_at) || a.seq - b.seq);
    function mirror() { try { storage?.setItem(key, JSON.stringify(memory)); } catch (_) {} }
    function useFallback() {
      fallback = true;
      try {
        const saved = JSON.parse(storage?.getItem(key) || '[]');
        if (Array.isArray(saved)) memory = saved;
        storage?.setItem(key + '-fallback', '1');
      } catch (_) {}
    }
    function changed() { for (const fn of listeners) { try { fn(); } catch (_) {} } }
    function db() {
      if (opening) return opening;
      opening = new Promise(resolve => {
        let done = false, timer;
        function finish(value) {
          if (done) { if (value) value.close(); return; }
          done = true;
          clearTimeout(timer);
          database = value;
          if (!value) useFallback();
          resolve(value);
        }
        try {
          if (!indexedDB) { finish(null); return; }
          const request = indexedDB.open(dbName, 1);
          request.onupgradeneeded = () => {
            request.result.createObjectStore('outbox', { keyPath: 'id' });
            request.result.createObjectStore('meta');
          };
          request.onsuccess = () => finish(request.result);
          request.onerror = request.onblocked = () => finish(null);
          timer = setTimeout(() => finish(null), openTimeoutMs);
        } catch (_) { finish(null); }
      });
      opening = opening.then(async value => {
        if (!value) return value;
        // Ein voruebergehend blockierter Speicher darf nach dem Neustart keine Vormerkungen verlieren.
        try {
          if (storage?.getItem(key + '-fallback') !== '1') return value;
          const saved = JSON.parse(storage.getItem(key) || '[]');
          if (!Array.isArray(saved) || !saved.length) return value;
          await new Promise((resolve, reject) => {
            const tx = value.transaction(['outbox', 'meta'], 'readwrite');
            const store = tx.objectStore('outbox'), meta = tx.objectStore('meta');
            const request = meta.get('seq');
            request.onsuccess = () => {
              let seq = request.result || 0;
              for (const item of saved) {
                const existing = store.get(item.id);
                existing.onsuccess = () => {
                  if (!existing.result) { store.put({ ...item, seq: ++seq }); meta.put(seq, 'seq'); }
                };
              }
            };
            tx.oncomplete = resolve;
            tx.onerror = tx.onabort = () => reject(tx.error);
          });
          storage.setItem(key + '-fallback', '0');
        } catch (_) { useFallback(); }
        return value;
      });
      return opening;
    }
    async function read() {
      await db();
      if (!fallback) {
        try {
          memory = await new Promise((resolve, reject) => {
            const tx = database.transaction('outbox');
            const request = tx.objectStore('outbox').getAll();
            let items;
            request.onsuccess = () => { items = request.result; };
            tx.oncomplete = () => resolve(items);
            tx.onerror = tx.onabort = () => reject(tx.error);
          });
          mirror();
        } catch (_) { useFallback(); }
      }
      return sorted(copy(memory));
    }
    function mutate(work) {
      const result = mutations.then(work);
      mutations = result.catch(() => {});
      return result;
    }
    async function write(item, remove = false, insert = false) {
      await read();
      if (!fallback) {
        try {
          await new Promise((resolve, reject) => {
            const tx = database.transaction(['outbox', 'meta'], 'readwrite');
            const store = tx.objectStore('outbox');
            if (remove) store.delete(item.id);
            else if (insert) {
              const existing = store.get(item.id);
              existing.onsuccess = () => {
                if (existing.result) { Object.assign(item, existing.result); return; }
                const meta = tx.objectStore('meta'), seq = meta.get('seq');
                seq.onsuccess = () => { item.seq = (seq.result || 0) + 1; meta.put(item.seq, 'seq'); store.put(item); };
              };
            } else store.put(item);
            tx.oncomplete = resolve;
            tx.onerror = tx.onabort = () => reject(tx.error);
          });
          await read();
          changed();
          return;
        } catch (_) { useFallback(); }
      }
      const index = memory.findIndex(x => x.id === item.id);
      if (remove) memory = memory.filter(x => x.id !== item.id);
      else if (insert && index !== -1) Object.assign(item, memory[index]);
      else {
        if (insert) item.seq = Math.max(0, ...memory.map(x => x.seq || 0)) + 1;
        if (index === -1) memory.push(copy(item));
        else memory[index] = copy(item);
      }
      mirror();
      changed();
    }
    async function run() {
      let sent = 0;
      while (true) {
        await mutations;
        const item = (await read()).find(x => x.status === 'wartend');
        if (!item || (item.retry_at || 0) > millis()) return sent;
        const controller = new AbortController();
        let timer, response, message = '';
        try {
          await Promise.race([
            (async () => {
              response = await fetch(item.path, {
                method: 'POST', credentials: 'same-origin', signal: controller.signal,
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: new URLSearchParams({ ...item.fields, client_event_id: item.id,
                  erfasst_at: item.erfasst_at, _csrf: csrfToken() }).toString()
              });
              if (response.status < 200 || response.status >= 400) message = (await response.text()).trim().slice(0, 200);
            })(),
            new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(Error('Zeitüberschreitung')); }, requestTimeoutMs); })
          ]);
        } catch (_) { response = null; message = 'Keine Verbindung zum Server'; }
        finally { clearTimeout(timer); }
        if (response && response.status >= 200 && response.status < 400) {
          await mutate(() => write(item, true));
          sent++;
        } else if (response && [400, 403, 404, 409, 422].includes(response.status)) {
          await mutate(() => write({ ...item, status: 'abgelehnt', versuche: item.versuche + 1, fehler: message }));
        } else {
          const versuche = item.versuche + 1;
          await mutate(() => write({ ...item, versuche, fehler: message,
            retry_at: millis() + Math.min(60000, 5000 * 2 ** (versuche - 1)) }));
          return sent;
        }
      }
    }
    return {
      erfassen(path, fields = {}) {
        let id = fields.client_event_id;
        if (!id) { try { id = uuid(); } catch (_) {} }
        const item = { id: id || zufallsUuid(), path, fields: { ...fields },
          erfasst_at: fields.erfasst_at || new Date(millis() + offsetMs()).toISOString(),
          status: 'wartend', versuche: 0, fehler: '', created_at: new Date(millis()).toISOString() };
        return mutate(async () => { await write(item, false, true); return copy(item); });
      },
      async liste() { await mutations; return read(); },
      async anzahl() { await mutations; return (await read()).length; },
      senden() {
        if (!running) running = run().finally(() => { running = null; });
        return running;
      },
      verwerfen(id) { return mutate(() => write({ id }, true)); },
      abonnieren(fn) { listeners.add(fn); return () => listeners.delete(fn); }
    };
  }
  const api = { createOutbox, zufallsUuid };
  if (typeof window !== 'undefined') window.ECPBreathingOutbox = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
