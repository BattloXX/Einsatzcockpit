/* Reiner Spiegel von Android AsuewLogic; Zeiten sind Server-Millisekunden.
 * Lokale Ereignisse sind eine Ueberlagerung, keine Servermutationen/Versionssperren.
 * Ganzzahlen ausserhalb des sicheren JS-Bereichs werden beim Parsen abgelehnt.
 */
(function () {
  'use strict';
  const stages = ['one_third', 'two_third', 'frei'];
  const ackKinds = ['max_time', 'withdraw'];
  const severity = ['notfall', 'withdraw', 'funk_verloren', 'max_exceeded', 'two_third_due', 'one_third_due'];
  const isValidLocalEvent = e => e.troopId > 0 && e.atServerMs > 0 &&
    ['kontrolle', 'druck', 'ack'].includes(e.kind) &&
    (e.stufe == null || stages.includes(e.stufe)) &&
    (e.ackKind == null || ackKinds.includes(e.ackKind)) &&
    (e.kind !== 'kontrolle' || e.stufe != null) && (e.kind !== 'ack' || e.ackKind != null);

  function applyLocalEvents(state, events) {
    if (!events.length) return state;
    return { ...state, troops: state.troops.map(original => {
      let t = { ...original, kontrollen: [...original.kontrollen], serverWarnings: [...original.serverWarnings] };
      for (const e of events) {
        if (e.troopId !== t.id || (t.entryAtMs != null && e.atServerMs < t.entryAtMs)) continue;
        if (e.kind === 'kontrolle' && stages.includes(e.stufe)) {
          if (!t.kontrollen.some(k => k.stufe === e.stufe && k.art === 'funk')) {
            t.kontrollen.push({ stufe: e.stufe, art: 'funk', erledigtAtMs: e.atServerMs, niedrigsterDruck: null });
          }
          const warning = { one_third: 'one_third_due', two_third: 'two_third_due' }[e.stufe];
          t.serverWarnings = t.serverWarnings.filter(w => w !== warning);
        } else if (e.kind === 'druck') {
          t.lastPressureAtMs = Math.max(t.lastPressureAtMs ?? e.atServerMs, e.atServerMs);
          t.serverWarnings = t.serverWarnings.filter(w => {
            const seconds = { one_third_due: t.oneThirdSeconds, two_third_due: t.twoThirdSeconds }[w];
            return t.entryAtMs == null || seconds == null || t.lastPressureAtMs < t.entryAtMs + seconds * 1000;
          });
        } else if (e.kind === 'ack') {
          if (e.ackKind === 'max_time') {
            t.maxTimeAcked = true;
            t.serverWarnings = t.serverWarnings.filter(w => w !== 'max_exceeded');
          } else if (e.ackKind === 'withdraw') t.serverWarnings = t.serverWarnings.filter(w => w !== 'withdraw');
        }
      }
      return t;
    }) };
  }

  function pruneLocalEvents(state, events, nowServerMs) {
    return events.filter(e => {
      const t = state.troops.find(t => t.id === e.troopId);
      if (!t || e.atServerMs <= nowServerMs - 12 * 60 * 60000 ||
          (t.entryAtMs != null && e.atServerMs < t.entryAtMs)) return false;
      if (e.kind === 'kontrolle') return !t.kontrollen.some(k => k.stufe === e.stufe && k.art === 'funk');
      if (e.kind === 'druck') return t.lastPressureAtMs == null || t.lastPressureAtMs < e.atServerMs;
      if (e.kind === 'ack' && e.ackKind === 'max_time') return !t.maxTimeAcked;
      if (e.kind === 'ack' && e.ackKind === 'withdraw') return t.serverWarnings.includes('withdraw');
      return true;
    });
  }
  const ordered = events => [...events].sort((a, b) => a.atServerMs - b.atServerMs).slice(-200);
  function serializeLocalEvents(events) {
    return JSON.stringify(ordered(events).map(e => ({ troopId: e.troopId, kind: e.kind,
      stufe: e.stufe ?? null, ackKind: e.ackKind ?? null, atServerMs: e.atServerMs })));
  }
  function parseLocalEvents(json) {
    try {
      const root = JSON.parse(json);
      if (!Array.isArray(root)) return [];
      return ordered(root.flatMap(item => {
        try {
          const e = { troopId: integer(item.troopId), kind: string(item.kind),
            stufe: optional(item.stufe, string), ackKind: optional(item.ackKind, string),
            atServerMs: integer(item.atServerMs) };
          return isValidLocalEvent(e) ? [e] : [];
        } catch (_) { return []; }
      }));
    } catch (_) { return []; }
  }
  function string(v) { if (typeof v !== 'string') throw Error('Text erwartet'); return v; }
  function integer(v) { if (!Number.isSafeInteger(v)) throw Error('Ganzzahl erwartet'); return v; }
  function number(v) { if (typeof v !== 'number' || !Number.isFinite(v)) throw Error('Zahl erwartet'); return v; }
  function array(v) { if (!Array.isArray(v)) throw Error('Liste erwartet'); return v; }
  const optional = (v, parse) => v == null ? null : parse(v);
  const bool = v => v === true || (typeof v === 'string' && v.toLowerCase() === 'true');
  function utc(v) {
    const match = string(v).match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z$/);
    if (!match) throw Error('UTC erwartet');
    const canonical = match[1] + '.' + (match[2] || '').slice(0, 3).padEnd(3, '0') + 'Z';
    const time = Date.parse(canonical);
    if (!Number.isFinite(time) || new Date(time).toISOString() !== canonical) throw Error('UTC ungueltig');
    return time;
  }
  function parseTroop(t) {
    const result = { id: integer(t.id), name: string(t.name), status: string(t.status),
      isSicherheitstrupp: bool(t.is_sicherheitstrupp) };
    for (const [dest, src] of Object.entries({ entryAtMs: 'entry_at', notfallAtMs: 'notfall_at',
      funkVerlorenAtMs: 'funk_verloren_at', lastMeldungAtMs: 'last_meldung_at', lastPressureAtMs: 'last_pressure_at' })) {
      result[dest] = optional(t[src], utc);
    }
    for (const [dest, src] of Object.entries({ plannedDurationMin: 'planned_duration_min',
      oneThirdSeconds: 'one_third_seconds', twoThirdSeconds: 'two_third_seconds', maxSeconds: 'max_seconds' })) {
      result[dest] = optional(t[src], integer);
    }
    if (result.plannedDurationMin < -2147483648 || result.plannedDurationMin > 2147483647) result.plannedDurationMin = null;
    result.withdrawPressCalc = optional(t.withdraw_press_calc, number);
    result.maxTimeAcked = bool(t.max_time_acked);
    result.serverWarnings = (optional(t.warnings, array) || []).filter(w => typeof w === 'string');
    result.kontrollen = (optional(t.kontrollen, array) || []).map(k => ({ stufe: string(k.stufe),
      art: string(k.art), erledigtAtMs: utc(k.erledigt_at), niedrigsterDruck: optional(k.niedrigster_druck, number) }));
    result.members = (optional(t.members, array) || []).map(m => ({ id: integer(m.id), name: string(m.name),
      role: string(m.role), startPress: optional(m.start_press, number), currentPress: optional(m.current_press, number),
      withdrawPress: optional(m.withdraw_press, number) }));
    return result;
  }
  function parseState(json) {
    try {
      const r = JSON.parse(json);
      return { serverTimeMs: utc(r.server_time), incidentId: integer(r.incident_id),
        appPflicht: bool(r.asuew_app_pflicht), troops: array(r.troops).map(parseTroop) };
    } catch (_) { return null; }
  }
  const isActive = t => t.entryAtMs != null && ['im_einsatz', 'rueckzug'].includes(t.status);
  function pendingDeadlines(t) {
    if (!isActive(t)) return [];
    const result = [];
    if (t.maxSeconds != null && !t.maxTimeAcked) result.push({ troopId: t.id, kind: 'max_exceeded', atServerMs: t.entryAtMs + t.maxSeconds * 1000 });
    for (const [seconds, kind, stufe] of [[t.twoThirdSeconds, 'two_third_due', 'two_third'], [t.oneThirdSeconds, 'one_third_due', 'one_third']]) {
      if (seconds == null) continue;
      const at = t.entryAtMs + seconds * 1000;
      if (!t.kontrollen.some(k => k.art === 'funk' && k.stufe === stufe) && (t.lastPressureAtMs == null || t.lastPressureAtMs < at)) {
        result.push({ troopId: t.id, kind, atServerMs: at });
      }
    }
    return result;
  }
  const timeWarnings = (troop, now) => pendingDeadlines(troop).filter(d => now >= d.atServerMs).map(d => d.kind);
  function activeAlarms(state, now) {
    const alarms = new Map();
    for (const t of state.troops) {
      const serverKinds = t.serverWarnings.filter(k => severity.includes(k) &&
        (isActive(t) || (k === 'notfall' && t.notfallAtMs != null) || (k === 'funk_verloren' && t.funkVerlorenAtMs != null)));
      for (const kind of [...serverKinds, ...timeWarnings(t, now)]) {
        const key = `${t.id}:${kind}:${t.entryAtMs ?? 0}`;
        if (!alarms.has(key)) alarms.set(key, { troopId: t.id, troopName: t.name, kind, key });
      }
    }
    return [...alarms.values()].sort((a, b) => severity.indexOf(a.kind) - severity.indexOf(b.kind) || a.troopId - b.troopId);
  }
  const nextDeadlines = (state, now) => state.troops.flatMap(pendingDeadlines).filter(d => d.atServerMs > now)
    .sort((a, b) => a.atServerMs - b.atServerMs || a.troopId - b.troopId || a.kind.localeCompare(b.kind));
  const nextWakeupServerMs = (state, now) => nextDeadlines(state, now)[0]?.atServerMs ?? null;
  const connectionStatus = (last, now) => last == null ? 'verbindet' : now - last <= 20000 ? 'ok' : now - last <= 60000 ? 'gestoert' : 'verloren';
  const batteryStatus = (percent, charging) => percent == null ? 'unbekannt' : charging ? 'ok' : percent < 10 ? 'kritisch' : percent < 20 ? 'warnung' : 'ok';
  function notificationLines(state, now) {
    return state.troops.filter(isActive).map(t => {
      const seconds = Math.max(0, Math.trunc((now - t.entryAtMs) / 1000));
      const parts = [`${t.name}: ${String(Math.trunc(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')} min`];
      for (const [field, label] of [['currentPress', 'niedrigster Druck'], ['withdrawPress', 'Rückzug bei']]) {
        const values = t.members.map(m => m[field]).filter(v => v != null);
        if (values.length) parts.push(`${label} ${String(Math.min(...values)).replace('.', ',')} bar`);
      }
      return parts.join(', ');
    });
  }
  const api = { isValidLocalEvent, applyLocalEvents, pruneLocalEvents, serializeLocalEvents, parseLocalEvents,
    parseState, timeWarnings, activeAlarms, nextDeadlines, nextWakeupServerMs, connectionStatus, batteryStatus,
    notificationLines, clockOffsetMs: (server, local) => server - local,
    serverNow: (local, offset) => local + offset, serverToLocal: (server, offset) => server - offset };
  if (typeof module !== 'undefined') module.exports = api;
  if (typeof window !== 'undefined') window.ECPAsuewLogik = api;
})();
