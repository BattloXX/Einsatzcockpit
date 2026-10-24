/* UI-Reihenfolge und Begriffe; erlaubte_status aus dem Backend bleibt verbindlich. */
const FOLGE = {
  abarbeitung: ["zugewiesen", "bestaetigt", "anfahrt", "vor_ort", "in_arbeit", "abgeschlossen"],
  erkundung: ["zugewiesen", "bestaetigt", "anfahrt", "vor_ort", "abgeschlossen"],
};
const AKTIONEN = {
  bestaetigt: "Auftrag übernehmen", anfahrt: "Anfahrt melden", vor_ort: "Vor Ort melden",
  in_arbeit: "In Arbeit setzen", abgeschlossen: "Fertig melden",
};

export function auftragsfuehrung(auftrag, { schreibbar = true, ausstehend = null, konflikt = false } = {}) {
  const warten = (text, hinweis) => ({ art: "warten", status: null, text, hinweis });
  if (!auftrag) return warten("Auftrag wird geladen", "");
  if (auftrag.withdrawn_at) return warten("Auftrag zurückgezogen", "Für diesen Auftrag sind keine Statusaktionen möglich.");
  if (auftrag.beendet_at || ["abgeschlossen", "nicht_durchfuehrbar"].includes(auftrag.einheit_status)) {
    const text = auftrag.einheit_status === "nicht_durchfuehrbar" ? "Nicht durchführbar"
      : auftrag.auftragsart === "erkundung" ? "Erkundet" : "Fertig gemeldet";
    return warten(text, "Der eigene Auftrag ist beendet. Die Einsatzleitung entscheidet über die Einsatzstelle.");
  }
  if (!schreibbar) return warten("Nur lesen", "Für diesen Zugang sind keine Statusaktionen möglich.");
  if (ausstehend) return warten("Übermittlung ausstehend", "Der vorgemerkte Schritt ist noch nicht vom Server bestätigt.");
  const pruefen = { art: "pruefen", status: null, text: "Auftrag prüfen", hinweis: "Aktuellen Stand laden und prüfen." };
  if (konflikt) return pruefen;
  const folge = typeof auftrag.auftragsart === "string" && Object.prototype.hasOwnProperty.call(FOLGE, auftrag.auftragsart)
    ? FOLGE[auftrag.auftragsart] : [];
  const index = folge.indexOf(auftrag.einheit_status);
  if (index < 0 || !Array.isArray(auftrag.erlaubte_status)) return pruefen;
  const status = folge.slice(index + 1).find((ziel) => auftrag.erlaubte_status.includes(ziel));
  if (!status) {
    const phaseGesperrt = auftrag.auftragsart === "abarbeitung" && ["eingegangen", "erkundung"].includes(auftrag.phase);
    return warten(phaseGesperrt ? "Freigabe ausstehend" : "Kein weiterer Schritt freigegeben", phaseGesperrt
      ? "Abarbeitung erst nach Bewertung durch die Einsatzleitung möglich."
      : "Weitere Statusaktionen stehen nur entsprechend der Freigabe des Servers zur Verfügung.");
  }
  const ergebnis = auftrag.auftragsart === "erkundung" && status === "abgeschlossen";
  return {
    art: ergebnis ? "ergebnis" : "status", status,
    text: ergebnis ? "Erkundungsergebnis erfassen" : AKTIONEN[status],
    hinweis: status === "bestaetigt" ? "Übernehmen bestätigt den Empfang und beginnt keine Anfahrt." : "",
  };
}
