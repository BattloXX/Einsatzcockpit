/* Gemeinsame Bedienung der Standardansicht; keine eigenen Auftrags- oder Statusregeln. */
export function gefahrenHinweise(detail) {
  const hinweise = (detail?.gefahren || []).map((g, index) => ({
    key: `lage:${g.id ?? index}`, titel: g.title, text: g.type_label || "", quelle: "Umgebung",
  }));
  for (const objekt of detail?.objekte || []) {
    (objekt.gefahren || []).forEach((g, index) => hinweise.push({
      key: `objekt:${objekt.id}:${index}`, titel: g.name + (g.un_nummer ? ` · UN ${g.un_nummer}` : ""),
      text: g.detail || "", quelle: objekt.bezeichnung,
    }));
  }
  return hinweise;
}

export function mobileBedienung() {
  return {
    dialogFokus: null, dialogScroll: null, dialogAnsicht: null, dialogFokusToken: 0, dialogRueckkehr: false,
    letzterBedienFokus: null, letzterBedienScroll: null,
    ansichtSchluessel() { return `${this.aktiverBereich}:${this.detail?.auftrag?.dispatch_id || ''}`; },
    bedienungInitialisieren() {
      const merken = event => {
        if (this.flyout) return;
        const ziel = event.target.closest('button,a,summary,input,select,textarea');
        if (!ziel?.closest('.einheit-inhalt')) return;
        this.letzterBedienFokus = ziel;
        this.letzterBedienScroll = { top: window.scrollY, left: window.scrollX };
      };
      // Vor Alpine-Klickaktionen merken: inert entfernt anschließend den Fokus aus dem Hintergrund.
      this.$root.addEventListener("click", merken, true);
      this.$root.addEventListener("focusin", merken);
      this.$watch("flyout", (art, vorher) => this.dialogWechsel(art, vorher));
      this.$watch("statusSpeichern || ergebnisSpeichern || meldungSpeichern || fotoSpeichern", busy => {
        if (!busy && !this.flyout && this.dialogRueckkehr) {
          const token = this.dialogFokusToken;
          this.$nextTick(() => this.dialogFokusWiederherstellen(token));
        }
      });
    },
    dialogFokusWiederherstellen(token) {
      if (token !== this.dialogFokusToken || this.flyout || !this.dialogRueckkehr) return;
      if (this.statusSpeichern || this.ergebnisSpeichern || this.meldungSpeichern || this.fotoSpeichern) return;
      this.dialogRueckkehr = false;
      if (this.dialogAnsicht !== this.ansichtSchluessel()) return;
      const ziel = this.dialogFokus?.isConnected && !this.dialogFokus.disabled
        ? this.dialogFokus : this.$refs.auftragsfuehrung;
      ziel?.focus({ preventScroll: true });
      if (this.dialogScroll) window.scrollTo({ ...this.dialogScroll, behavior: "instant" });
    },
    dialogWechsel(art, vorher) {
      const token = ++this.dialogFokusToken;
      if (art && !vorher) {
        this.dialogFokus = this.letzterBedienFokus || document.activeElement;
        this.dialogScroll = this.letzterBedienScroll || { top: window.scrollY, left: window.scrollX };
        this.dialogAnsicht = this.ansichtSchluessel();
      }
      this.dialogRueckkehr = !art;
      this.$nextTick(() => {
        if (token !== this.dialogFokusToken) return;
        if (art) {
          // Orientierung zuerst; kein automatischer Fokus in ein Textfeld und damit keine unerwartete Tastatur.
          this.$refs.dialogTitel?.focus({ preventScroll: true });
        } else this.dialogFokusWiederherstellen(token);
      });
    },
    dialogTab(event) {
      const dialog = this.$refs.dialog;
      const elemente = [...dialog.querySelectorAll(
        'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]'
      )].filter(el => el.getClientRects().length && !el.closest('[inert]') && el.type !== "hidden");
      if (!elemente.length) { event.preventDefault(); this.$refs.dialogTitel?.focus(); return; }
      const erster = elemente[0], letzter = elemente.at(-1);
      if (event.shiftKey && (document.activeElement === erster || document.activeElement === this.$refs.dialogTitel)) {
        event.preventDefault(); letzter.focus();
      } else if (!event.shiftKey && document.activeElement === letzter) {
        event.preventDefault(); erster.focus();
      }
    },
  };
}
