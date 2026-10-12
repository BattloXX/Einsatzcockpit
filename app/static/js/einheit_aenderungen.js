/* Reine Zustandsvergleiche fuer Hinweise im Einheitenmodus. */

const PRIORITAET = { sofort: 0, dringend: 1, normal: 2, aufschiebbar: 3 };

function kategorien(zustand = {}) {
  return zustand.kategorien || zustand;
}

function alle(zustand = {}) {
  const daten = kategorien(zustand);
  return [daten.aktuell, ...(daten.weitere || []), ...(daten.abgeschlossen || []), ...(daten.zurueckgezogen || [])]
    .filter(Boolean);
}

function nachId(zustand, id) {
  return alle(zustand).find((auftrag) => Number(auftrag.dispatch_id) === Number(id)) || null;
}

export function istGeaendert(auftrag, gesehen) {
  if (!auftrag || auftrag.version === undefined || gesehen === undefined || gesehen === null) return false;
  return Number(auftrag.version) > Number(gesehen);
}

export function vergleicheZustand(vorher = {}, nachher = {}) {
  const vorherKategorien = kategorien(vorher);
  const nachherKategorien = kategorien(nachher);
  const vorherNaechsterId = vorher.naechster_dispatch_id;
  const nachherNaechsterId = nachher.naechster_dispatch_id;
  const naechster = nachId(nachher, nachherNaechsterId);
  // Nur eine echte Umreihung zählt: der bisherige nächste Auftrag wartet weiter, steht aber nicht mehr vorne.
  // Normaler Fortschritt (begonnen, fertig, zurückgezogen) löst keinen Hinweis aus.
  const bisherigerWartetNoch = (nachherKategorien.weitere || [])
    .some((auftrag) => Number(auftrag.dispatch_id) === Number(vorherNaechsterId));
  const reihenfolgeGeaendert = Boolean(
    vorherNaechsterId && nachherNaechsterId && Number(vorherNaechsterId) !== Number(nachherNaechsterId)
    && bisherigerWartetNoch
  );
  const vorherIds = new Set(alle(vorher).map((auftrag) => Number(auftrag.dispatch_id)));
  const laufender = vorherKategorien.aktuell || nachherKategorien.aktuell;
  const laufendePrioritaet = PRIORITAET[laufender?.priority];
  const hoeherePrioritaet = laufender && laufendePrioritaet !== undefined
    ? alle(nachher).filter((auftrag) => (
      !vorherIds.has(Number(auftrag.dispatch_id))
      && !nachherKategorien.zurueckgezogen?.some((item) => item.dispatch_id === auftrag.dispatch_id)
      && PRIORITAET[auftrag.priority] !== undefined
      && PRIORITAET[auftrag.priority] < laufendePrioritaet
    ))
    : [];
  const vorherAktiv = new Map(
    [vorherKategorien.aktuell, ...(vorherKategorien.weitere || [])].filter(Boolean)
      .map((auftrag) => [Number(auftrag.dispatch_id), auftrag])
  );
  const zurueckgezogen = (nachherKategorien.zurueckgezogen || [])
    .filter((auftrag) => vorherAktiv.has(Number(auftrag.dispatch_id)));

  return { reihenfolgeGeaendert, naechster, hoeherePrioritaet, zurueckgezogen };
}
