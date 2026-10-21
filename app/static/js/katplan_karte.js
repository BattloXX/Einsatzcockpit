/* Verortung eines Planelements; Instanzen leben nur bis zum Detail-Swap. */
(function () {
  'use strict';
  let timer;

  let iconGesetzt = false;

  function initialisieren() {
    if (!window.L) return;
    if (!iconGesetzt) {
      // Leaflets Default-Icon zeigt relativ zur CSS auf images/marker-icon.png; das Projekt
      // liefert die Bilder unter /static/img/leaflet/ aus (Muster lagefuehrung.js/map-picker.js).
      delete L.Icon.Default.prototype._getIconUrl;
      L.Icon.Default.mergeOptions({
        iconUrl: '/static/img/leaflet/marker-icon.png',
        iconRetinaUrl: '/static/img/leaflet/marker-icon-2x.png',
        shadowUrl: '/static/img/leaflet/marker-shadow.png'
      });
      iconGesetzt = true;
    }
    plankartenInitialisieren();
    document.querySelectorAll('[data-kp-geo]').forEach(function (element) {
      if (element.kpGeo) return;
      const container = element.querySelector('.kp-geo-karte');
      const input = element.querySelector('[name="geometry_json"]');
      const geprueft = element.querySelector('[name="geo_geprueft"]');
      const badge = element.querySelector('[data-kp-geo-status]');
      const punkt = element.dataset.kpGeo === 'punkt';
      const map = L.map(container).setView([Number(element.dataset.lat), Number(element.dataset.lng)], 13);
      window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
      const controller = new AbortController();
      element.kpGeo = { map: map, controller: controller };
      let layer = null;

      function status() {
        const farbe = !input.value ? 'gray' : (geprueft.checked ? 'green' : 'amber');
        badge.className = 'kp-badge kp-badge--' + farbe;
        badge.textContent = !input.value ? 'nicht verortet' : (geprueft.checked ? 'Ort geprüft' : 'Ort ungeprüft');
      }
      function speichern() {
        input.value = layer ? JSON.stringify(layer.toGeoJSON().geometry) : '';
        geprueft.checked = false;
        status();
      }
      function einsetzen(neu, geaendert) {
        if (layer) map.removeLayer(layer);
        layer = neu.addTo(map);
        // Geoman feuert Bearbeitungsereignisse auf der jeweiligen Geometrie.
        layer.on('pm:edit pm:update pm:dragend moveend', speichern);
        if (geaendert) speichern();
        if (layer.getBounds) map.fitBounds(layer.getBounds(), { padding: [20, 20], maxZoom: 16 });
        else map.setView(layer.getLatLng(), 16);
      }
      try {
        if (input.value) {
          const gruppe = L.geoJSON(JSON.parse(input.value), {
            pointToLayer: function (feature, latlng) { return L.marker(latlng); }
          });
          if (gruppe.getLayers().length) einsetzen(gruppe.getLayers()[0], false);
        }
      } catch (fehler) {
        // Ungueltige Eingaben bleiben fuer die serverseitige Fehlermeldung erhalten.
      }
      map.pm.addControls({
        position: 'topleft', drawMarker: true, drawCircleMarker: false,
        drawCircle: false, drawText: false, drawPolyline: !punkt,
        drawRectangle: false, drawPolygon: !punkt, editMode: true,
        dragMode: true, cutPolygon: false, removalMode: true, rotateMode: false
      });
      map.on('pm:create', function (event) { einsetzen(event.layer, true); });
      map.on('pm:remove', function (event) {
        if (event.layer === layer) { layer = null; speichern(); }
      });
      geprueft.addEventListener('change', status);
      status();

      const button = element.querySelector('[data-kp-adress-suche]');
      const eingabe = element.querySelector('[data-kp-adress-eingabe]');
      const treffer = element.querySelector('[data-kp-geo-treffer]');
      const form = element.closest('form');
      const felder = ['strasse', 'hausnummer', 'plz', 'ort'];
      let manuell = false;

      // Suchtext aus den Adressfeldern; ohne Ort wird der Ort der Organisation ergaenzt.
      function vorschlag() {
        const wert = function (name) { return form.elements[name] ? form.elements[name].value.trim() : ''; };
        const zeile1 = [wert('strasse'), wert('hausnummer')].filter(Boolean).join(' ');
        const zeile2 = [wert('plz'), wert('ort') || eingabe.dataset.orgOrt || ''].filter(Boolean).join(' ');
        return [zeile1, zeile2].filter(Boolean).join(', ');
      }
      eingabe.addEventListener('input', function () { manuell = true; });
      felder.forEach(function (name) {
        if (!form.elements[name]) return;
        form.elements[name].addEventListener('input', function () {
          if (!manuell) eingabe.value = vorschlag();
        });
      });

      async function suchen() {
        const q = eingabe.value.trim() || vorschlag();
        treffer.replaceChildren();
        if (q.length < 3) {
          treffer.textContent = 'Bitte mindestens 3 Zeichen eingeben.';
          return;
        }
        button.disabled = true;
        try {
          const antwort = await fetch('/plaene/adress-suche?q=' + encodeURIComponent(q), {
            signal: controller.signal
          });
          if (!antwort.ok) throw new Error('Adresssuche fehlgeschlagen.');
          const daten = await antwort.json();
          if (!daten.treffer.length) treffer.textContent = 'Keine Adresse gefunden.';
          daten.treffer.forEach(function (ort) {
            const auswahl = document.createElement('button');
            auswahl.type = 'button';
            auswahl.className = 'btn btn--ghost btn--sm';
            auswahl.textContent = ort.display_name;
            auswahl.addEventListener('click', function () {
              if (punkt) einsetzen(L.marker([ort.lat, ort.lng]), true);
              else map.setView([ort.lat, ort.lng], 16);
              // Gewaehlte Adresse in die Felder uebernehmen; leere Teile der Auswahl lassen Eingaben stehen.
              felder.forEach(function (name) {
                if (ort[name] && form.elements[name]) form.elements[name].value = ort[name];
              });
              eingabe.value = ort.display_name;
              geprueft.checked = false;
              status();
              treffer.replaceChildren();
            });
            treffer.appendChild(auswahl);
          });
        } catch (fehler) {
          if (fehler.name !== 'AbortError') treffer.textContent = 'Adresssuche fehlgeschlagen.';
        } finally {
          button.disabled = false;
        }
      }
      button.addEventListener('click', suchen);
      eingabe.addEventListener('keydown', function (event) {
        // Enter sucht, statt das Elementformular abzusenden.
        if (event.key === 'Enter') {
          event.preventDefault();
          suchen();
        }
      });
      map.invalidateSize();
    });
  }

  function plankartenInitialisieren() {
    if (!window.L) return;
    document.querySelectorAll('[data-kp-plankarte]').forEach(async function (element) {
      if (element.kpPlankarte) return;
      const mini = element.dataset.mini === 'true';
      const container = element.querySelector('.kp-plankarte');
      const map = L.map(container, {
        dragging: !mini, scrollWheelZoom: !mini, doubleClickZoom: !mini,
        touchZoom: !mini, boxZoom: !mini, keyboard: !mini, zoomControl: !mini
      }).setView([Number(element.dataset.lat), Number(element.dataset.lng)], 13);
      window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
      const controller = new AbortController();
      element.kpPlankarte = { map: map, controller: controller };
      const status = element.querySelector('[data-kp-karten-status]');
      const bereich = element.closest('[data-kp-kartenbereich]');
      const eintraege = [];

      function popup(daten) {
        const box = document.createElement('div');
        const titel = document.createElement('strong');
        titel.textContent = [daten.kennung, daten.titel].filter(Boolean).join(' · ');
        box.appendChild(titel);
        const typ = document.createElement('p');
        typ.innerHTML = ecpIcon(daten.typ_icon_name, {size: 'sm'});
        typ.appendChild(document.createTextNode(' ' + daten.typ_label));
        box.appendChild(typ);
        if (daten.geo_status !== 'geprueft') {
          const badge = document.createElement('p');
          badge.className = 'kp-badge kp-badge--amber';
          badge.textContent = 'Ort ungeprüft';
          box.appendChild(badge);
        }
        const link = document.createElement('a');
        link.href = daten.url;
        link.textContent = 'Details';
        box.appendChild(link);
        return box;
      }
      function filtern() {
        if (!bereich) return;
        const typen = Array.from(bereich.querySelectorAll('[data-kp-filter-typ]:checked')).map(function (input) {
          return input.value;
        });
        const phase = bereich.querySelector('[data-kp-filter-phase]').value;
        const prio = bereich.querySelector('[data-kp-filter-prio]').value;
        const geprueft = bereich.querySelector('[data-kp-filter-geprueft]').checked;
        let anzahl = 0;
        eintraege.forEach(function (eintrag) {
          const daten = eintrag.feature.properties;
          const sichtbar = typen.includes(daten.typ) && (!phase || daten.phasen.map(String).includes(phase)) &&
            (!prio || String(daten.prioritaet) === prio) && (!geprueft || daten.geo_status === 'geprueft');
          if (sichtbar) { eintrag.layer.addTo(map); anzahl += 1; }
          else map.removeLayer(eintrag.layer);
        });
        status.textContent = !eintraege.length ? 'Keine verorteten Elemente' :
          (anzahl ? '' : 'Keine Elemente für diese Filter');
      }
      function hervorheben() {
        const uuid = window.location.hash.slice('#element-'.length);
        if (!window.location.hash.startsWith('#element-')) return;
        const eintrag = eintraege.find(function (item) { return item.feature.properties.uuid === uuid; });
        if (!eintrag || mini) return;
        // Ein Direktlink bleibt auch nach zuvor gesetzten Filtern sichtbar.
        eintrag.layer.addTo(map);
        map.fitBounds(eintrag.layer.getBounds(), { padding: [30, 30], maxZoom: 16 });
        eintrag.layer.eachLayer(function (layer) {
          if (layer.setStyle) layer.setStyle({ weight: 6 });
          if (layer.setRadius) layer.setRadius(12);
          if (layer.bringToFront) layer.bringToFront();
          layer.openPopup();
        });
      }
      try {
        const antwort = await fetch(element.dataset.url, { signal: controller.signal });
        if (!antwort.ok) throw new Error('Karte konnte nicht geladen werden.');
        const daten = await antwort.json();
        daten.features.forEach(function (feature) {
          const farbe = { 1: '#dc2626', 2: '#2563eb', 3: '#64748b' }[feature.properties.prioritaet] || '#64748b';
          const geprueft = feature.properties.geo_status === 'geprueft';
          const stil = { color: farbe, fillColor: farbe, weight: 3, fillOpacity: geprueft ? 0.45 : 0.2,
            opacity: geprueft ? 1 : 0.5, dashArray: geprueft ? null : '5 5' };
          const layer = L.geoJSON(feature, {
            style: function () { return stil; },
            pointToLayer: function (f, latlng) { return L.circleMarker(latlng, Object.assign({ radius: 8 }, stil)); },
            onEachFeature: function (f, objekt) { if (!mini) objekt.bindPopup(popup(f.properties)); }
          }).addTo(map);
          eintraege.push({ feature: feature, layer: layer });
        });
        status.textContent = eintraege.length ? '' : 'Keine verorteten Elemente';
        if (eintraege.length) {
          const alle = L.featureGroup(eintraege.map(function (eintrag) { return eintrag.layer; }));
          map.fitBounds(alle.getBounds(), { padding: [20, 20], maxZoom: 16 });
        }
        if (bereich) bereich.addEventListener('change', filtern, { signal: controller.signal });
        window.addEventListener('hashchange', hervorheben, { signal: controller.signal });
        hervorheben();
        map.invalidateSize();
      } catch (fehler) {
        if (fehler.name !== 'AbortError') status.textContent = 'Karte konnte nicht geladen werden.';
      }
    });
  }

  document.addEventListener('htmx:beforeSwap', function (event) {
    const ziel = event.detail.target;
    if (!event.detail.shouldSwap || !ziel) return;
    ziel.querySelectorAll('[data-kp-plankarte]').forEach(function (element) {
      if (!element.kpPlankarte) return;
      element.kpPlankarte.controller.abort();
      element.kpPlankarte.map.remove();
      delete element.kpPlankarte;
    });
    ziel.querySelectorAll('[data-kp-geo]').forEach(function (element) {
      if (!element.kpGeo) return;
      element.kpGeo.controller.abort();
      element.kpGeo.map.remove();
      delete element.kpGeo;
    });
  });
  document.addEventListener('htmx:afterSettle', function () {
    clearTimeout(timer);
    timer = setTimeout(initialisieren, 100);
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialisieren);
  else initialisieren();
})();
