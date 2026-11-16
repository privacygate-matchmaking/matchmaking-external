'use strict';
window.MatchingUI = (() => {
  const scopeNames = {name:'이름',profile:'소개 정보',education:'학력 검토 결과',employment:'재직 검토 결과',income:'소득 구간',marital_status:'혼인관계 검토 결과',property:'부동산 보유 검토 결과',photo:'프로필 사진',documents:'검토·마스킹된 문서 사본',contact:'연락처'};
  const scopeHints = {name:'승인되면 상대 화면의 소개 정보에서 "소개 상대" 대신 실명이 보입니다.',
    photo:'직원이 검토한 사진(위치 등 정보 제거)입니다.',
    documents:'서류 심사를 통과한 학력·재직·소득·혼인·부동산 서류의 개인정보 가림 사본입니다. 원본은 제공하지 않습니다.',
    contact:'만남 일정이 확정되고 두 사람 모두 연락처 공개에 동의한 뒤에만 승인 요청할 수 있습니다.'};
  const releaseOnly = [];
  // 상호 공개 항목: 나와 상대가 같은 항목을 모두 공개해야 서로 볼 수 있다 (한쪽만 공개하면 둘 다 못 봄)
  const conditional = {contact:' · 일정 확정 후 · 서로 공개 시',name:' · 서로 공개 시',photo:' · 서로 공개 시',income:' · 서로 공개 시',marital_status:' · 서로 공개 시',property:' · 서로 공개 시'};
  const states = {pending:'응답 대기',matched:'매칭 성립',rejected:'거절',cancelled:'신청 취소',expired:'응답 기간 만료',ended:'관계 종료'};
  const el = (tag, cls, text) => { const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n; };
  const date = x => x ? new Date(x).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}) : '—';
  const won = n => new Intl.NumberFormat('ko-KR').format(n)+'원';
  function note(text) { return el('div','matching-note',text); }
  function empty(text) { const n=el('div','empty');n.append(el('div','empty-symbol','◇'),el('h3','',text));return n; }
  function button(text, fn, primary=false) {
    const b=el('button',primary?'primary':'secondary',text);b.type='button';
    b.onclick=async()=>{b.disabled=true;try{await fn(b);}finally{if(b.isConnected)b.disabled=false;}};
    return b;
  }
  function title(name, text) { const p=el('div','matching-heading');p.append(el('h2','',name),el('p','muted',text));return p; }
  function lab(data,root) {
    if(data.data_source==='synthetic')root.append(note('합성 회원 실습 모드 · 검토 결과는 시험용 예시이며 실제 증빙 심사 결과가 아닙니다.'));
  }
  async function render(tab,ctx) {
    const {api,root,isCurrent}=ctx;
    const endpoint={candidates:'/api/me/candidates',matches:'/api/me/matches',membership:'/api/me/membership'}[tab];
    const data=await(await api(endpoint)).json();
    if(!isCurrent())return;
    lab(data,root);
    if(tab==='candidates')await candidates(data,ctx);
    if(tab==='matches')matches(data,ctx);
    if(tab==='membership')membership(data,ctx);
  }
  async function candidates(data,ctx) {
    const {root,api,reload,toast,isCurrent}=ctx;
    root.append(title('새로운 인연을 만나보세요','필요한 소개 정보부터 확인하고, 서로의 의사를 존중하며 신청하세요.'));
    const wallet=await(await api('/api/me/membership')).json();if(!isCurrent())return;
    const w=wallet.wallet;
    const band=el('div','wallet-band');
    band.append(el('div','',w.active?`사용 가능 ${w.available}개 · 예약 ${w.reserved}개 · 사용 ${w.used}개`:'현재 일반회원입니다.'),
      el('span','muted',w.active?'이용 기한 '+date(w.expires_at):'신청은 정회원 이용권이 필요합니다. 받은 신청에는 응답할 수 있습니다.'));
    root.append(band,rulesBox(data.self,w));
    if(!data.items.length){root.append(empty('현재 소개 가능한 후보가 없습니다. 진행 중이거나 성사된 상대는 매칭 현황에서 확인하세요.'));return;}
    const grid=el('div','candidate-grid');grid.tabIndex=0;grid.setAttribute('aria-label','소개 후보 (옆으로 넘겨 보기)');
    const cards=[];
    const lastText={ended:'이전 매칭이 종료된 상대입니다.',rejected:'이전 신청이 거절된 상대입니다.',cancelled:'이전 신청을 취소한 상대입니다.',expired:'이전 신청이 응답 기한을 넘긴 상대입니다.'};
    for(const item of data.items) {
      const c=el('article','candidate-card');
      const closed=item.category==='closed';
      c.dataset.category=closed?'closed':'new';
      const tag=el('span','candidate-tag '+(closed?'closed':'new'),closed?'매칭 취소':'새 매칭');
      const top=el('div','candidate-top');top.append(el('div','candidate-avatar',item.pseudo_id.replace('M-','').slice(0,2)),tag);
      c.append(top,el('h3','',item.pseudo_id),el('div','candidate-meta',[item.age_band,item.height_cm?`${item.height_cm}cm`:null,item.weight_kg?`${item.weight_kg}kg`:null].filter(Boolean).join(' · ')+(item.height_cm||item.weight_kg?' (본인 신고)':'')),
        el('p','candidate-intro',item.introduction),el('p','candidate-note','직원이 소개한 회원입니다. 상세 자료는 매칭 후 공개 동의와 승인을 거쳐 확인합니다.'));
      const b=button('매칭 신청 ↗',async()=>{
        if(!confirm(`${item.pseudo_id}님에게 신청할까요? 매칭권 1개가 예약되고, 수락 시 사용이 확정됩니다.`))return;
        try{
          await api('/api/me/matches',{method:'POST',body:JSON.stringify({recipient_member_id:item.member_id,idempotency_key:crypto.randomUUID()})});
          toast('신청했습니다. 상대의 응답을 기다려주세요.');await reload();
        }catch(e){toast(e.message);}
      },true);
      b.disabled=!w.active||w.available<1||!data.self.eligible||Boolean(item.retry_at);
      if(closed)c.append(note((lastText[item.last_status]||'이전 매칭 기록이 있는 상대입니다.')+(item.retry_at?` ${date(item.retry_at)} 이후 다시 신청할 수 있습니다.`:' 다시 신청할 수 있습니다.')));
      if(!data.self.eligible)c.append(note('위 매칭 조건(신분증·학력·프로필 사진 승인)을 모두 갖춘 뒤 신청할 수 있습니다.'));
      c.append(b);cards.push(c);
    }
    // 분류: 전체 · 새 매칭 · 매칭 취소 (종료·거절·취소·만료된 상대는 서버가 맨 뒤로 보낸다)
    const counts={all:cards.length,new:cards.filter(c=>c.dataset.category==='new').length};counts.closed=counts.all-counts.new;
    const filters=el('div','candidate-filters');filters.setAttribute('role','group');filters.setAttribute('aria-label','후보 분류');
    const countLine=el('p','candidate-count muted');
    const show=kind=>{
      const list=kind==='all'?cards:cards.filter(c=>c.dataset.category===kind);
      grid.replaceChildren(...list);grid.scrollLeft=0;
      for(const f of filters.children){const on=f.dataset.kind===kind;f.classList.toggle('active',on);f.setAttribute('aria-pressed',String(on));}
      countLine.textContent=list.length?`후보 ${list.length}명 · 옆으로 넘겨 보세요`:'이 분류에 해당하는 후보가 없습니다.';
      requestAnimationFrame(edges);
    };
    for(const [kind,label] of [['all','전체'],['new','새 매칭'],['closed','매칭 취소']]) {
      const f=el('button','candidate-filter '+kind);f.type='button';f.dataset.kind=kind;
      f.append(el('span','dot'),document.createTextNode(` ${label} ${counts[kind]}`));
      f.onclick=()=>show(kind);filters.append(f);
    }
    const strip=el('div','candidate-strip');
    const prev=el('button','candidate-scroll prev','‹'),next=el('button','candidate-scroll next','›');
    prev.type=next.type='button';prev.setAttribute('aria-label','이전 후보');next.setAttribute('aria-label','다음 후보');
    const step=dir=>grid.scrollBy({left:dir*(grid.clientWidth*0.9),behavior:'smooth'});
    prev.onclick=()=>step(-1);next.onclick=()=>step(1);
    const edges=()=>{prev.disabled=grid.scrollLeft<=4;next.disabled=grid.scrollLeft+grid.clientWidth>=grid.scrollWidth-4;};
    grid.addEventListener('scroll',edges,{passive:true});
    strip.append(prev,grid,next);
    const head=el('div','candidate-head');head.append(countLine,filters);
    root.append(head,strip);
    show('all');
  }
  // 매칭 조건: 필수 심사(신분증·학력·프로필 사진) + 정회원 이용권, 신청 규칙
  function rulesBox(self,w) {
    const r=self.requirements||{};
    const box=el('section','match-rules');box.setAttribute('aria-label','매칭 조건');
    box.append(el('h3','','매칭 조건'));
    const list=el('ul','rule-checks');
    const item=(ok,text,hint)=>{const li=el('li',ok?'ok':'todo');li.append(el('span','mark',ok?'✓':'!'),el('span','',text));if(!ok&&hint)li.append(el('small','muted',' '+hint));list.append(li);};
    item(r.identity,'신분증(본인 확인) 심사 승인','증빙 관리에서 제출');
    item(r.education,'졸업증명서(학력) 심사 승인','증빙 관리에서 제출');
    item(r.photo,'프로필 사진 심사 승인','증빙 관리에서 제출');
    item(Boolean(w.active&&w.available>0),'정회원 이용권 (매칭권 1개 이상)','이용권 탭에서 구매 · 받은 신청 응답은 이용권 없이 가능');
    box.append(list,el('p','rule-note muted','후보에는 같은 조건(신분증·학력·프로필 사진 승인)을 갖춘 이성 회원만 보입니다. 신청은 하루 3건·동시 대기 3건, 응답 기한 72시간이며, 거절된 상대에게는 30일 동안 다시 신청할 수 없습니다. 프로필 사진은 매칭 후 서로 제공에 동의해야 서로 볼 수 있습니다.'));
    return box;
  }
  function matches(data,ctx) {
    const {root}=ctx;
    root.append(title('우리의 매칭 현황','수락은 관계의 시작입니다. 정보 공개는 각자가 항목과 기한을 선택한 뒤 가능합니다.'));
    if(!data.items.length){root.append(empty('아직 주고받은 매칭 신청이 없습니다.'));return;}
    const cards=[];
    for(const item of data.items){
      const c=el('article','match-card');const top=el('div','card-top');
      top.append(el('h3','',item.peer.pseudo_id),el('span','badge'+(item.status==='matched'?'':' inactive'),states[item.status]||item.status));
      c.append(top,el('p','muted',`${item.direction==='sent'?'보낸 신청':'받은 신청'} · ${date(item.created_at)}`));
      if(item.status==='pending')c.append(el('p','muted','응답 기한 '+date(item.expires_at)));
      const actions=el('div','matching-actions');
      function action(label,value){actions.append(button(label,async()=>{
        if(!confirm(`${label} 처리할까요?`))return;
        try{await ctx.api(`/api/me/matches/${item.match_id}/decision`,{method:'POST',body:JSON.stringify({action:value})});ctx.toast('처리했습니다.');await ctx.reload();}
        catch(e){ctx.toast(e.message);}
      },value==='accept'));}
      if(item.status==='pending'){
        if(item.direction==='received'){action('수락','accept');action('거절','reject');}else action('신청 취소','cancel');
      }
      const detail=el('div','matching-detail');detail.hidden=true;
      if(item.status==='matched'){
        if(ctx.meetingScheduling)actions.append(button('만남 일정 확인',async()=>{await MeetingsUI.render(item,detail,ctx);}));
        actions.append(button('내 공개 범위 설정',async()=>{await sharing(item,detail,ctx);}));
        actions.append(button('상대가 허용한 정보 보기',async()=>{await peer(item,detail,ctx);}));
        action('관계 종료','end');
      }
      c.append(actions,detail);cards.push(c);
    }
    const box=el('div','paged-list');root.append(box);
    if(window.Pager)Pager.mount(box,cards,7,{label:'매칭 현황 페이지'});else box.append(...cards);
  }
  async function sharing(item,panel,ctx){
    panel.hidden=false;panel.replaceChildren(el('p','muted','공개 설정을 불러오고 있습니다…'));
    try {
      const data=await(await ctx.api(`/api/me/matches/${item.match_id}/sharing`)).json();
      if(!panel.isConnected)return;
      panel.replaceChildren(el('h4','','내 정보를 상대에게 공개하기'),note('원본 증빙은 공개하지 않습니다. 체크한 항목은 담당 직원이 요청하고 승인 담당자가 승인해야 상대 화면에 표시됩니다(동의만으로는 보이지 않습니다). 이름·프로필 사진·소득·혼인관계·부동산·연락처는 "서로 공개" 항목으로, 나와 상대가 같은 항목을 모두 공개해야 서로 볼 수 있습니다(나만 공개하면 상대도 볼 수 없습니다). 연락처는 만남 일정 확정도 필요합니다. 동의는 언제든 철회할 수 있고, 철회하면 상대 화면에서도 사라집니다.'));
      const form=el('form','sharing-form');const checks=[];
      for(const [code,name] of Object.entries(scopeNames)){
        const label=el('label','check-label');const input=el('input');input.type='checkbox';input.value=code;
        input.checked=!!(data.own_grant?.active&&data.own_grant.scopes.includes(code));
        label.append(input,el('span','',name+(conditional[code]||'')));checks.push(input);form.append(label);
        if(scopeHints[code])form.append(el('p','scope-hint',scopeHints[code]));
      }
      const lid='expiry-'+item.match_id;const label=el('label','','공개 만료일시 (최대 7일)');label.htmlFor=lid;
      const expiry=el('input');expiry.id=lid;expiry.type='datetime-local';expiry.required=true;
      const t=new Date(data.own_grant?.active?data.own_grant.expires_at:Date.now()+86400000);
      expiry.value=new Date(t.getTime()-t.getTimezoneOffset()*60000).toISOString().slice(0,16);
      const save=el('button','primary','동의 저장');save.type='submit';form.append(label,expiry,save);
      form.onsubmit=async e=>{
        e.preventDefault();const scopes=checks.filter(x=>x.checked).map(x=>x.value);
        if(!scopes.length){ctx.toast('공개할 항목을 선택하거나 아래에서 동의를 철회해주세요.');return;}
        const end=new Date(expiry.value);if(!Number.isFinite(end.getTime()))return;
        save.disabled=true;
        try{await ctx.api(`/api/me/matches/${item.match_id}/sharing`,{method:'POST',body:JSON.stringify({scopes,expires_at:end.toISOString()})});ctx.toast('내 공개 범위를 저장했습니다.');await sharing(item,panel,ctx);}
        catch(error){ctx.toast(error.message);}finally{save.disabled=false;}
      };
      panel.append(form);
      if(data.own_grant?.active)panel.append(button('내 공개 동의 철회',async()=>{
        if(!confirm('내 공개 동의를 철회할까요? 이후 서버 열람이 제한됩니다.'))return;
        try{await ctx.api(`/api/me/matches/${item.match_id}/sharing/revoke`,{method:'POST',body:'{}'});ctx.toast('동의를 철회했습니다.');await sharing(item,panel,ctx);}catch(e){ctx.toast(e.message);}
      }));
    }catch(e){panel.replaceChildren(note(e.message));}
  }
  async function peer(item,panel,ctx){
    panel.hidden=false;panel.replaceChildren(el('p','muted','현재 관계와 동의를 확인하고 있습니다…'));
    try{
      const data=await(await ctx.api(`/api/me/matches/${item.match_id}/profile`)).json();if(!panel.isConnected)return;
      panel.replaceChildren(el('h4','','상대가 허용한 정보'));lab(data,panel);
      // Every item is shown only after the counselor's request and the approver's approval (latest approved material).
      let delivered=[];
      try{delivered=(await(await ctx.api('/api/me/deliveries')).json()).items.filter(d=>d.match_id===item.match_id&&d.request_status==='approved');}catch(e){delivered=[];}
      if(!panel.isConnected)return;
      if(delivered.length){
        const viewer=el('div','material-view');panel.append(viewer);
        if(window.MaterialUI)await MaterialUI.show(delivered[0],viewer,ctx,{compact:true});
      }else{
        panel.append(note('상대가 공개에 동의한 항목: '+(data.consented_scopes||[]).map(x=>scopeNames[x]||x).join(', ')+' · 담당 직원 요청과 승인 담당자 승인 후 여기에 표시됩니다.'));
      }
      if((data.mutual_pending||[]).length)panel.append(note(`${data.mutual_pending.map(x=>scopeNames[x]||x).join(', ')}: 상대는 공개에 동의했습니다. 나도 같은 항목을 공개해야 서로 볼 수 있습니다.`));
      if(data.contact_pending)panel.append(note('연락처: 만남 일정이 확정되고 두 사람 모두 연락처 공개에 동의해야 승인 요청할 수 있습니다.'));
      panel.append(el('p','muted','공개 기한 '+date(data.expires_at)));
    }catch(e){panel.replaceChildren(note(e.message));}
  }
  function membership(data,ctx){
    const {root}=ctx,w=data.wallet,p=data.plan;
    root.append(title('소중한 인연을 위한 정회원','이용권은 매칭 신청 자격입니다. 상대의 정보 공개 동의와는 별개입니다.'));
    const card=el('article','plan-card');card.append(el('span','eyebrow','IEUM MEMBERSHIP'),el('h3','',p.name),el('p','plan-price',won(p.amount_won)),el('p','muted',`VAT 포함 · ${p.months}개월 · 매칭권 ${p.credits}개`));
    card.append(note('신청 시 1개 예약 → 수락 시 사용 확정. 거절·72시간 무응답·수락 전 취소 시 예약 해제. 매칭 성립 후 종료 시 자동 반환하지 않습니다.'));
    if(w.active){
      card.append(el('p','wallet-numbers',`사용 가능 ${w.available}개 / 예약 ${w.reserved}개 / 사용 ${w.used}개`),el('p','muted','이용 기한 '+date(w.expires_at)));
    }else if(data.test_payments_enabled){
      card.append(note('시험 결제 모드 · 실제 카드 입력이나 금전 거래가 없습니다.'));
      card.append(button('시험 결제로 이용권 활성화',async()=>{
        if(!confirm('실제 돈이 결제되지 않는 실습용 이용권을 활성화할까요?'))return;
        try{
          const order=await(await ctx.api('/api/me/orders',{method:'POST',body:JSON.stringify({plan_code:p.code,idempotency_key:crypto.randomUUID()})})).json();
          await ctx.api(`/api/me/orders/${order.order_id}/test-confirm`,{method:'POST',body:'{}'});
          ctx.toast('시험 이용권이 활성화됐습니다.');await ctx.reload();
        }catch(e){ctx.toast(e.message);}
      },true));
    }else card.append(note('결제 서비스 연결 대기 중입니다. 이 환경에서는 결제할 수 없습니다.'));
    root.append(card);
    if(data.orders.length){const history=el('section','order-history');history.append(el('h3','','최근 주문'));for(const order of data.orders)history.append(el('p','muted',`${date(order.created_at)} · ${won(order.amount_won)} · ${order.status==='paid'?'시험 결제 완료':'대기'}`));root.append(history);}
  }
  return {render};
})();
