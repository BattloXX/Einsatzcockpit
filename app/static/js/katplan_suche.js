/* Adressauswahl und Standort fuer die kombinierte Plansuche. */
(function () {
  const form = document.getElementById('kp-suchformular');
  if (!form) return;
  const adresse = document.getElementById('kp-suche-adresse');
  const auswahl = document.getElementById('kp-suche-adressen');
  const hinweis = document.getElementById('kp-suche-geo-hinweis');
  let timer;
  let lauf = 0;

  function punkt(lat, lng) {
    form.elements.lat.value = lat;
    form.elements.lng.value = lng;
    if (!form.elements.radius_m.value) form.elements.radius_m.value = '1000';
    hinweis.textContent = 'Umkreis gesetzt.';
    auswahl.replaceChildren();
    form.elements.lat.dispatchEvent(new Event('change', { bubbles: true }));
  }

  adresse.addEventListener('input', function () {
    clearTimeout(timer);
    const aktuell = ++lauf;
    const q = adresse.value.trim();
    auswahl.replaceChildren();
    if (q.length < 3) return;
    timer = setTimeout(async function () {
      try {
        const response = await fetch('/plaene/adress-suche?q=' + encodeURIComponent(q));
        if (!response.ok) throw new Error('Adresssuche fehlgeschlagen');
        const data = await response.json();
        if (aktuell !== lauf) return;
        hinweis.textContent = data.treffer.length ? '' : 'Keine Adresse gefunden.';
        data.treffer.forEach(function (treffer) {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'btn btn--ghost btn--sm';
          button.textContent = treffer.display_name;
          button.addEventListener('click', function () {
            adresse.value = treffer.display_name;
            punkt(treffer.lat, treffer.lng);
          });
          auswahl.append(button);
        });
      } catch (_error) {
        if (aktuell === lauf) hinweis.textContent = 'Adresssuche derzeit nicht verfügbar.';
      }
    }, 400);
  });

  document.getElementById('kp-suche-standort').addEventListener('click', function () {
    if (!navigator.geolocation) {
      hinweis.textContent = 'Standortbestimmung wird nicht unterstützt.';
      return;
    }
    hinweis.textContent = 'Standort wird ermittelt ...';
    navigator.geolocation.getCurrentPosition(function (position) {
      punkt(position.coords.latitude, position.coords.longitude);
    }, function () {
      hinweis.textContent = 'Standort konnte nicht ermittelt werden. Bitte eine Adresse auswählen.';
    }, { timeout: 10000 });
  });

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    htmx.trigger(form, 'change');
  });

  form.addEventListener('change', function (event) {
    if (event.target.name === 'plan_id' || event.target.name === 'arbeitsstand') {
      form.elements.phase.value = '';
    }
    if (event.target.name === 'radius_m' && !event.target.value) {
      form.elements.lat.value = '';
      form.elements.lng.value = '';
    }
  }, true);
})();
