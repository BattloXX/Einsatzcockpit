/* Karte der Einheitenansicht; die Sortierhelfer bleiben bewusst DOM-frei. */
import { auftragsartAnzeige, prioritaetAnzeige } from "./einheit_darstellung.js";

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
  // Statuskennzeichnung bleibt unabhängig von der farbigen Auftragsart.
  if (laeuft(auftrag)) return "in_arbeit";
  if (auftrag.naechster) return "naechster";
  return "offen";
}

function auftragBezeichnung(auftrag) {
  const name = auftrag.bezeichnung || auftrag.einsatzgrund;
  return [name, auftrag.adresse].filter(Boolean).join(", ") || "Auftrag";
}

export function markerDarstellung(auftrag, nummer) {
  const art = markerArt(auftrag);
  const typ = auftragsartAnzeige(auftrag.auftragsart);
  const prioritaet = prioritaetAnzeige(auftrag.priority);
  const klasse = `einheit-kartenmarker--${art} ${typ.klasse} ${prioritaet.klasse}`;
  const bezeichnung = auftragBezeichnung(auftrag);
  let statusIconName = null;
  let statusText = auftrag.einheit_status_label || "Offen";
  if (art === "naechster") {
    statusIconName = "player-play";
    statusText = "Nächster Auftrag";
  } else if (art === "in_arbeit") {
    statusIconName = "player-record";
    statusText = `Aktuell · ${auftrag.einheit_status_label || "In Arbeit"}`;
  } else if (art === "fertig") {
    statusIconName = "check";
    statusText = auftrag.einheit_status_label || "Fertig";
  }
  const titel = `${typ.label} · ${statusText} · Priorität: ${prioritaet.label} · ${bezeichnung}`;
  return {
    klasse, inhalt: String(nummer || ""), iconName: typ.icon, label: typ.label,
    statusIconName, statusText, prioritaet, titel,
    groesse: art === "in_arbeit" ? 56 : 48, anker: art === "in_arbeit" ? [28, 28] : [24, 24],
  };
}

export function markerIcon(auftrag, nummer, anzahl = 1) {
  const d = markerDarstellung(auftrag, nummer);
  const status = d.statusIconName ? window.ecpIcon(d.statusIconName, { size: "xs" }) : "";
  return window.L.divIcon({
    className: `einheit-kartenmarker ${d.klasse}${auftrag.ausgewaehlt ? " einheit-kartenmarker--auswahl" : ""}`,
    html: `<span class="einheit-marker__punkt" aria-label="${esc(d.titel)}">`
      + window.ecpIcon(d.iconName, { size: "sm" })
      + `</span>${anzahl > 1 ? `<span class="einheit-marker__anzahl">${anzahl}</span>` : ""}`
      + `<span class="einheit-marker__beschriftung">`
      + `<span class="einheit-artchip">${esc(d.label)}</span>`
      + `<span class="einheit-marker__prioritaet">Priorität: ${esc(d.prioritaet.label)}</span>`
      + `<span class="einheit-marker__status">${status}${esc(d.inhalt ? d.inhalt + ' · ' : '')}${esc(d.statusText)}</span>`
      + `</span>`,
    iconSize: [d.groesse, d.groesse], iconAnchor: d.anker, popupAnchor: [0, -24],
  });
}

export function hatKoordinaten(ort) {
  const gueltig = (wert, grenze) => (typeof wert === "number" || typeof wert === "string" && wert.trim() !== "")
    && Number.isFinite(Number(wert)) && Math.abs(Number(wert)) <= grenze;
  return Boolean(ort && gueltig(ort.lat, 90) && gueltig(ort.lng, 180));
}

export function navUrl(auftrag) {
  if (!auftrag) return null;
  // Das Ziel kommt aus den bestehenden Einsatzdaten; niemals ungeprüfte URL-Schemata aus einem Cache öffnen.
  if (hatKoordinaten(auftrag)) {
    return `https://www.google.com/maps/dir/?api=1&destination=${Number(auftrag.lat)},${Number(auftrag.lng)}`;
  }
  return auftrag.adresse?.trim()
    ? `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(auftrag.adresse)}` : null;
}

