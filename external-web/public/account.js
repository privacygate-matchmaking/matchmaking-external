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
  function render(ctx) {
    const {root, api, toast, reload} = ctx;
    const form = el('form', 'form-panel');
    form.append(el('h2', '', '내 계정 관리'), el('p', 'muted',
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
