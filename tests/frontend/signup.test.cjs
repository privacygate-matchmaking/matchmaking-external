const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.value = ''; this.checked = false; this.isConnected = true; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(k, v) { this.attrs[k] = v; }
  focus() { this.focused = true; }
}
function load() {
  const context = {window: {}, document: {createElement: tag => new Element(tag)}, JSON, Date, Number, String, Object};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../external-web/public/signup.js'), 'utf8'), context);
  return context.window.SignupUI;
}
const TODAY = '2026-11-16';
const good = () => ({username: 'newmember1', password: 'Synthetic-Pass-2026!', password_confirm: 'Synthetic-Pass-2026!',
  name: ' 가상 회원 ', gender: '여', birth_date: '1995-04-01', contact_phone: '010-1234-5678',
  contact_email: 'n@example.test', education_level: '4년제 대졸', job_group: ' IT ', height_cm: '170', weight_kg: '60', introduction: '합성 소개', privacy_consent: true});

test('valid form builds the signup body with consent version', () => {
  const ui = load();
  const {body} = ui.validate(good(), TODAY);
  assert.equal(body.name, '가상 회원');
  assert.equal(body.height_cm, 170);
  assert.equal(body.weight_kg, 60);
  assert.equal(body.job_group, 'IT');
  assert.equal(body.education_level, '4년제 대졸');
  assert.equal(body.consent_version, '2026-11');
  assert.equal('password_confirm' in body, false);
});

test('validation rules match the server', () => {
  const ui = load();
  const cases = [
    [{username: 'Ab'}, 'username'], [{password: 'short'}, 'password'], [{password: 'xxnewmember1xx', password_confirm: 'xxnewmember1xx'}, 'password'],
    [{password_confirm: 'different-pass-123'}, 'password_confirm'], [{gender: ''}, 'gender'],
    [{birth_date: '2008-11-17'}, 'birth_date'], [{contact_phone: '02-123-4567'}, 'contact_phone'],
    [{contact_email: 'bad'}, 'contact_email'], [{height_cm: '300'}, 'height_cm'], [{privacy_consent: false}, 'privacy_consent'],
    [{education_level: ''}, 'education_level'], [{job_group: ''}, 'job_group'], [{weight_kg: ''}, 'weight_kg'], [{introduction: ' '}, 'introduction']];
  for (const [change, field] of cases) assert.equal(ui.validate({...good(), ...change}, TODAY).field, field, JSON.stringify(change));
  assert.ok(ui.validate({...good(), birth_date: '2007-11-16'}, TODAY).body, 'exactly 19 is allowed');
});

function fill(view, values) {
  for (const [k, v] of Object.entries(values)) {
    if (!view.inputs[k]) continue;
    if (k === 'privacy_consent') view.inputs[k].checked = v; else view.inputs[k].value = v;
  }
}

test('submit posts JSON once, clears the password and shows completion', async () => {
  const ui = load();
  const root = new Element('section');
  const calls = [];
  const view = ui.render(root, {today: () => TODAY, fetch: async (url, options) => { calls.push({url, options}); return {ok: true, json: async () => ({status: 'registered'})}; }});
  fill(view, good());
  await view.form.onsubmit({preventDefault() {}});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/signup');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal('X-CSRF-Token' in calls[0].options.headers, false);
  assert.equal(JSON.parse(calls[0].options.body).username, 'newmember1');
  assert.equal(view.inputs.password.value, '');
  assert.equal(root.children[0].className, 'signup-done');
});

test('client validation blocks the request and server message is shown', async () => {
  const ui = load();
  const root = new Element('section');
  let sent = 0;
  const view = ui.render(root, {today: () => TODAY, fetch: async () => { sent++; return {ok: false, status: 409, json: async () => ({error: {message: '이미 사용 중인 아이디 또는 이메일입니다.'}})}; }});
  fill(view, {...good(), privacy_consent: false});
  await view.form.onsubmit({preventDefault() {}});
  assert.equal(sent, 0);
  assert.match(view.error.textContent, /동의/);
  fill(view, good());
  await view.form.onsubmit({preventDefault() {}});
  assert.equal(sent, 1);
  assert.equal(view.error.textContent, '이미 사용 중인 아이디 또는 이메일입니다.');
  assert.equal(root.children[0], view.form);
});
