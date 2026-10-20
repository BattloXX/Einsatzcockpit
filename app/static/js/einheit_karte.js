/* Karte der Einheitenansicht; die Sortierhelfer bleiben bewusst DOM-frei. */

function esc(wert) {
  return String(wert ?? "").replace(/[&<>"']/g, (z) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[z]));
}

function laeuft(auftrag) {
  return Boolean(auftrag.aktuell || auftrag.in_arbeit || auftrag.einheit_status === "in_arbeit");
}

function istOffen(auftrag) {
  return !auftrag.withdrawn_at && !auftrag.zurueckgezogen
    && auftrag.einheit_status !== "abgeschlossen" && auftrag.einheit_status !== "nicht_durchfuehrbar";
}

function distanz(a, b) {
  const rad = Math.PI / 180;
  const dLat = (Number(b.lat) - Number(a.lat)) * rad;
  const dLng = (Number(b.lng) - Number(a.lng)) * rad;
  const x = dLng * Math.cos((Number(a.lat) + Number(b.lat)) * rad / 2);
  return dLat * dLat + x * x;
}

export { esc };

export function berechneReihenfolge(auftraege, standort = null) {
  const sortiert = [...auftraege].sort((a, b) => (a.rang ?? Infinity) - (b.rang ?? Infinity));
  if (!standort) return sortiert;
  // Laufender und vom Server bestimmter nächster Auftrag bleiben fix (Vorgabe der Leitstelle bzw. einheitliche
  // Anzeige in Liste und Übersicht); der Entfernungsvorschlag ordnet nur die Aufträge danach.
  const fix = sortiert.reduce((letzter, auftrag, i) => (laeuft(auftrag) || auftrag.naechster ? i : letzter), -1);
  const start = fix + 1;
  for (let i = start; i < sortiert.length;) {
    const erster = sortiert[i];
    if (!istOffen(erster) || erster.reihenfolge != null) { i += 1; continue; }
    const gruppe = [erster];
    let ende = i + 1;
    while (ende < sortiert.length && istOffen(sortiert[ende]) && sortiert[ende].reihenfolge == null
      && sortiert[ende].priority === erster.priority) {
      gruppe.push(sortiert[ende]);
      ende += 1;
    }
    if (gruppe.length > 1) {
      gruppe.sort((a, b) => distanz(standort, a) - distanz(standort, b));
      sortiert.splice(i, gruppe.length, ...gruppe.map((auftrag) => ({ ...auftrag, vorschlag_entfernung: true })));
    }
    i = ende;
  }
  return sortiert;
}

export function markerArt(auftrag) {
  if (auftrag.withdrawn_at || auftrag.zurueckgezogen) return "zurueckgezogen";
  if (auftrag.einheit_status === "abgeschlossen" || auftrag.einheit_status === "nicht_durchfuehrbar"
    || auftrag.beendet_at) return "fertig";
  // Laufender und nächster Auftrag haben Vorrang; die Erkundung bleibt über eine Zusatzklasse erkennbar.
  if (laeuft(auftrag)) return "in_arbeit";
  if (auftrag.naechster) return "naechster";
  if (auftrag.auftragsart === "erkundung") return "erkundung";
  return "offen";
}

function auftragBezeichnung(auftrag) {
  const name = auftrag.bezeichnung || auftrag.einsatzgrund;
  return [name, auftrag.adresse].filter(Boolean).join(", ") || "Auftrag";
}

export function markerDarstellung(auftrag, nummer) {
  const art = markerArt(auftrag);
  const erkundung = auftrag.auftragsart === "erkundung";
  const klasse = `einheit-kartenmarker--${art}${erkundung ? " einheit-kartenmarker--erkundung" : ""}`;
  const bezeichnung = auftragBezeichnung(auftrag);
  let inhalt = String(nummer || "");
  let iconName = null;
  let titel = `Auftrag: ${bezeichnung}`;
  let groesse = 28;

  if (art === "naechster") {
    iconName = "player-play";
    inhalt = "";
    titel = `Nächster Auftrag: ${bezeichnung}`;
  } else if (art === "in_arbeit") {
    iconName = "player-record";
    inhalt = "";
    titel = `In Arbeit: ${bezeichnung}`;
  } else if (art === "erkundung") {
    titel = `Erkundung: ${bezeichnung}`;
    groesse = 30;
  } else if (art === "fertig") {
    iconName = "check";
    inhalt = "";
    titel = `Fertig: ${bezeichnung}`;
    groesse = 24;
  }
  return { klasse, inhalt, iconName, titel, groesse, anker: [groesse / 2, groesse / 2] };
}

export function navUrl(auftrag) {
  if (auftrag.nav_url) return auftrag.nav_url;
  if (auftrag.gmaps_url) return auftrag.gmaps_url;
  if (auftrag.lat != null && auftrag.lng != null) {
    return `https://www.google.com/maps/dir/?api=1&destination=${auftrag.lat},${auftrag.lng}`;
  }
  return auftrag.adresse
    ? `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(auftrag.adresse)}` : null;
}

export class EinheitKarte {
  constructor(container, { onSelect = () => {} } = {}) {
    this.container = container;
    this.onSelect = onSelect;
    this.marker = new Map();
    this.fertige = false;
    this.standort = null;
    this.watchId = null;
    this.map = window.L.map(container, { zoomControl: true, attributionControl: true });
    window.EinsatzcockpitMapConfig?.addOsmTileLayer(this.map);
    this.layer = window.L.layerGroup().addTo(this.map);
    this.map.setView([47.5, 13.5], 7);
  }

  icon(auftrag, nummer) {
    const darstellung = markerDarstellung(auftrag, nummer);
    return window.L.divIcon({
      className: `einheit-kartenmarker ${darstellung.klasse}`,
      html: `<span title="${esc(darstellung.titel)}" aria-label="${esc(darstellung.titel)}">`
        + `${darstellung.iconName ? ecpIcon(darstellung.iconName, {size: "xs"}) : esc(darstellung.inhalt)}</span>`,
      iconSize: [darstellung.groesse, darstellung.groesse],
      iconAnchor: darstellung.anker,
      popupAnchor: [0, -darstellung.groesse / 2],
    });
  }

  aktualisieren(auftraege, { fertige = false, standort = null } = {}) {
    this.fertige = fertige;
    this.standort = standort || this.standort;
    this.layer.clearLayers(); this.marker.clear();
    const reihenfolge = berechneReihenfolge(auftraege, this.standort);
    const punkte = [];
    const linie = [];
    let nummer = 0;
    for (const auftrag of reihenfolge) {
      const art = markerArt(auftrag);
      const offen = art !== "zurueckgezogen" && art !== "fertig";
      if (offen) nummer += 1;  // Nummer folgt der angezeigten (ggf. nach Entfernung vorgeschlagenen) Reihenfolge
      if (art === "zurueckgezogen" || (art === "fertig" && !fertige)
        || auftrag.lat == null || auftrag.lng == null) continue;
      const latlng = [Number(auftrag.lat), Number(auftrag.lng)];
      punkte.push(latlng);
      if (offen) linie.push(latlng);
      const darstellung = markerDarstellung(auftrag, offen ? nummer : null);
      const marker = window.L.marker(latlng, {
        icon: this.icon(auftrag, offen ? nummer : null), title: darstellung.titel, alt: darstellung.titel,
      }).addTo(this.layer);
      marker.bindPopup(this.popup(auftrag));
      marker.on("click", () => this.onSelect(auftrag));
      this.marker.set(Number(auftrag.dispatch_id), marker);
    }
    if (linie.length > 1) {
      window.L.polyline(linie, { dashArray: "6 7", color: "#475569", weight: 3 }).addTo(this.layer);
    }
    // Nur neu einpassen, wenn sich die Marker ändern – sonst springt die Karte bei jedem Abruf/Standort-Update.
    const signatur = punkte.map((p) => p.join(",")).join(";");
    if (punkte.length && signatur !== this.signatur) this.map.fitBounds(punkte, { padding: [28, 28], maxZoom: 14 });
    this.signatur = signatur;
    this.zeigeStandort();
    requestAnimationFrame(() => this.map.invalidateSize());
  }

  popup(auftrag) {
    const nav = navUrl(auftrag);
    const titel = auftrag.bezeichnung || auftrag.einsatzgrund || "Auftrag";
    const vorschlag = auftrag.vorschlag_entfernung ? "<p>Vorschlag (Entfernung)</p>" : "";
    const details = `${auftrag.priority || "ohne Priorität"} · ${auftrag.auftragsart_label || ""}`
      + ` · ${auftrag.einheit_status_label || ""}`;
    const navigation = nav
      ? ` <a href="${esc(nav)}" target="_blank" rel="noopener">Navigation starten</a>` : "";
    return `<strong>${esc(titel)}</strong><br>${esc(auftrag.adresse)}<br>${esc(details)}${vorschlag}`
      + `<p><button type="button" data-dispatch-id="${esc(auftrag.dispatch_id)}">Auftrag öffnen</button>`
      + `${navigation}</p>`;
  }

  hervorheben(dispatchId) { this.marker.get(Number(dispatchId))?.openPopup(); }
  zentrieren() { if (this.standort) this.map.setView([this.standort.lat, this.standort.lng], 15); }

  starteStandort(onError, onPosition) {
    if (this.watchId != null || !navigator.geolocation) return;
    this.watchId = navigator.geolocation.watchPosition((position) => {
      this.standort = {
        lat: position.coords.latitude, lng: position.coords.longitude, genauigkeit: position.coords.accuracy,
      };
      this.zeigeStandort();
      onPosition?.(this.standort);
    }, () => onError?.(), { enableHighAccuracy: true, maximumAge: 30000 });
  }

  stoppeStandort() {
    if (this.watchId != null) navigator.geolocation.clearWatch(this.watchId);
    this.watchId = null;
  }
  zeigeStandort() {
    if (!this.standort) return;
    this.eigeneMarker?.remove(); this.genauigkeit?.remove();
    const latlng = [this.standort.lat, this.standort.lng];
    this.eigeneMarker = window.L.marker(latlng, {
      icon: window.L.divIcon({
        className: "einheit-standortmarker", html: "<span></span>", iconSize: [20, 20], iconAnchor: [10, 10],
      }),
      zIndexOffset: 1000, title: "Mein Standort", alt: "Mein Standort",
    }).addTo(this.map);
    this.genauigkeit = window.L.circle(latlng, {
      radius: this.standort.genauigkeit || 0, color: "#2563eb", fillOpacity: 0.08,
    }).addTo(this.map);
  }
}
