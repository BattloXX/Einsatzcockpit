window.personSelectFlyout = function(options) {
  const collator = new Intl.Collator('de', { sensitivity: 'base' });
  const normalize = (value) => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleUpperCase('de-DE');
  const sorted = (options.people || []).slice().sort((a, b) => collator.compare(a.lastname || '', b.lastname || '') || collator.compare(a.firstname || '', b.firstname || ''));
  const groups = {};
  sorted.forEach((person) => {
    const letter = normalize(person.lastname || person.name).charAt(0) || '#';
    (groups[letter] ||= []).push(person);
  });
  return {
    title: options.title, people: sorted, groups, query: '', open: false,
    memberId: options.initialId || '', selectedName: options.initialName || '', invalid: false, freitext: false,
    uid: 'person-select-' + Math.random().toString(36).slice(2),
    letters: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split(''),
    get availableLetters() { return Object.keys(this.groups).sort(collator.compare); },
    get filtered() {
      const needle = normalize(this.query).replace(/\s+/g, ' ').trim();
      if (!needle) return this.people;
      return this.people.filter((person) => normalize([person.firstname, person.lastname, person.name].join(' ')).includes(needle));
    },
    element: null,
    init() {
      this.element = this.$el;
      this.element.addEventListener('person-select-restore', () => {
        const id = this.element.querySelector('input[name$="_member_id"]')?.value || '';
        const name = this.element.querySelector('input[name$="_name"]')?.value || '';
        this.memberId = id; this.selectedName = name; this.invalid = !!name && !id; this.freitext = false;
      });
    },
    openFlyout() { this.open = true; this.query = ''; this.invalid = false; this.$nextTick(() => this.$refs.search?.focus()); },
    close() { this.open = false; },
    jump(letter) { document.getElementById(this.uid + '-' + letter)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); },
    clearSelection(asFreitext = false) {
      this.select({ id: '', name: '' });
      this.freitext = asFreitext;
      if (asFreitext) {
        this.element.dispatchEvent(new CustomEvent('person-select-freitext', { bubbles: true }));
      }
    },
    select(person) {
      this.memberId = person.id; this.selectedName = person.name; this.invalid = false; this.freitext = false; this.close();
      this.element.dispatchEvent(new CustomEvent('person-select-change', { bubbles: true, detail: person }));
      this.$nextTick(() => {
        // Hidden inputs loesen selbst kein Form-Event aus: explizit signalisieren,
        // damit die bestehende Entwurfsicherung die ID sofort mitspeichert.
        this.element.querySelector('input[name$="_member_id"]')?.dispatchEvent(new Event('change', { bubbles: true }));
        this.element.querySelector('.person-select__trigger')?.focus();
      });
    },
  };
};
