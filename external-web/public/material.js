'use strict';
// 승인된 소개 자료 보기: 검토 결과 텍스트, 검토된 사진·마스킹 문서 사본, (조건 충족 시) 연락처.
// 이미지는 형식·문자 집합을 확인한 뒤 data: 주소로만 표시한다 (CSP img-src 'self' data:).
window.MaterialUI = (() => {
  // 회원 화면에는 직원용 처리 설명(메타데이터 제거 등)을 빼고 보여 준다. 자료 원문(검토 결과)은 그대로라 다운로드 무결성 확인에 영향 없음
  const memberText = f => f.scope==='photo' ? String(f.value||'').replace(/\s*\(위치 등 메타데이터 제거\)/,'') : f.value;
  const names = {name:'이름',profile:'공개 소개',education:'학력 확인',employment:'재직 확인',income:'소득 구간',marital_status:'혼인관계',
    property:'부동산',photo:'프로필 사진',documents:'검토·마스킹 문서 사본',contact:'연락처'};
  const el = (tag, cls, text) => { const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n; };
  const date = x => x ? new Date(x).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}) : '—';
  const safeImage = a => a && ['image/png','image/jpeg'].includes(a.media_type) && typeof a.data_base64==='string'
    && a.data_base64.length < 700000 && /^[A-Za-z0-9+/]+={0,2}$/.test(a.data_base64);
  // 안전한 반출: 내려받기 직전에 일회용 토큰(60초·1회)을 받아 헤더로 보낸다 (주소창·기록에 남지 않음).
  // 서버가 토큰을 요구하지 않는 모드(비교 시연 baseline)면 그대로 내려받는다.
  async function fetchDelivery(api, item) {
    const id = encodeURIComponent(item.delivery_id);
    const t = await (await api(`/api/me/deliveries/${id}/download-token`, {method:'POST', body:'{}'})).json();
    return api(`/api/me/deliveries/${id}/download`, t.required ? {headers:{'X-Download-Token': t.token}} : {});
  }
  async function show(item, panel, ctx, options={}) {
    panel.hidden=false; panel.replaceChildren(el('p','muted','자료를 확인하고 있습니다…'));
    try {
      const response=await fetchDelivery(ctx.api, item);
      if(!(response.headers.get('content-type')||'').includes('json')){panel.replaceChildren(el('p','muted','이 자료는 화면 보기를 지원하지 않습니다. 다운로드해 주세요.'));return;}
      const doc=await response.json();
      const only=options.attachmentsOnly;
      panel.replaceChildren(...(only||options.compact?[]:[el('h4','','소개 자료'),el('p','matching-note','상대가 동의하고 담당 직원 요청·승인 담당자 승인을 거친 자료입니다. 원본 증빙이 아니며, 만남 준비 외 용도로 저장·공유하지 마세요.')]));
      for(const f of (doc.fields||[]).filter(f=>!only||(f.attachments||[]).length)) {
        const row=el('div','result-row');
        row.append(el('strong','',names[f.scope]||f.label),el('p','',memberText(f)),el('span','muted','검토일 '+date(f.reviewed_at)));
        for(const a of (f.attachments||[])) {
          if(!safeImage(a))continue;
          const fig=el('figure','material-figure');const img=el('img');img.alt=a.label||'검토된 이미지';img.loading='lazy';
          img.src=`data:${a.media_type};base64,${a.data_base64}`;fig.append(img,el('figcaption','muted',a.label||''));row.append(fig);
        }
        panel.append(row);
      }
      if(!panel.children.length&&!only)panel.append(el('p','muted','표시할 항목이 없습니다.'));
    } catch(e) { panel.replaceChildren(el('p','matching-note',e.message)); }
  }
  return {show, fetchDelivery};
})();
