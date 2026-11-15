'use strict';
/* Members arrange the meeting between themselves: either member proposes up to three
 * options, the other picks one, and the assigned counselor only approves the agreed option. */
window.MeetingsUI = (() => {
  const names = {proposed:'일정 조율 중',confirmed:'일정 확정',superseded:'이전 제안',cancelled:'취소·반려됨'};
  const replies = {available:'가능해요',change_requested:'다른 일정 요청'};
  const el = (tag,cls,text) => {
    const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;
  };
  const date = value => new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});

  function replyForm(match,item,panel,ctx) {
    const form=el('form','meeting-reply');
    const choice=el('select');choice.setAttribute('aria-label','일정 가능 여부');
    for(const [value,label] of Object.entries(replies)) {
      if(item.status==='confirmed'&&value==='available')continue;
      const option=el('option','',label);option.value=value;choice.append(option);
    }
    if(item.status!=='confirmed'&&item.my_reply)choice.value=item.my_reply.availability;
    const note=el('textarea');note.maxLength=300;note.rows=2;
    note.setAttribute('aria-label','담당 직원에게 남길 의견');
    note.placeholder='담당 직원에게만 전달할 의견 (선택, 300자 이내)';
    note.value=item.my_reply?.private_note||'';
    const submit=el('button','secondary',item.status==='confirmed'?'일정 변경 요청':'응답 보내기');submit.type='submit';
    form.append(choice,note,submit);
    form.onsubmit=async event=>{
      event.preventDefault();
      if(item.status==='confirmed'&&!confirm('확정된 일정을 해제하고 변경을 요청할까요? 이후 새 후보를 제안할 수 있습니다.'))return;
      submit.disabled=true;
      try {
        await ctx.api(`/api/me/matches/${match.match_id}/meetings/${item.proposal_id}/reply`,{
          method:'POST',body:JSON.stringify({availability:choice.value,private_note:note.value})});
        ctx.toast(choice.value==='available'?'일정에 동의했습니다. 담당 직원 승인 후 확정됩니다.':'응답을 전달했습니다.');
        if(panel.isConnected)await render(match,panel,ctx);
      } catch(error) {ctx.toast(error.message);}
      finally {if(submit.isConnected)submit.disabled=false;}
    };
    return form;
  }

  // ISO instant -> value for <input type="datetime-local"> in Korean time.
  const kstInput = iso => new Date(new Date(iso).getTime()+9*3600*1000).toISOString().slice(0,16);

  function proposeForm(match,panel,ctx,prefill=[]) {
    const editing=prefill.length>0;
    const form=el('form','meeting-propose'),rows=[],list=el('div','meeting-options');
    const error=el('p','error');error.setAttribute('role','alert');error.hidden=true;
    form.append(el('h4','',editing?'제안한 일정 수정':'새 일정 제안'),
      el('p','muted',editing?'상대가 수락하기 전까지 수정할 수 있습니다. 수정하면 기존 후보는 새 후보로 바뀝니다.'
        :'최대 3개 후보를 보낼 수 있습니다. 입력한 후보만 전달되며, 날짜·장소·주소를 모두 채워야 합니다.'),list);
    const renumber=()=>{
      rows.forEach((row,i)=>{row.legend.textContent=`후보 ${i+1}`;row.remove.disabled=rows.length===1;});
      list.replaceChildren(...rows.map(row=>row.box));
      addButton.disabled=rows.length===3;
    };
    const add=(value={})=>{
      if(rows.length===3)return;
      const box=el('fieldset'),legend=el('legend');
      const time=el('input');time.type='datetime-local';time.setAttribute('aria-label','날짜·시간 (한국 시간)');
      const venue=el('input');venue.placeholder='장소 (예: 카페 이름)';venue.maxLength=120;venue.setAttribute('aria-label','장소');
      const address=el('input');address.placeholder='주소';address.maxLength=240;address.setAttribute('aria-label','주소');
      if(value.starts_at)time.value=kstInput(value.starts_at);
      venue.value=value.venue||'';address.value=value.address||'';
      const remove=el('button','secondary','이 후보 삭제');remove.type='button';
      const row={box,legend,time,venue,address,remove};
      remove.onclick=()=>{if(rows.length===1)return;rows.splice(rows.indexOf(row),1);renumber();};
      box.append(legend,time,venue,address,remove);rows.push(row);renumber();
    };
    const addButton=el('button','secondary','후보 추가');addButton.type='button';addButton.onclick=()=>add();
    const submit=el('button','primary',editing?'수정한 일정 보내기':'상대에게 일정 제안');submit.type='submit';
    const actions=el('div','meeting-actions');actions.append(addButton,submit);
    if(editing){const cancel=el('button','secondary','수정 취소');cancel.type='button';cancel.onclick=()=>render(match,panel,ctx);actions.append(cancel);}
    form.append(error,actions);
    (editing?prefill:[{}]).forEach(item=>add(item));
    const fail=message=>{error.textContent=message;error.hidden=false;ctx.toast(message);};
    form.onsubmit=async event=>{
      event.preventDefault();error.hidden=true;
      const values=rows.map(row=>({time:String(row.time.value||'').trim(),venue:String(row.venue.value||'').trim(),address:String(row.address.value||'').trim()}));
      const partial=values.findIndex(v=>(v.time||v.venue||v.address)&&!(v.time&&v.venue&&v.address));
      if(partial>=0)return fail(`후보 ${partial+1}: 날짜·장소·주소를 모두 입력하거나 이 후보를 삭제해주세요.`);
      const filled=values.filter(v=>v.time&&v.venue&&v.address);
      if(!filled.length)return fail('후보를 하나 이상 입력해주세요.');
      // Explicit KST offset so the member's device timezone cannot shift the appointment.
      const options=filled.map(v=>({starts_at:new Date(v.time+':00+09:00').toISOString(),venue:v.venue,address:v.address}));
      const summary=options.map((o,i)=>`${i+1}. ${date(o.starts_at)} · ${o.venue}`).join('\n');
      if(!confirm(`${editing?'수정한 일정을':'이 일정을'} 상대 회원에게 보낼까요?\n\n${summary}`))return;
      submit.disabled=true;
      try {
        await ctx.api(`/api/me/matches/${match.match_id}/meetings`,{method:'POST',body:JSON.stringify({options})});
        ctx.toast(editing?'수정한 일정을 보냈습니다.':'상대 회원에게 일정 후보를 보냈습니다.');
        if(panel.isConnected)await render(match,panel,ctx);
      } catch(error) {fail(error.message);}
      finally {if(submit.isConnected)submit.disabled=false;}
    };
    return form;
  }

  async function render(match,panel,ctx) {
    panel.hidden=false;
    const loading=el('p','muted','만남 일정을 확인하고 있습니다…');
    panel.replaceChildren(loading);
    try {
      const data=await(await ctx.api(`/api/me/matches/${match.match_id}/meetings`)).json();
      if(!panel.isConnected||!panel.contains(loading))return;
      panel.replaceChildren(el('h4','','만남 일정 조율'),el('p','matching-note',
        '두 회원이 직접 일정을 제안하고 합의합니다. 같은 후보에 합의하면 담당 직원이 승인해 확정합니다. 의견은 본인과 담당 직원만 확인합니다.'));
      const open=data.items.filter(item=>['proposed','confirmed'].includes(item.status));
      if(!open.length)panel.append(el('p','muted','아직 제안된 일정이 없습니다. 아래에서 먼저 일정을 제안해 보세요.'));
      for(const item of data.items) {
        const card=el('section','meeting-card');
        const top=el('div','card-top');top.append(el('strong','',date(item.starts_at)+' (한국 시간)'),
          el('span','badge'+(item.status==='confirmed'?'':' inactive'),names[item.status]||item.status));
        card.append(top,el('p','',item.venue),el('p','muted',item.address));
        if(item.status==='proposed')card.append(el('p','muted',item.proposed_by_me?'내가 제안한 일정 · 상대 수락 전까지 수정할 수 있습니다':'상대 회원이 제안한 일정 · 후보 중 하나만 수락할 수 있습니다'));
        if(item.status==='confirmed')card.append(el('p','meeting-confirmed','담당 직원이 승인해 확정된 일정입니다.'));
        else if(item.both_available&&item.status==='proposed')card.append(el('p','muted','두 회원 합의 완료 · 담당 직원 승인을 기다리고 있습니다.'));
        if(item.my_reply&&!(item.proposed_by_me&&item.status==='proposed'))card.append(el('p','muted','내 응답: '+replies[item.my_reply.availability]));
        // The proposer is already recorded as available; only the counterpart answers an open option.
        if(item.can_reply&&!(item.proposed_by_me&&item.status==='proposed'))card.append(replyForm(match,item,panel,ctx));
        panel.append(card);
      }
      if(data.items.some(item=>item.status==='confirmed'))return;
      const live=data.items.filter(item=>item.status==='proposed'&&item.can_reply);
      if(live.some(item=>item.both_available)){panel.append(el('p','muted','수락된 일정이 담당 직원 승인을 기다리고 있어 새로 제안하거나 수정할 수 없습니다.'));return;}
      const mine=live.filter(item=>item.proposed_by_me);
      if(mine.length) {
        const edit=el('button','secondary','제안한 일정 수정');edit.type='button';
        edit.onclick=()=>{edit.hidden=true;panel.append(proposeForm(match,panel,ctx,mine));};
        panel.append(edit);
      } else if(live.length) {
        const other=el('button','secondary','다른 일정 제안하기');other.type='button';
        other.onclick=()=>{other.hidden=true;panel.append(proposeForm(match,panel,ctx));};
        panel.append(other);
      } else panel.append(proposeForm(match,panel,ctx));
    } catch(error) {
      if(panel.isConnected&&panel.contains(loading))panel.replaceChildren(el('p','error',error.message));
    }
  }
  return {render};
})();
