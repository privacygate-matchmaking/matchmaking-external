'use strict';
/* 회원 알림 (헤더 종 아이콘). 매칭·만남 일정·정보 공개·심사 결과 알림을 internal-api 가 만들고,
 * 이 화면은 /api/me/notifications 로 본인 알림만 읽는다. 문구에는 상대 이름·연락처가 없다. */
window.NotifyUI = (() => {
  const ICON = {match_requested:'💌',match_accepted:'🤝',match_rejected:'🙏',match_cancelled:'↩️',match_ended:'🔚',
    meeting_proposed:'📅',meeting_accepted:'✅',meeting_change_requested:'🔁',meeting_confirmed:'📌',meeting_cancelled:'⛔',
    release_approved:'📨',release_revoked:'🔒',evidence_passed:'✔️',evidence_rejected:'⚠️'};
  const state = {items: [], timer: null, ctx: null};
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined && text !== null) n.textContent = String(text); return n; };
  const $ = id => document.getElementById(id);
  function ago(iso) {
    const sec = Math.max(0, (Date.now() - new Date(iso)) / 1000);
    if (sec < 60) return '방금';
    if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}시간 전`;
    return new Date(iso).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'});
  }
  function badge(n) {
    const b = $('notify-badge');
    b.textContent = n > 99 ? '99+' : String(n);
    b.hidden = n <= 0;
    $('notify-bell').setAttribute('aria-label', n > 0 ? `알림 ${n}개 읽지 않음` : '알림');
  }
  async function load() {
    if (!state.ctx) return;
    try {
      const data = await (await state.ctx.api('/api/me/notifications')).json();
      state.items = Array.isArray(data.items) ? data.items : [];
      badge(data.unread || 0);
      render();
    } catch { /* 알림을 못 불러와도 다른 기능은 그대로 */ }
  }
  async function post(path, body) {
    try { await state.ctx.api(path, {method: 'POST', body: JSON.stringify(body)}); } catch (error) { state.ctx.toast(error.message); }
    await load();
  }
  function render() {
    const list = $('notify-list');
    $('notify-empty').hidden = state.items.length > 0;
    const unread = state.items.filter(x => !x.read).length;
    $('notify-summary').textContent = state.items.length ? `읽지 않음 ${unread} · 전체 ${state.items.length}` : '';
    list.replaceChildren(...state.items.map(x => {
      const li = el('li', 'notify-item' + (x.read ? '' : ' unread'));
      const main = el('button', 'notify-main'); main.type = 'button';
      const kind = String(x.kind || '').replace(/^member_/, '');
      main.append(el('span', 'notify-icon', ICON[kind] || '🔔'));
      const text = el('span', 'notify-text');
      text.append(el('strong', '', x.title), el('span', 'notify-body', x.body || ''), el('span', 'notify-time', ago(x.created_at)));
      main.append(text);
      main.onclick = async () => {
        if (!x.read) await post('/api/me/notifications/read', {ids: [x.notification_id]});
        const tab = x.link_view && document.getElementById('tab-' + x.link_view);
        if (tab && !tab.hidden) { toggle(false); tab.click(); }
      };
      const ops = el('span', 'notify-ops');
      if (!x.read) { const r = el('button', 'text-button', '읽음'); r.type = 'button'; r.onclick = () => post('/api/me/notifications/read', {ids: [x.notification_id]}); ops.append(r); }
      const d = el('button', 'text-button', '삭제'); d.type = 'button'; d.onclick = () => post('/api/me/notifications/delete', {ids: [x.notification_id]}); ops.append(d);
      li.append(main, ops);
      return li;
    }));
  }
  function toggle(open) {
    const panel = $('notify-panel');
    const show = open ?? panel.hidden;
    panel.hidden = !show;
    $('notify-bell').setAttribute('aria-expanded', String(show));
    if (show) load();
  }
  function start(ctx) {
    state.ctx = ctx;
    $('notify').hidden = false;
    $('notify-bell').onclick = e => { e.stopPropagation(); toggle(); };
    $('notify-panel').onclick = e => e.stopPropagation();
    document.addEventListener('click', () => { if (!$('notify-panel').hidden) toggle(false); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('notify-panel').hidden) toggle(false); });
    $('notify-read-all').onclick = () => post('/api/me/notifications/read', {all: true});
    $('notify-delete-all').onclick = () => { if (state.items.length && confirm(`알림 ${state.items.length}개를 모두 삭제할까요?`)) post('/api/me/notifications/delete', {all: true}); };
    load();
    clearInterval(state.timer);
    state.timer = setInterval(() => { if (!document.hidden) load(); }, 30000);
  }
  function stop() { clearInterval(state.timer); state.ctx = null; const n = $('notify'); if (n) n.hidden = true; }
  return {start, stop, load, ICON};
})();