export function zielhinweiseLayer(map, daten, vorher = null) {
  vorher?.remove();
  const layer = window.L.layerGroup().addTo(map);
  for (const gefahr of daten?.gefahren || []) {
    if (!hatKoordinaten(gefahr)) continue;
    window.L.marker([Number(gefahr.lat), Number(gefahr.lng)], {
      icon: window.L.divIcon({ className: "einheit-gefahrmarker", html: "<span>!</span>",
        iconSize: [48, 48], iconAnchor: [24, 24] }),
      title: `Gefahr: ${gefahr.title}`, alt: `Gefahr: ${gefahr.title}`, zIndexOffset: 500,
    }).bindPopup(`<strong>Gefahr</strong><p>${esc(gefahr.title)}</p>`, {
      className: "einheit-kartenpopup",
    }).addTo(layer);
  }
  for (const sperre of daten?.strassensperren || []) {
    if (!sperre.geometry) continue;
    window.L.geoJSON(sperre.geometry, {
      style: { color: "#d83030", weight: 6, dashArray: "8 5", fillOpacity: 0.15 },
      pointToLayer: (_feature, latlng) => window.L.marker(latlng, {
        icon: window.L.divIcon({ className: "einheit-sperrmarker", html: "<span>×</span>",
          iconSize: [48, 48], iconAnchor: [24, 24] }),
        title: `Straßensperre: ${sperre.title}`, alt: `Straßensperre: ${sperre.title}`,
      }),
    }).bindPopup(`<strong>Straßensperre</strong><p>${esc(sperre.title)} · ${esc(sperre.restriction_label)}</p>`, {
      className: "einheit-kartenpopup",
    }).addTo(layer);
  }
  return layer;
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

  icon(auftrag, nummer, anzahl = 1) {
    return markerIcon(auftrag, nummer, anzahl);
  }

  aktualisieren(auftraege, { fertige = false, standort = null, ausgewaehlt = null } = {}) {
    this.fertige = fertige;
    this.standort = standort || this.standort;
    // GPS darf die von der Einsatzleitung vorgegebene Reihenfolge nicht ändern.
    const reihenfolge = berechneReihenfolge(auftraege);
    const sichtbar = reihenfolge.filter(a => markerArt(a) !== "zurueckgezogen"
      && (markerArt(a) !== "fertig" || fertige) && hatKoordinaten(a));
    this.auftraege = new Map(sichtbar.map(a => [Number(a.dispatch_id), a]));
    const punkte = [], linie = [];
    let nummer = 0;
    for (const auftrag of reihenfolge) {
      const art = markerArt(auftrag);
      const offen = art !== "zurueckgezogen" && art !== "fertig";
      if (offen) nummer += 1;
      const id = Number(auftrag.dispatch_id);
      if (!this.auftraege.has(id)) continue;
      const latlng = [Number(auftrag.lat), Number(auftrag.lng)];
      punkte.push(latlng);
      if (offen) linie.push(latlng);
      const gruppe = sichtbar.filter(a => Number(a.lat) === latlng[0] && Number(a.lng) === latlng[1]);
      const daten = { ...auftrag, ausgewaehlt: id === Number(ausgewaehlt) };
      const darstellung = markerDarstellung(daten, offen ? nummer : null);
      const signatur = JSON.stringify([daten, nummer, gruppe]);
      let marker = this.marker.get(id);
      if (!marker) {
        marker = window.L.marker(latlng, {
          icon: this.icon(daten, offen ? nummer : null, gruppe.length),
          title: darstellung.titel, alt: darstellung.titel,
        }).addTo(this.layer);
        marker.on("click", () => this.onSelect(this.auftraege.get(id)));
        this.marker.set(id, marker);
      }
      if (marker.einheitSignatur !== signatur) {
        marker.setLatLng(latlng).setIcon(this.icon(daten, offen ? nummer : null, gruppe.length));
        marker.bindPopup(this.popup(auftrag, gruppe), {
          className: "einheit-kartenpopup", maxHeight: 300,
        });
        const element = marker.getElement();
        element?.setAttribute("aria-label", darstellung.titel);
        element?.setAttribute("title", darstellung.titel);
        marker.einheitSignatur = signatur;
      }
      marker.setZIndexOffset(art === "in_arbeit" ? 1200 : daten.ausgewaehlt ? 1100 : auftrag.naechster ? 900 : 0);
    }
    for (const [id, marker] of this.marker) {
      if (!this.auftraege.has(id)) { marker.remove(); this.marker.delete(id); }
    }
    const linienSignatur = JSON.stringify(linie);
    if (linienSignatur !== this.linienSignatur) {
      this.linie?.remove();
      this.linie = linie.length > 1
        ? window.L.polyline(linie, { dashArray: "6 7", color: "#475569", weight: 3 }).addTo(this.layer) : null;
      this.linienSignatur = linienSignatur;
    }
    const signatur = punkte.map(p => p.join(",")).sort().join(";");
    this.punkte = punkte;
    if (punkte.length && signatur !== this.signatur) this.alleZeigen();
    this.signatur = signatur;
    this.zeigeStandort();
    requestAnimationFrame(() => this.map.invalidateSize());
  }

  alleZeigen() {
    if (this.punkte?.length) this.map.fitBounds(this.punkte, {
      paddingTopLeft: [65, 65], paddingBottomRight: [65, 125], maxZoom: 14,
    });
  }

  zielZeigen(id) {
    const marker = this.marker.get(Number(id));
    if (marker) this.map.setView(marker.getLatLng(), 16);
  }

  hinweise(daten) {
    const signatur = JSON.stringify([daten?.gefahren, daten?.strassensperren]);
    if (signatur === this.hinweisSignatur) return;
    this.hinweisLayer = zielhinweiseLayer(this.map, daten, this.hinweisLayer);
    this.hinweisSignatur = signatur;
  }

  popup(auftrag, gruppe = []) {
    if (gruppe.length > 1) return `<strong>${gruppe.length} Aufträge an dieser Position</strong>`
      + gruppe.map(a => `<section class="einheit-kartenpopup__auftrag">${this.popup(a)}</section>`).join("");
    const nav = navUrl(auftrag);
    const titel = auftrag.bezeichnung || auftrag.einsatzgrund || "Auftrag";
    const vorschlag = auftrag.vorschlag_entfernung ? "<p>Vorschlag (Entfernung)</p>" : "";
    const typ = auftragsartAnzeige(auftrag.auftragsart);
    const prioritaet = prioritaetAnzeige(auftrag.priority);
    const artchip = `<span class="einheit-artchip ${typ.klasse}">`
      + window.ecpIcon(typ.icon, { size: "sm" }) + esc(typ.label) + "</span>";
    const priochip = `<span class="einheit-chip ${prioritaet.chipKlasse}">Priorität: ${esc(prioritaet.label)}</span>`;
    const navigation = nav
      ? `<a class="btn btn--ghost" href="${esc(nav)}" target="_blank" rel="noopener">Navigation starten</a>` : "";
    return `${artchip}<br><strong>${esc(titel)}</strong><br>${esc(auftrag.adresse)}<br>${priochip}`
      + `<br>${esc(auftrag.einheit_status_label)}${vorschlag}`
      + (auftrag.gefahren || []).map(g => `<p class="einheit-warning">Gefahr: ${esc(g.title)}</p>`).join("")
      + `<p>ECP-Sperren werden von externer Navigation nicht automatisch umgangen. Zufahrt prüfen.</p>`
      + `<p class="einheit-kartenpopup__aktionen"><button type="button" class="btn btn--ghost"`
      + ` data-dispatch-id="${esc(auftrag.dispatch_id)}">Auftrag öffnen</button>`
      + `${navigation}</p>`;
  }

  hervorheben(dispatchId) { this.marker.get(Number(dispatchId))?.openPopup(); }
  zentrieren() { if (this.standort) this.map.setView([this.standort.lat, this.standort.lng], 15); }

  starteStandort(onError, onPosition) {
    if (this.watchId != null) return;
    if (!navigator.geolocation) { onError?.(); return; }
    const token = this.standortToken = (this.standortToken || 0) + 1;
    this.watchId = navigator.geolocation.watchPosition((position) => {
      if (token !== this.standortToken) return;
      this.standort = {
        lat: position.coords.latitude, lng: position.coords.longitude, genauigkeit: position.coords.accuracy,
      };
      if (!hatKoordinaten(this.standort)) { onError?.(); return; }
      this.zeigeStandort();
      onPosition?.(this.standort);
    }, () => {
      if (token !== this.standortToken) return;
      this.stoppeStandort();
      onError?.();
    }, { enableHighAccuracy: true, maximumAge: 30000 });
  }

  stoppeStandort() {
    this.standortToken = (this.standortToken || 0) + 1;
    if (this.watchId != null) navigator.geolocation?.clearWatch(this.watchId);
    this.watchId = null;
    this.standort = null;
    this.eigeneMarker?.remove(); this.genauigkeit?.remove();
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
