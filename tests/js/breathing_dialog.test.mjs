import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { validateTroopForm, newTroopForm } = createRequire(import.meta.url)('../../app/static/js/breathing_dialog.js');
const valid = () => ({members:[{id:'1', press:300}, {name:'Person B', press:290}], bottlePreset:''});
test('mindestens zwei Mitglieder, Feldzuordnung', () => {
  assert.deepEqual(validateTroopForm({members:[{name:'  '}]}).map(e => e.field), ['member_0_name']);
});
test('fehlender Anfangsdruck nur bei ausgefüllten Mitgliedern', () => {
  const values = valid(); values.members[1].press = null; values.members.push({press:null});
  assert.deepEqual(validateTroopForm(values).map(e => e.field), ['member_1_press']);
});
for (const press of [-1, 331, NaN, Infinity, 'ungueltig']) {
  test(`Druck außerhalb 0-330: ${press}`, () => {
    const values = valid(); values.members[0].press = press;
    assert.equal(validateTroopForm(values)[0].field, 'member_0_press');
  });
}
for (const duration of [undefined, '', 0, -1, 121, 1.5]) {
  test(`manuelle Einsatzzeit ungültig: ${duration}`, () => {
    const values = {...valid(), bottlePreset:'manuell', planned_duration_min:duration};
    assert.equal(validateTroopForm(values)[0].field, 'planned_duration_min');
  });
}
test('gültige Daten und Grenzwerte', () => {
  assert.deepEqual(validateTroopForm(valid()), []);
  for (const duration of [1, 120]) {
    assert.deepEqual(validateTroopForm({...valid(), bottlePreset:'manuell', planned_duration_min:duration}), []);
  }
  assert.deepEqual(validateTroopForm({members:[{name:'A', press:0}, {name:'B', press:330}]}), []);
});
test('Validierung verändert Daten nicht', () => {
  const values = valid(); const before = structuredClone(values); validateTroopForm(values);
  assert.deepEqual(values, before);
});
test('drei Standardzeilen, erneuter Einsatz ohne Druck und stabile Schlüssel', () => {
  const form = newTroopForm();
  assert.equal(form.members.length, 3); assert.equal(form.members[0].role, 'truppfuehrer');
  const existing = form.members[2].key; form.removeMember(1); form.addMember();
  assert.equal(form.members[1].key, existing); assert.notEqual(form.members[2].key, existing);
  const redeploy = newTroopForm({initialMembers:[{id:'42', role:'truppfuehrer', press:300}]});
  assert.equal(redeploy.members[0].id, '42'); assert.equal(redeploy.members[0].press, null);
});

test('Fahrzeug schlägt Funkrufname nur bis zur manuellen Änderung vor', () => {
  const form = newTroopForm();
  const input = {value:''};
  const event = {target:{form:{elements:{unit_name:input}}, value:'42', selectedOptions:[{textContent:'Florian 42'}]}};
  form.suggestUnit(event); assert.equal(input.value, 'Florian 42');
  form.unitEdited = true; input.value = 'Eigener Rufname';
  form.suggestUnit(event); assert.equal(input.value, 'Eigener Rufname');
});
