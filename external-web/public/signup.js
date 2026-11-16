'use strict';
/* 이음 회원가입 폼. 로그인 전 화면이라 세션·CSRF 토큰이 없다 (external-api 가 Origin 과 요청 횟수를 확인).
 * 비밀번호는 요청 본문으로만 보내고 화면·저장소·로그에 남기지 않는다. 최종 검사는 internal-api 가 한다. */
window.SignupUI = (() => {
  const CONSENT_VERSION = '2026-11';
  const EDUCATION = ['고졸','전문대졸','4년제 대졸','대학원졸'];
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const kstToday = () => new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
  const age = (birth, today) => {
    const [by, bm, bd] = birth.split('-').map(Number), [ty, tm, td] = today.split('-').map(Number);
    return ty - by - ((tm < bm || (tm === bm && td < bd)) ? 1 : 0);
  };

  /** 입력값 → {body} 또는 {error, field}. 서버와 같은 규칙 (서버가 최종 판단). */
  function validate(v, today = kstToday()) {
    const fail = (field, error) => ({field, error});
    const username = (v.username || '').trim();
    if (!/^[a-z][a-z0-9_]{3,19}$/.test(username)) return fail('username', '아이디는 영문 소문자로 시작하는 4~20자(영문 소문자·숫자·_)로 입력하세요.');
    const password = v.password || '';
    if (password.length < 12 || password.length > 64) return fail('password', '비밀번호는 12~64자로 입력하세요.');
    if (password !== password.trim()) return fail('password', '비밀번호 앞뒤에 공백을 넣을 수 없습니다.');
    if (password.toLowerCase().includes(username)) return fail('password', '비밀번호에 아이디를 넣을 수 없습니다.');
    if (password !== v.password_confirm) return fail('password_confirm', '비밀번호 확인이 일치하지 않습니다.');
    const name = (v.name || '').trim();
    if (!name || name.length > 30) return fail('name', '이름을 입력하세요 (30자 이내).');
    if (!['남','여'].includes(v.gender)) return fail('gender', '성별을 선택하세요.');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(v.birth_date || '') || v.birth_date > today || v.birth_date < '1900-01-01') return fail('birth_date', '생년월일을 확인하세요.');
    if (age(v.birth_date, today) < 19) return fail('birth_date', '만 19세 이상만 가입할 수 있습니다.');
    const phone = (v.contact_phone || '').trim();
    if (!/^01[016789]-?\d{3,4}-?\d{4}$/.test(phone)) return fail('contact_phone', '휴대전화 번호 형식(010-1234-5678)을 확인하세요.');
    const email = (v.contact_email || '').trim();
    if (!/^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,120}\.[A-Za-z]{2,24}$/.test(email)) return fail('contact_email', '이메일 형식을 확인하세요.');
    if (!EDUCATION.includes(v.education_level)) return fail('education_level', '학력 수준을 선택하세요.');
    const job = (v.job_group || '').trim(), intro = (v.introduction || '').trim();
    if (!job || job.length > 40) return fail('job_group', '직군을 입력하세요 (40자 이내).');
    const number = (key, min, max, label) => {
      const raw = String(v[key] || '').trim();
      if (!raw) return {error: `${label}를 입력하세요.`};
      const n = Number(raw);
      if (!Number.isInteger(n) || n < min || n > max) return {error: `${label}는 ${min}~${max} 사이 정수로 입력하세요.`};
      return {value: n};
    };
    const height = number('height_cm', 120, 230, '키'), weight = number('weight_kg', 30, 200, '몸무게');
    if (height.error) return fail('height_cm', height.error);
    if (weight.error) return fail('weight_kg', weight.error);
    if (!intro || intro.length > 300) return fail('introduction', '소개를 입력하세요 (300자 이내).');
    if (v.privacy_consent !== true) return fail('privacy_consent', '개인정보 수집·이용에 동의해야 가입할 수 있습니다.');
    return {body: {username, password, name, gender: v.gender, birth_date: v.birth_date, contact_phone: phone,
      contact_email: email, education_level: v.education_level, job_group: job,
      height_cm: height.value, weight_kg: weight.value, introduction: intro,
      privacy_consent: true, consent_version: CONSENT_VERSION}};
  }

  function field(form, key, label, input, hint) {
    const id = 'signup-' + key.replace(/_/g, '-');
    input.id = id; input.name = key;
    const box = el('div', 'signup-field');
    const l = el('label', '', label); l.htmlFor = id;
    box.append(l, input);
    if (hint) box.append(el('p', 'muted', hint));
    form.append(box);
    return input;
  }
  function text(type, attrs = {}) {
    const input = el('input'); input.type = type;
    for (const [k, value] of Object.entries(attrs)) input.setAttribute(k, value);
    return input;
  }
  function select(options, placeholder) {
    const s = el('select');
    const first = el('option', '', placeholder); first.value = ''; s.append(first);
    for (const value of options) { const o = el('option', '', value); o.value = value; s.append(o); }
    return s;
  }

  function render(root, {fetch: send = (...a) => fetch(...a), onBack = () => {}, today = kstToday} = {}) {
    root.replaceChildren();
    const form = el('form', 'signup-form'); form.noValidate = true;
    form.append(el('span', 'eyebrow', 'JOIN IEUM'), el('h2', '', '이음 회원가입'),
      el('p', 'muted', '가입하면 담당 상담 직원이 자동으로 배정됩니다. 표시(*)는 필수 항목입니다. 시험 서비스이므로 실제 개인정보를 입력하지 마세요.'));
    const inputs = {};
    form.append(el('h3', '', '계정'));
    inputs.username = field(form, 'username', '아이디 *', text('text', {autocomplete: 'username', maxlength: '20', autocapitalize: 'none', spellcheck: 'false'}), '영문 소문자로 시작, 4~20자 (영문 소문자·숫자·_)');
    inputs.password = field(form, 'password', '비밀번호 *', text('password', {autocomplete: 'new-password', maxlength: '64'}), '12자 이상, 아이디를 포함할 수 없음');
    inputs.password_confirm = field(form, 'password_confirm', '비밀번호 확인 *', text('password', {autocomplete: 'new-password', maxlength: '64'}));
    form.append(el('h3', '', '기본 정보'));
    inputs.name = field(form, 'name', '이름 *', text('text', {autocomplete: 'name', maxlength: '30'}));
    inputs.gender = field(form, 'gender', '성별 *', select(['남','여'], '선택하세요'));
    inputs.birth_date = field(form, 'birth_date', '생년월일 *', text('date', {max: today()}), '만 19세 이상 가입 가능');
    inputs.contact_phone = field(form, 'contact_phone', '휴대폰 *', text('tel', {autocomplete: 'tel', maxlength: '13', placeholder: '010-1234-5678'}));
    inputs.contact_email = field(form, 'contact_email', '이메일 *', text('email', {autocomplete: 'email', maxlength: '200'}));
    form.append(el('h3', '', '프로필'));
    inputs.education_level = field(form, 'education_level', '학력 수준 *', select(EDUCATION, '선택하세요'), '학력은 졸업증명서 심사 후 확인 완료로 표시됩니다.');
    inputs.job_group = field(form, 'job_group', '직군 *', text('text', {maxlength: '40', placeholder: '예: IT·개발'}));
    inputs.height_cm = field(form, 'height_cm', '키 (cm) *', text('number', {min: '120', max: '230', step: '1', inputmode: 'numeric'}));
    inputs.weight_kg = field(form, 'weight_kg', '몸무게 (kg) *', text('number', {min: '30', max: '200', step: '1', inputmode: 'numeric'}));
    const intro = el('textarea'); intro.maxLength = 300; intro.rows = 3;
    inputs.introduction = field(form, 'introduction', '소개 *', intro, '300자 이내');

    const consentBox = el('section', 'signup-consent');
    consentBox.append(el('h3', '', '개인정보 수집·이용 동의 (필수)'));
    const terms = el('ul', 'muted');
    for (const line of ['수집 항목: 아이디, 비밀번호(인증 서버에만 저장), 이름, 성별, 생년월일, 휴대폰, 이메일, 프로필(학력 수준·직군·키·몸무게·소개)',
      '이용 목적: 회원 관리, 담당 상담 직원 배정, 소개(매칭) 서비스 제공',
      '보관 기간: 탈퇴 확정 시까지 (탈퇴 신청 후 7일 유예 뒤 삭제)',
      '이름·연락처 등은 본인 동의와 승인 관리자 승인 없이 다른 회원에게 공개되지 않습니다.',
      '동의하지 않을 수 있으나, 동의하지 않으면 가입할 수 없습니다.']) terms.append(el('li', '', line));
    const agree = el('label', 'signup-check');
    const check = text('checkbox'); check.id = 'signup-privacy-consent';
    agree.append(check, el('span', '', ' 위 내용을 확인했으며 개인정보 수집·이용에 동의합니다.'));
    consentBox.append(terms, agree);
    form.append(consentBox);
    inputs.privacy_consent = check;

    const error = el('p', 'error'); error.setAttribute('role', 'alert');
    const submit = el('button', 'primary wide', '가입하기'); submit.type = 'submit';
    const back = el('button', 'text-button', '← 로그인 화면으로'); back.type = 'button'; back.onclick = onBack;
    form.append(error, submit, back);
    root.append(form);

    const clearSecrets = () => { inputs.password.value = ''; inputs.password_confirm.value = ''; };
    form.onsubmit = async event => {
      event.preventDefault();
      error.textContent = '';
      const values = {};
      for (const [key, input] of Object.entries(inputs)) values[key] = key === 'privacy_consent' ? input.checked === true : input.value;
      const result = validate(values, today());
      if (result.error) {
        error.textContent = result.error;
        if (inputs[result.field] && inputs[result.field].focus) inputs[result.field].focus();
        return;
      }
      submit.disabled = true;
      try {
        const response = await send('/api/signup', {method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(result.body)});
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          error.textContent = data.error?.message || (response.status === 422 ? '입력 내용을 다시 확인해주세요.' : '가입을 완료하지 못했습니다. 잠시 후 다시 시도해주세요.');
          return;
        }
        clearSecrets();
        done(root, result.body.username, onBack);
      } catch {
        error.textContent = '가입을 완료하지 못했습니다. 잠시 후 다시 시도해주세요.';
      } finally {
        if (submit.isConnected) submit.disabled = false;
      }
    };
    return {form, inputs, error, submit};
  }

  function done(root, username, onBack) {
    const box = el('section', 'signup-done');
    box.append(el('span', 'eyebrow', 'WELCOME'), el('h2', '', '가입이 완료되었습니다'),
      el('p', '', `아이디 ${username} 로 로그인할 수 있습니다. 담당 상담 직원이 배정되었습니다.`),
      el('p', 'muted', '로그인한 뒤 "증빙 관리"에서 신분증과 졸업증명서를 제출하면 소개(매칭)를 시작할 수 있습니다.'));
    const login = el('a', 'primary wide', '로그인하기'); login.href = '/auth/login';
    const back = el('button', 'text-button', '← 처음 화면으로'); back.type = 'button'; back.onclick = onBack;
    box.append(login, back);
    root.replaceChildren(box);
  }

  function bind(doc = document) {
    const open = doc.querySelector('#signup-open'), view = doc.querySelector('#signup-view'), login = doc.querySelector('#login-view');
    if (!open || !view || !login) return;
    const back = () => { view.hidden = true; view.replaceChildren(); login.hidden = false; };
    open.onclick = () => { login.hidden = true; view.hidden = false; render(view, {onBack: back}); };
  }
  if (typeof document !== 'undefined' && document.addEventListener) document.addEventListener('DOMContentLoaded', () => bind());
  return {validate, render, bind, CONSENT_VERSION};
})();
