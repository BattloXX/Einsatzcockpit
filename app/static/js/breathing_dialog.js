(function (root) {
  'use strict';
  function validateTroopForm(values) {
    const errors = [];
    const filled = (values.members || []).map((m, i) => ({m, i}))
      .filter(({m}) => m.id || String(m.name || '').trim());
    if (filled.length < 2) errors.push({field: 'member_0_name', message: 'Bitte mindestens 2 Truppmitglieder ausfüllen.'});
    filled.forEach(({m, i}) => {
      if (m.press === null || m.press === undefined || String(m.press).trim() === '') {
        errors.push({field: `member_${i}_press`, message: `Bitte Anfangsdruck für Mitglied ${i + 1} wählen.`});
      } else if (!Number.isFinite(Number(m.press)) || Number(m.press) < 0 || Number(m.press) > 330) {
        errors.push({field: `member_${i}_press`, message: `Anfangsdruck für Mitglied ${i + 1} muss zwischen 0 und 330 bar liegen.`});
      }
    });
    if (values.bottlePreset === 'manuell' &&
        (!String(values.planned_duration_min ?? '').trim() || !Number.isInteger(Number(values.planned_duration_min)) ||
         Number(values.planned_duration_min) < 1 || Number(values.planned_duration_min) > 120)) {
      errors.push({field: 'planned_duration_min', message: 'Bitte eine manuelle Einsatzzeit von 1 bis 120 Minuten eingeben.'});
    }
    return errors;
  }
  function newTroopForm(options = {}) {
    let nextKey = 0;
    const member = (m = {}) => ({id:m.id || '', name:m.name || '', role:m.role || 'truppmann',
      press:null, pickerOpen:false, free:!m.id && !!m.name, key:nextKey++});
    const initial = () => (options.initialMembers?.length ? options.initialMembers :
      [{role:'truppfuehrer'}, {}, {}]).map(member);
    return {
      members:initial(), bottlePreset:options.bottlePreset || '', occupied:options.occupied || {},
      pressValues:Array.from({length:34}, (_, i) => 330 - i * 10), errors:[], unitEdited:false,
      addMember() { this.members.push(member()); },
      removeMember(i) { this.members.splice(i, 1); },
      errorFor(field) { return this.errors.find(e => e.field === field)?.message || ''; },
      suggestUnit(event) {
        if (!this.unitEdited) event.target.form.elements.unit_name.value = event.target.value ? event.target.selectedOptions[0].textContent : '';
      },
      validate() {
        this.errors = validateTroopForm({members:this.members, bottlePreset:this.bottlePreset,
          planned_duration_min:this.$el.elements.planned_duration_min.value});
        if (this.errors.length) {
          this.$nextTick(() => {
            const field = this.errors[0].field;
            let target = this.$el.querySelector(`[data-field="${field}"]`) || this.$el.elements[field];
            if (!target || target.disabled || target.offsetParent === null) {
              target = this.$el.querySelector('.bt-dialog-person .person-select__trigger') || this.$el.querySelector('.bt-dialog-errors');
            }
            target.focus();
          });
          return false;
        }
        if (!this.$el.elements.name.value.trim()) this.$el.elements.name.value = options.nameSuggestion;
        return true;
      },
      afterRequest(event) {
        if (event.detail.elt !== this.$el) return;
        const xhr = event.detail.xhr;
        if (!event.detail.successful || /[?&]error=/.test(xhr.responseURL || '')) {
          this.errors = [{field:'server', message: 'Speichern fehlgeschlagen. Bitte Eingaben prüfen und erneut versuchen.'}];
          this.$nextTick(() => this.$el.querySelector('.bt-dialog-errors').focus());
          return;
        }
        this.$el.closest('dialog').close();
        this.$el.reset();
        this.members = initial(); this.bottlePreset = options.bottlePreset || ''; this.errors = []; this.unitEdited = false;
      }
    };
  }
  const api = {validateTroopForm, newTroopForm};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) { root.ECPBreathingDialog = api; root.newTroopForm = newTroopForm; }
})(typeof window !== 'undefined' ? window : null);
