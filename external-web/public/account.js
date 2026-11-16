'use strict';
window.AccountUI = (() => {
  let logoutUrl = '/', loggedOut = false;
  const el = (tag, cls, text) => {
    const item = document.createElement(tag);
    if (cls) item.className = cls;
    if (text !== undefined) item.textContent = text;
    return item;
  };
  function showServices(show) {
    document.querySelector('#dashboard .overview').hidden = !show;
    document.querySelector('#dashboard .content-layout').hidden = !show;
  }
  async function check(ctx) {
    const status = await (await ctx.api('/api/me/status')).json();
    if (!ctx.isCurrent()) return false;
    const panel = document.querySelector('#member-status-panel');
    panel.replaceChildren();
    panel.hidden = status.status === 'active';
    showServices(status.status === 'active');
    if (status.status === 'active') return true;
    panel.append(el('h2', '', '회원 상태 안내'), el('p', '', status.message || '회원 상태를 확인해주세요.'));
    if (status.can_cancel === true && status.by === 'self' && status.status === 'withdraw_pending') {
      const cancel = el('button', 'primary', '탈퇴 신청 철회');
      cancel.type = 'button';
      cancel.onclick = async () => {
        if (!confirm('본인이 신청한 탈퇴를 철회할까요?')) return;
        cancel.disabled = true;
        try {
          await ctx.api('/api/me/withdrawal/cancel', {method:'POST', body:'{}'});
          ctx.toast('탈퇴 신청을 철회했습니다.');
          await ctx.reload();
        } catch (error) { ctx.toast(error.message); }
        finally { if (cancel.isConnected) cancel.disabled = false; }
      };
      panel.append(cancel);
    }
    if (status.status === 'withdrawn') {
      // Keep the notice visible after deleting the local session. Do not loop login.
      if (!loggedOut) {
        try {
          const result = await (await ctx.api('/auth/logout', {method:'POST', body:'{}'})).json();
          logoutUrl = result.logout_url;
          loggedOut = true;
          document.querySelector('#account').hidden = true;
        } catch (error) { panel.append(el('p', 'error', '로그아웃을 완료하지 못했습니다. '+error.message)); }
      }
      const link = el('a', 'primary', '로그인 화면으로 돌아가기');
      link.href = logoutUrl;
      panel.append(link);
    } else {
      const refresh = el('button', 'text-button', '회원 상태 다시 확인');
      refresh.type = 'button'; refresh.onclick = ctx.reload;
      panel.append(refresh);
    }
    return false;
  }
  function section(title, text) {
    const box = el('section', 'form-panel account-section');
    box.append(el('h2', '', title));
    if (text) box.append(el('p', 'muted', text));
    return box;
  }
  function field(label, id, type, value, attrs = {}) {
    const l = el('label', '', label); l.htmlFor = id;
    const input = el('input'); input.id = id; input.type = type;
    if (value !== null && value !== undefined) input.value = value;
    Object.assign(input, attrs);
    return [l, input];
  }
  const EVIDENCE_KO = {identity:'신분증 (본인 확인)', education:'졸업증명서 (학력)', employment:'재직증명서', marital:'혼인관계증명서',
    income:'소득 증빙', property:'부동산 증빙', photo:'프로필 사진'};
  // 심사 상태 → [표시, 색]. 심사 결과가 있으면 결과가 우선 (원본이 보관 기간 후 삭제돼도 결과는 유지)
  function evidenceState(item) {
    if (item.review_result === 'passed') return ['승인', 'ok'];
    if (item.review_result === 'rejected') return ['반려', 'bad'];
    if (item.status === 'in_review') return ['심사중', 'wait'];
    if (item.status === 'submitted') return ['미승인 · 심사 대기', 'wait'];
    return ['미승인', 'none'];
  }
  async function evidenceHistory(api) {
    const box = el('section', 'form-panel account-section evidence-history');
    box.append(el('h2', '', '증빙 자료 내역'),
      el('p', 'muted', '내가 제출한 증빙과 심사 상태입니다. 같은 종류를 다시 제출하면 최신 제출이 위에 표시됩니다. 원본은 심사 후 보관 기간이 지나면 삭제되고 결과만 남습니다.'));
    let items = [];
    try { items = (await (await api('/api/me/evidence-files')).json()).items || []; }
    catch (error) { box.append(el('div', 'error-box', '증빙 내역을 불러오지 못했습니다. ' + error.message)); return box; }
    if (!items.length) { box.append(el('p', 'muted', '아직 제출한 증빙이 없습니다. "증빙 관리" 탭에서 서류를, 아래 "프로필 사진"에서 사진을 제출할 수 있습니다.')); return box; }
    items = [...items].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    const seen = new Set();
    const table = el('table', 'evidence-table');
    const head = el('tr'); for (const h of ['증빙 종류', '제출일', '상태']) head.append(el('th', '', h));
    const thead = el('thead'); thead.append(head);
    const tbody = el('tbody');
    for (const item of items) {
      const [label, tone] = evidenceState(item);
      const tr = el('tr');
      const kind = el('td', '', EVIDENCE_KO[item.kind] || '증빙');
      if (seen.has(item.kind)) { tr.className = 'older'; kind.append(el('span', 'muted', ' · 이전 제출')); }
      seen.add(item.kind);
      const state = el('td'); state.append(el('span', 'state-pill ' + tone, label));
      tr.append(kind, el('td', '', new Date(item.created_at).toLocaleDateString('ko-KR', {timeZone: 'Asia/Seoul'})), state);
      tbody.append(tr);
    }
    table.append(thead, tbody);
    const counts = items.reduce((n, item) => { const [label] = evidenceState(item); n[label] = (n[label] || 0) + 1; return n; }, {});
    box.append(el('p', 'evidence-summary', Object.entries(counts).map(([k, v]) => `${k} ${v}건`).join(' · ')), table);
    return box;
  }
  const photoStatus = {submitted:'접수 · 검토 대기', in_review:'직원 검토 중', passed:'승인됨', rejected:'반려 · 다시 등록해 주세요'};
  async function render(ctx) {
    const {root, api, toast, reload} = ctx;
    let profile = null;
    try { profile = await (await api('/api/me/profile')).json(); }
    catch (error) { root.append(el('div', 'error-box', '내 정보를 불러오지 못했습니다. ' + error.message)); }
    if (ctx.isCurrent && !ctx.isCurrent()) return;

    // 0) 로그인한 계정 (본인 이름·아이디)
    if (profile && profile.name) {
      const who = el('section', 'form-panel account-section account-who');
      who.append(el('span', 'eyebrow', 'MY ACCOUNT'), el('h2', '', profile.name + ' 님'));
      if (profile.username) who.append(el('p', 'muted', '로그인 아이디 ' + profile.username));
      root.append(who);
    }
    // 0-1) 증빙 자료 내역: 내가 제출한 서류·사진과 현재 심사 상태 (제출한 것만)
    root.append(await evidenceHistory(api));
    if (ctx.isCurrent && !ctx.isCurrent()) return;
    // 1) 비밀번호: 이 서버는 비밀번호를 받지 않고 Keycloak 변경 화면으로 보낸다
    const pw = section('로그인 비밀번호 변경', '비밀번호는 이음 로그인 서비스(Keycloak)에서만 변경합니다. 변경 후 이 화면으로 돌아옵니다.');
    const pwLink = el('a', 'secondary', '비밀번호 변경하기 ↗'); pwLink.href = '/auth/login?action=password';
    pw.append(pwLink); root.append(pw);

    if (profile) {
      // 2) 프로필 사진: 직원 검토(위치 등 메타데이터 제거) 후 승인된 사진만 매칭 상대에게 승인 자료로 제공
      const ph = section('프로필 사진 등록·변경', 'JPEG·PNG 1장, 최대 2MB. 촬영 위치 등 메타데이터는 자동으로 지워지고, 직원 검토 후 승인된 사진만 매칭 상대에게 (공개 동의 + 승인 절차를 거쳐) 제공됩니다.');
      const latest = profile.photo?.latest;
      ph.append(el('p', '', '현재 상태: ' + (latest ? (photoStatus[latest.status] || latest.status) : '등록된 사진 없음')
        + (profile.photo?.approved_at ? ' · 승인된 사진 있음' : '')));
      // 미리보기: 승인된 사진 / 검토 중인 사진 (둘 다 위치 등 메타데이터를 지운 사본, 본인만 볼 수 있음)
      const previews = el('div', 'photo-previews');
      const thumb = (which, label) => {
        const fig = el('figure', 'photo-thumb'); const img = el('img');
        img.alt = label; img.src = `/api/me/profile/photo?which=${which}&t=${Date.now()}`;
        img.onerror = () => { fig.replaceChildren(el('div', 'photo-empty', which === 'latest' ? '검사 중 · 잠시 후 표시' : '사진 없음'), el('figcaption', '', label)); };
        fig.append(img, el('figcaption', '', label)); return fig;
      };
      if (profile.photo?.approved_at) previews.append(thumb('approved', '승인된 사진 (상대에게 제공되는 사진)'));
      if (latest && ['submitted', 'in_review'].includes(latest.status)) previews.append(thumb('latest', '검토 중인 새 사진'));
      if (previews.children.length) ph.append(previews);
      const [pl, pf] = field('사진 파일', 'profile-photo', 'file', null, {accept: 'image/jpeg,image/png', required: true});
      // 올리기 전 선택한 파일 미리보기 (브라우저 안에서만, 서버로 보내지 않음)
      const picked = el('figure', 'photo-thumb'); picked.hidden = true;
      pf.onchange = () => {
        const file = pf.files[0];
        if (!file || !['image/jpeg', 'image/png'].includes(file.type) || file.size > 2097152) { picked.hidden = true; return; }
        const reader = new FileReader();
        reader.onload = () => { const img = el('img'); img.alt = '선택한 사진'; img.src = reader.result; picked.replaceChildren(img, el('figcaption', '', '선택한 사진 (등록 전 미리보기)')); picked.hidden = false; };
        reader.readAsDataURL(file);
      };
      const up = el('button', 'primary', latest ? '사진 변경' : '사진 등록'); up.type = 'submit';
      const pform = el('form'); pform.append(pl, pf, picked, up);
      pform.onsubmit = async e => {
        e.preventDefault(); const file = pf.files[0];
        if (!file || file.size > 2097152 || !['image/jpeg','image/png'].includes(file.type)) { toast('2MB 이하의 JPEG·PNG 사진을 선택해 주세요.'); return; }
        up.disabled = true;
        try { const data = new FormData(); data.append('file', file); data.append('kind', 'photo');
          await api('/api/me/evidence-files', {method:'POST', body:data}); toast('사진을 등록했습니다. 직원 검토 후 승인됩니다.'); await reload(); }
        catch (error) { toast(error.message); } finally { if (up.isConnected) up.disabled = false; }
      };
      ph.append(pform); root.append(ph);

      // 3) 키·몸무게 (본인 신고)
      const body = section('키·몸무게', '본인이 입력하는 정보이며 소개 정보에 "본인 신고"로 표시됩니다.');
      const [hl, h] = field('키 (cm)', 'profile-height', 'number', profile.height_cm, {min: 120, max: 230, step: 1});
      const [wl, w] = field('몸무게 (kg)', 'profile-weight', 'number', profile.weight_kg, {min: 30, max: 200, step: 1});
      const save = el('button', 'primary', '저장'); save.type = 'submit';
      const bform = el('form', 'grid-two'); const hb = el('div'); hb.append(hl, h); const wb = el('div'); wb.append(wl, w); bform.append(hb, wb, save);
      bform.onsubmit = async e => {
        e.preventDefault(); const patch = {};
        if (h.value) patch.height_cm = Number(h.value); if (w.value) patch.weight_kg = Number(w.value);
        save.disabled = true;
        try { await api('/api/me/profile', {method:'PATCH', body:JSON.stringify(patch)}); toast('키·몸무게를 저장했습니다.'); await reload(); }
        catch (error) { toast(error.message); } finally { if (save.isConnected) save.disabled = false; }
      };
      body.append(bform); root.append(body);

      // 4) 연락처: 여기서는 등록·변경만. 상대에게 주는 것은 매칭의 연락처 공개 동의 + 일정 확정 + 직원 요청 + 승인자 승인뿐
      const ct = section('연락처 변경', '연락처는 화면에 일부만 표시됩니다. 매칭 상대에게는 연락처 공개 동의, 만남 일정 확정, 상대의 동의, 담당 직원 요청과 승인 담당자 최종 승인을 모두 거친 경우에만 전달됩니다. 변경하면 이전에 승인된 연락처 자료는 더 이상 받을 수 없습니다.');
      ct.append(el('p', '', `현재: 전화 ${profile.contact_phone || '없음'} · 이메일 ${profile.contact_email || '없음'}`));
      const [cpl, cp] = field('새 휴대전화 (선택)', 'profile-phone', 'tel', '', {placeholder: '010-1234-5678', maxLength: 20, autocomplete: 'tel'});
      const [cel, ce] = field('새 이메일 (선택)', 'profile-email', 'email', '', {placeholder: 'name@example.com', maxLength: 200, autocomplete: 'email'});
      const csave = el('button', 'primary', '연락처 변경'); csave.type = 'submit';
      const cform = el('form'); cform.append(cpl, cp, cel, ce, csave);
      cform.onsubmit = async e => {
        e.preventDefault(); const patch = {};
        if (cp.value.trim()) patch.contact_phone = cp.value.trim(); if (ce.value.trim()) patch.contact_email = ce.value.trim();
        if (!Object.keys(patch).length) { toast('변경할 연락처를 입력해 주세요.'); return; }
        if (!confirm('연락처를 변경할까요? 이전에 승인된 연락처 자료는 무효가 됩니다.')) return;
        csave.disabled = true;
        try { await api('/api/me/profile', {method:'PATCH', body:JSON.stringify(patch)}); toast('연락처를 변경했습니다.'); await reload(); }
        catch (error) { toast(error.message); } finally { if (csave.isConnected) csave.disabled = false; }
      };
      ct.append(cform); root.append(ct);
    }

    // 5) 회원 탈퇴 (회원 상태 연동이 켜진 경우)
    if (!ctx.withdrawal) return;
    const form = el('form', 'form-panel account-section');
    form.append(el('h2', '', '회원 탈퇴'), el('p', 'muted',
      '탈퇴 신청 후 7일의 유예 기간이 적용됩니다. 유예 중에는 회원 서비스를 이용할 수 없고, 본인 신청 건은 철회할 수 있습니다.'));
    const label = el('label', '', '탈퇴 사유 (선택)'); label.htmlFor = 'withdrawal-reason';
    const reason = el('textarea'); reason.id = 'withdrawal-reason'; reason.maxLength = 200;
    const submit = el('button', 'secondary', '회원 탈퇴 신청'); submit.type = 'submit';
    form.append(label, reason, el('p', 'muted', '사유는 최대 200자이며 담당 직원에게 전달됩니다.'), submit);
    form.onsubmit = async event => {
      event.preventDefault();
      if (!confirm('탈퇴를 신청할까요? 7일 후 회원 정보가 삭제되며 유예 중 회원 기능이 제한됩니다.')) return;
      submit.disabled = true;
      try {
        await api('/api/me/withdrawal', {method:'POST', body:JSON.stringify({reason:reason.value.trim() || null})});
        toast('탈퇴 신청을 접수했습니다.'); await reload();
      } catch (error) { toast(error.message); }
      finally { if (submit.isConnected) submit.disabled = false; }
    };
    root.append(form);
  }
  return {check, render};
})();
