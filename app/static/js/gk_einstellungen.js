(() => {
  "use strict";

  const card = document.getElementById("gk-zugang-einstellungen");
  if (!card) return;

  const input = document.getElementById("gk-zugang-nachricht");
  const counter = document.getElementById("gk-sms-zaehler");
  const warning = document.getElementById("gk-sms-warnung");
  const preview = document.getElementById("gk-nachricht-vorschau");
  const restore = document.getElementById("gk-standard-wiederherstellen");
  const standard = JSON.parse(card.dataset.standardNachricht || '""');
  const baseUrl = card.dataset.baseUrl || "";
  const link = `${baseUrl}/gk#${"x".repeat(36)}`;
  const gsm = new Set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ !\"#¤%&'()*+,-./0123456789:;<=>?¡" +
      "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüßà",
  );
  const extended = new Set("€[]{}\\^~|");

  function smsLength(text) {
    const gsm7 = [...text].every((character) => gsm.has(character) || extended.has(character));
    const characters = gsm7
      ? [...text].reduce((sum, character) => sum + (extended.has(character) ? 2 : 1), 0)
      : [...text].length;
    const segments = gsm7 ? (characters <= 160 ? 1 : Math.ceil(characters / 153)) : (characters <= 70 ? 1 : Math.ceil(characters / 67));
    return { characters, encoding: gsm7 ? "GSM-7" : "UCS-2", segments };
  }

  function update() {
    const template = input.value || standard;
    const text = template
      .replaceAll("{lage}", "Hochwasser Rheintal")
      .replaceAll("{einheit}", "RLF Wolfurt")
      .replaceAll("{gruppenkommandant}", "Max Muster")
      .replaceAll("{link}", link);
    const result = smsLength(text);
    counter.textContent = `${result.characters} Zeichen - ${result.segments} SMS - ${result.encoding}`;
    preview.textContent = text;
    const notices = [];
    if (result.encoding === "UCS-2") notices.push("Sonderzeichen erhöhen die SMS-Anzahl.");
    if (result.segments > 3) notices.push("Die Nachricht benötigt mehr als drei SMS.");
    warning.textContent = notices.join(" ");
    warning.style.display = notices.length ? "block" : "none";
  }

  input.addEventListener("input", update);
  restore.addEventListener("click", () => {
    input.value = standard;
    update();
  });
  update();

  const orderInput = document.getElementById("gk-auftrag-nachricht");
  const orderCounter = document.getElementById("gk-auftrag-sms-zaehler");
  const orderPreview = document.getElementById("gk-auftrag-vorschau");
  const orderRestore = document.getElementById("gk-auftrag-standard-wiederherstellen");
  const orderStandard = JSON.parse(card.dataset.auftragStandardNachricht || '""');
  if (orderInput && orderCounter && orderPreview && orderRestore) {
    function updateOrder() {
      const template = orderInput.value || orderStandard;
      const text = template.replaceAll("{ereignis}", "Neuer Einsatzauftrag")
        .replaceAll("{gsl}", "Hochwasser Rheintal").replaceAll("{einheit}", "RLF Wolfurt")
        .replaceAll("{einsatzstelle}", "Rathausplatz 1, Wolfurt")
        .replaceAll("{auftrag}", "Wasser abpumpen").replaceAll("{link}", link);
      const result = smsLength(text);
      orderCounter.textContent = `${result.characters} Zeichen - ${result.segments} SMS - ${result.encoding}`;
      orderPreview.textContent = text;
    }
    orderInput.addEventListener("input", updateOrder);
    orderRestore.addEventListener("click", () => { orderInput.value = orderStandard; updateOrder(); });
    updateOrder();
  }
})();
