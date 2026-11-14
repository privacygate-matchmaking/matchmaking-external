'use strict';
window.JourneyUI = (() => {
  const names={profile:'공개 소개',education:'학력 확인',employment:'재직 확인',photo:'사진',contact:'연락처',income:'소득',marital_status:'혼인 정보'};
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const date=v=>v?new Date(v).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'—';
  function move(label,tab,ctx){const b=el('button',label);b.type='button';b.className='secondary';b.onclick=()=>ctx.navigate(tab);return b;}
  async function home(ctx) {
    const endpoints=['/api/me/candidates','/api/me/membership','/api/me/matches','/api/me/deliveries','/api/me/evidence-files'];
    const result=await Promise.allSettled(endpoints.map(async p=>(await ctx.api(p)).json()));
    if(!ctx.isCurrent())return;
    const value=i=>result[i].status==='fulfilled'?result[i].value:null;
    const candidates=value(0),wallet=value(1)?.wallet,matches=value(2)?.items,deliveries=value(3)?.items,evidence=value(4)?.items;
    const title=el('div');title.className='matching-heading';title.append(el('h2','나의 이용 현황'),el('p','증빙 심사부터 만남 확정까지, 현재 상태와 다음 할 일을 확인하세요.'));ctx.root.append(title);
    const stages=el('ol');stages.className='journey-stages';
    for(const text of ['증빙 제출·직원 심사','직원이 소개한 상대 확인','이용권 신청·상대 수락','각자의 공개 항목 동의','직원 일정 조율·자료 승인'])stages.append(el('li',text));
    ctx.root.append(stages);
    const steps=[
      ['증빙 심사',candidates?(candidates.self.eligible?'학력 확인 완료 · 매칭 신청 가능':'학력 확인이 필요합니다. 증빙을 제출하고 담당 직원의 심사를 기다려주세요.'):'심사 상태를 불러오지 못했습니다.', 'evidence','증빙 제출·결과 확인'],
      ['이용권',wallet?(wallet.active?`신청 가능 ${wallet.available}회 · 응답 대기 예약 ${wallet.reserved}회 · 사용 ${wallet.used}회`:'유효한 이용권이 없습니다. 받은 신청에는 이용권 없이 응답할 수 있습니다.'):'이용권 상태를 불러오지 못했습니다.','membership','이용권 확인'],
      ['소개 후보',candidates?`현재 소개 가능한 상대 ${candidates.items.length}명 · 전체 회원 목록은 공개하지 않습니다.`:'소개 정보를 불러오지 못했습니다.','candidates','소개 상대 확인'],
      ['신청·만남',matches?`받은 응답 대기 ${matches.filter(m=>m.status==='pending'&&m.direction==='received').length}건 · 성사된 매칭 ${matches.filter(m=>m.status==='matched').length}건`:'매칭 상태를 불러오지 못했습니다.','matches','응답·동의·일정 확인'],
      ['제공 자료',deliveries?`이용 가능한 승인 자료 ${deliveries.filter(d=>d.request_status==='approved').length}건 · 직원 승인 전에는 다운로드할 수 없습니다.`:'제공 자료를 불러오지 못했습니다.','deliveries','승인 자료 확인']
    ];
    for(const [name,text,tab,label] of steps){const card=el('article');card.className='form-panel';card.append(el('h3',name),el('p',text),move(label,tab,ctx));ctx.root.append(card);}
    if(evidence?.some(e=>e.review_result==='fail'))ctx.root.append(el('p','반려된 증빙이 있습니다. 증빙 관리에서 결과를 확인하고 시험 자료를 다시 제출하세요.'));
    ctx.root.append(el('p','매칭 수락과 개인정보 공개 동의는 별도입니다. 공개 범위를 바꾸거나 철회하면 이후 열람·다운로드에 다시 반영됩니다. 이미 받은 파일까지 회수되지는 않습니다.'));
  }
  async function consents(ctx) {
    const data=await (await ctx.api('/api/me/matches')).json();
    const results=await Promise.all(data.items.map(async item=>({item,grant:(await (await ctx.api(`/api/me/matches/${item.match_id}/sharing`)).json()).own_grant})));
    if(!ctx.isCurrent())return;
    ctx.root.append(el('h2','내가 직접 설정한 매칭 공개 동의'),el('p','매칭 현황에서 상대별로 항목과 기한을 선택합니다. 동의를 저장해도 파일은 직원의 별도 반출 승인을 거쳐 제공됩니다.'),move('매칭별 공개 범위 설정','matches',ctx));
    const grants=results.filter(r=>r.grant);
    if(!grants.length)ctx.root.append(el('p','설정한 매칭 공개 동의가 없습니다. 상대의 수락 후 매칭 현황에서 설정하세요.'));
    for(const {item,grant:g} of grants){const card=el('article');card.className='form-panel';card.append(el('h3',item.peer.pseudo_id+'에게 공개'),el('p',g.scopes.map(s=>names[s]||s).join(' · ')),el('p',(g.revoked_at?'철회':g.active?'유효':'만료 또는 관계 종료')+' · 기한 '+date(g.expires_at)));
      if(g.active){const b=el('button','공개 동의 철회');b.type='button';b.className='text-button';b.onclick=async()=>{if(!confirm('이 상대에게 공개한 동의를 철회할까요? 승인된 자료도 다음 다운로드부터 차단됩니다.'))return;b.disabled=true;try{await ctx.api(`/api/me/matches/${item.match_id}/sharing/revoke`,{method:'POST',body:'{}'});await ctx.reload();}catch(e){ctx.toast(e.message);b.disabled=false;}};card.append(b);}
      ctx.root.append(card);
    }
    ctx.root.append(el('h3','기존 방식의 제공 동의 내역'),el('p','아래는 이전에 등록한 별도 제공 동의입니다. 기존 기록을 보존하며 새 매칭 자료에는 위의 매칭 동의를 사용합니다.'));
  }
  return {home,consents};
})();
