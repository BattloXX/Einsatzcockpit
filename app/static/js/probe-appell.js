(() => {
  const root = document.querySelector('#appell');
  if (!root) return;
  const open = 'nicht_erfasst', labels = {anwesend: '✓ ANWESEND', entschuldigt: '✉ ENTSCHULDIGT', unentschuldigt: '✗ UNENTSCHULDIGT', nicht_erfasst: '○ OFFEN'};
  const people = () => [...root.querySelectorAll('[data-person]')];
  const sync = root.querySelector('[data-sync]'); let onlyOpen = false, groupFilter = '', pending = new Map();
  const csrf = root.dataset.csrf, endpoint = root.dataset.statusUrl, canEdit = root.dataset.canEdit === 'true';
  function stats() { const all = people(), count = s => all.filter(p => p.dataset.status === s).length, total = all.length, opened = count(open), done = total - opened, pct = total ? Math.round(done * 100 / total) : 100;
    ['anwesend','entschuldigt','unentschuldigt'].forEach(s => root.querySelector(`[data-stat="${s}"]`).textContent = `${count(s)} ${labels[s].split(' ')[0]}`);
    root.querySelector('[data-stat="offen"]').textContent = `${opened} offen`; root.querySelector('[data-open-count]').textContent = opened; root.querySelector('[data-progress]').style.width = `${pct}%`; root.querySelector('[data-percent]').textContent = `${pct} %`;
  }
  function inGroup(p) { return !groupFilter || p.dataset.groups.split(',').includes(groupFilter); }
  function current() { return people().find(p => p.dataset.status === open && inGroup(p)) || null; }
  function paint() { const next = current(); people().forEach(p => { const active = p === next; p.classList.toggle('appell-person--current', active); p.querySelector('.appell-current').hidden = !active; p.hidden = (onlyOpen && p.dataset.status !== open) || !inGroup(p); });
    root.querySelector('[data-filter]').setAttribute('aria-pressed', String(onlyOpen)); root.querySelector('[data-filter]').classList.toggle('is-active', onlyOpen);
    root.querySelector('[data-filter]').childNodes[0].nodeValue = `${onlyOpen ? '✓ ' : ''}Nur Offene (`;
    const nextText = next ? `${next.dataset.name} · Pos. ${next.dataset.position} von ${people().length}` : 'Alle Personen erfasst'; root.querySelector('[data-next]').textContent = nextText; root.querySelector('[data-next-label]').textContent = next ? 'Nächster Offener' : 'Appell vollständig'; stats();
  }
  function jump() { const next = current(); if (next) next.scrollIntoView({behavior: 'smooth', block: 'center'}); }
  function update(person, status) { person.dataset.status = status; person.className = `appell-person appell-person--${status}`; person.querySelector('[data-status-label]').textContent = labels[status]; paint(); jump(); if (navigator.vibrate) navigator.vibrate(20); }
  async function save(person, status) { const key = person.dataset.id; pending.set(key, {person, status}); sync.textContent = `Synchronisierung …${pending.size > 1 ? ` (${pending.size})` : ''}`;
    try { const response = await fetch(`${endpoint}/${key}`, {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify({status})}); if (!response.ok) throw new Error(); pending.delete(key); sync.textContent = pending.size ? `⚠ ${pending.size} Änderungen noch nicht synchronisiert` : ''; }
    catch (_) { sync.innerHTML = `⚠ ${pending.size} Änderungen noch nicht synchronisiert <button type="button" data-retry>Erneut versuchen</button>`; }
  }
  root.addEventListener('click', event => { const button = event.target.closest('[data-status-action]'); if (button && canEdit) { const person = button.closest('[data-person]'); const status = button.dataset.statusAction; update(person, status); save(person, status); return; }
    if (event.target.closest('[data-filter]')) { onlyOpen = !onlyOpen; paint(); return; } if (event.target.closest('[data-jump]')) { jump(); return; }
    const letter = event.target.closest('[data-letter]'); if (letter) { const p = people().find(x => x.dataset.letter === letter.dataset.letter); if (p) p.scrollIntoView({behavior: 'smooth', block: 'start'}); return; }
    if (event.target.closest('[data-retry]')) { pending.forEach(({person, status}) => save(person, status)); return; } if (event.target.closest('[data-complete]')) complete(false); if (event.target.closest('[data-close]')) root.querySelector('[data-complete-dialog]').close(); if (event.target.closest('[data-force]')) complete(true); });
  async function complete(force) { if (pending.size) { sync.textContent = `⚠ ${pending.size} Änderungen noch nicht synchronisiert`; return; } const response = await fetch(`${endpoint}/abschliessen`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify({force})}); const body = await response.json(); const dialog = root.querySelector('[data-complete-dialog]'), bodyEl = root.querySelector('[data-dialog-body]'), forceButton = root.querySelector('[data-force]'); if (response.status === 409) { bodyEl.innerHTML = `<h2>Appell noch nicht vollständig</h2><p>Es sind noch <strong>${body.offen}</strong> Personen nicht erfasst.</p>`; forceButton.hidden = false; dialog.showModal(); return; } bodyEl.innerHTML = `<h2>Appell ${body.vollstaendig ? 'vollständig' : 'abgeschlossen'}</h2><p>Die Teilnehmerdaten sind in der Probe gespeichert.</p>`; forceButton.hidden = true; dialog.showModal(); }
  root.querySelector('[data-group-filter]')?.addEventListener('change', event => { groupFilter = event.target.value; paint(); jump(); });
  document.addEventListener('keydown', event => { if (event.target.matches('input, textarea, select')) return; const p = current(); const key = event.key.toLowerCase(); if (canEdit && p && ({a:'anwesend',e:'entschuldigt',u:'unentschuldigt'}[key])) { event.preventDefault(); const s = ({a:'anwesend',e:'entschuldigt',u:'unentschuldigt'}[key]); update(p, s); save(p, s); } if (event.key === 'Enter' || event.key === 'ArrowDown') { event.preventDefault(); jump(); } });
  paint(); jump();
})();
