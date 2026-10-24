/* Reine Anzeige des vorhandenen Feldes auftragsart; keine Ableitung aus Text, Phase oder Status. */
const ARTEN = Object.freeze({
  erkundung: Object.freeze({ label: "ERKUNDUNG", icon: "search", klasse: "einheit-art--erkundung" }),
  abarbeitung: Object.freeze({ label: "AUFTRAG", icon: "tools", klasse: "einheit-art--abarbeitung" }),
});
const UNBEKANNT = Object.freeze({ label: "ART UNBEKANNT", icon: "help-circle", klasse: "einheit-art--unbekannt" });

export function auftragsartAnzeige(wert) {
  return typeof wert === "string" && Object.prototype.hasOwnProperty.call(ARTEN, wert) ? ARTEN[wert] : UNBEKANNT;
}

const PRIORITAETEN = Object.freeze({
  sofort: { label: "Sofort", klasse: "einheit-prio--sofort", chipKlasse: "einheit-chip--red" },
  dringend: { label: "Dringend", klasse: "einheit-prio--dringend", chipKlasse: "einheit-chip--orange" },
  normal: { label: "Normal", klasse: "einheit-prio--normal", chipKlasse: "einheit-chip--yellow" },
  aufschiebbar: { label: "Aufschiebbar", klasse: "einheit-prio--aufschiebbar", chipKlasse: "einheit-chip--muted" },
});
const OHNE_PRIORITAET = { label: "ohne Priorität", klasse: "einheit-prio--keine", chipKlasse: "einheit-chip--muted" };

export function prioritaetAnzeige(wert) {
  return typeof wert === "string" && Object.prototype.hasOwnProperty.call(PRIORITAETEN, wert)
    ? PRIORITAETEN[wert] : OHNE_PRIORITAET;
}
