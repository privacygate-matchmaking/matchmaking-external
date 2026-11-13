'use strict';
window.MeetingsUI = (() => {
  const names = {proposed:'일정 조율 중',confirmed:'일정 확정',superseded:'이전 제안',cancelled:'취소됨'};
  const replies = {available:'가능해요',change_requested:'다른 일정 요청'};
  const el = (tag,cls,text) => {
    const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;
  };
  const date = value => new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});
  async function render(match,panel,ctx) {
    panel.hidden=false;
    const loading=el('p','muted','담당 직원이 제안한 일정을 확인하고 있습니다…');
    panel.replaceChildren(loading);
    try {
      const data=await(await ctx.api(`/api/me/matches/${match.match_id}/meetings`)).json();
      if(!panel.isConnected||!panel.contains(loading))return;
      panel.replaceChildren(el('h4','','만남 일정 조율'),el('p','matching-note',
        '양쪽이 같은 후보에 동의한 뒤 담당 직원이 최종 확정합니다. 의견은 본인과 담당 직원만 확인하며, 일정 변경 요청에 추가 횟수 차감은 없습니다.'));
      if(!data.items.length){panel.append(el('p','muted','담당 직원의 일정 제안을 기다리고 있습니다.'));return;}
      for(const item of data.items) {
        const card=el('section','meeting-card');
        const top=el('div','card-top');top.append(el('strong','',date(item.starts_at)+' (한국 시간)'),
          el('span','badge'+(item.status==='confirmed'?'':' inactive'),names[item.status]||item.status));
        card.append(top,el('p','',item.venue),el('p','muted',item.address));
        if(item.status==='confirmed')card.append(el('p','meeting-confirmed','직원이 최종 확정한 일정입니다.'));
        else if(item.both_available&&item.status==='proposed')card.append(el('p','muted','양쪽 응답 완료 · 직원 최종 확인을 기다리고 있습니다.'));
        if(item.my_reply)card.append(el('p','muted','내 응답: '+replies[item.my_reply.availability]));
        if(item.can_reply) {
          const form=el('form','meeting-reply');
          const choice=el('select');choice.setAttribute('aria-label','일정 가능 여부');
          for(const [value,label] of Object.entries(replies)) {
            if(item.status==='confirmed'&&value==='available')continue;
            const option=el('option','',label);option.value=value;choice.append(option);
          }
          if(item.status!=='confirmed'&&item.my_reply)choice.value=item.my_reply.availability;
          const note=el('textarea');note.maxLength=300;note.rows=2;
          note.setAttribute('aria-label','담당 직원에게 남길 의견');
          note.placeholder='가능한 시간 등 담당 직원에게만 전달할 의견 (선택, 300자 이내)';
          note.value=item.my_reply?.private_note||'';
          const submit=el('button','secondary',item.status==='confirmed'?'일정 변경 요청':'응답 보내기');submit.type='submit';
          form.append(choice,note,submit);
          form.onsubmit=async event=>{
            event.preventDefault();
            if(item.status==='confirmed'&&!confirm('일정 확정을 해제하고 담당 직원에게 변경을 요청할까요?'))return;
            submit.disabled=true;
            try {
              await ctx.api(`/api/me/matches/${match.match_id}/meetings/${item.proposal_id}/reply`,{
                method:'POST',body:JSON.stringify({availability:choice.value,private_note:note.value})});
              ctx.toast('응답을 담당 직원에게 전달했습니다.');
              if(panel.isConnected)await render(match,panel,ctx);
            } catch(error) {ctx.toast(error.message);}
            finally {if(submit.isConnected)submit.disabled=false;}
          };
          card.append(form);
        }
        panel.append(card);
      }
    } catch(error) {
      if(panel.isConnected&&panel.contains(loading))panel.replaceChildren(el('p','error',error.message));
    }
  }
  return {render};
})();
