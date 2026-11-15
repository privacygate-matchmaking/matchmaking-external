const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.isConnected=true;this.hidden=false;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(key,value){this[key]=value;}
  contains(item){return this.children.some(child=>child===item||child.contains(item));}
}
function elements(root){return [root,...root.children.flatMap(elements)];}
function moduleContext(file){
  const context={window:{},document:{createElement:tag=>new Element(tag)},confirm:()=>true};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../external-web/public',file),'utf8'),context);
  return context;
}

for(const enabled of [false,true])test(`existing matching screen preserves rollout capability=${enabled}`,async()=>{
  const context=moduleContext('matching.js'),root=new Element('div');
  const data={items:[{match_id:'test-match',peer:{pseudo_id:'소개 상대'},status:'matched',created_at:'2026-11-13T00:00:00Z',direction:'sent'}]};
  await context.window.MatchingUI.render('matches',{root,api:async()=>({json:async()=>data}),isCurrent:()=>true,meetingScheduling:enabled});
  const labels=elements(root).map(n=>n.textContent);
  assert.equal(labels.includes('만남 일정 확인'),enabled);
  assert.equal(labels.includes('내 공개 범위 설정'),true);
  assert.equal(labels.includes('관계 종료'),true);
});

test('meeting response sends only the current member availability and staff-only note',async()=>{
  const context=moduleContext('meetings.js'),panel=new Element('div'),calls=[];
  const data={items:[{proposal_id:'p1',status:'proposed',starts_at:'2026-12-01T03:00:00Z',venue:'합성 카페',address:'시험 주소',can_reply:true,both_available:false,my_reply:null}]};
  const ctx={api:async(url,options)=>{calls.push({url,options});return {json:async()=>data};},toast:()=>{}};
  await context.window.MeetingsUI.render({match_id:'m1'},panel,ctx);
  const form=elements(panel).find(n=>n.tagName==='form');
  form.children.find(n=>n.tagName==='select').value='available';
  form.children.find(n=>n.tagName==='textarea').value='직원에게만 전달할 의견';
  assert.equal(form.children.find(n=>n.tagName==='textarea').maxLength,300);
  await form.onsubmit({preventDefault:()=>{}});
  const sent=calls.find(call=>call.options?.method==='POST');
  assert.equal(sent.url,'/api/me/matches/m1/meetings/p1/reply');
  assert.deepEqual(JSON.parse(sent.options.body),{availability:'available',private_note:'직원에게만 전달할 의견'});
});

test('confirmed meeting only offers a change request and asks before invalidating confirmation',async()=>{
  const context=moduleContext('meetings.js'),panel=new Element('div'),calls=[];
  context.confirm=()=>false;
  const data={items:[{proposal_id:'p1',status:'confirmed',starts_at:'2026-12-01T03:00:00Z',venue:'카페',address:'주소',can_reply:true,my_reply:null}]};
  const ctx={api:async(url,options)=>{calls.push({url,options});return {json:async()=>data};},toast:()=>{}};
  await context.window.MeetingsUI.render({match_id:'m1'},panel,ctx);
  const form=elements(panel).find(n=>n.tagName==='form');
  const select=form.children.find(n=>n.tagName==='select');
  assert.equal(select.children.length,1);
  assert.equal(select.children[0].value,'change_requested');
  await form.onsubmit({preventDefault:()=>{}});
  assert.equal(calls.some(call=>call.options?.method==='POST'),false);
});

test('proposer edits until accepted; empty rows are skipped, partial rows block, and a confirm is required',async()=>{
  const context=moduleContext('meetings.js'),panel=new Element('div'),calls=[];
  const data={items:[{proposal_id:'p1',status:'proposed',starts_at:'2026-12-01T10:30:00Z',venue:'카페',address:'주소',
    can_reply:true,both_available:false,proposed_by_me:true,my_reply:{availability:'available',private_note:''}}]};
  const ctx={api:async(url,options)=>{calls.push({url,options});return {json:async()=>data};},toast:()=>{}};
  await context.window.MeetingsUI.render({match_id:'m1'},panel,ctx);
  assert.equal(elements(panel).some(n=>n.className==='meeting-reply'),false);
  elements(panel).find(n=>n.textContent==='제안한 일정 수정').onclick();
  const form=elements(panel).find(n=>n.className==='meeting-propose');
  const inputs=()=>elements(form).filter(n=>n.tagName==='input');
  assert.equal(inputs()[0].value,'2026-12-01T19:30');                       // prefilled in KST
  elements(form).find(n=>n.textContent==='후보 추가').onclick();
  elements(form).find(n=>n.textContent==='후보 추가').onclick();
  assert.deepEqual(elements(form).filter(n=>n.tagName==='legend').map(n=>n.textContent),['후보 1','후보 2','후보 3']);
  // Delete the first: the others are renumbered and at least one always remains.
  elements(form).filter(n=>n.textContent==='이 후보 삭제')[0].onclick();
  assert.deepEqual(elements(form).filter(n=>n.tagName==='legend').map(n=>n.textContent),['후보 1','후보 2']);
  const [t1,v1,a1]=inputs();t1.value='2026-12-05T19:30';v1.value='합성 카페';a1.value='';     // partial row
  await form.onsubmit({preventDefault:()=>{}});
  assert.equal(calls.some(c=>c.options?.method==='POST'),false);
  assert.ok(elements(form).some(n=>n.className==='error'&&n.textContent.includes('후보 1')));
  a1.value='시험시 1번길';                                                  // row 2 stays empty -> skipped
  context.confirm=()=>false;await form.onsubmit({preventDefault:()=>{}});
  assert.equal(calls.some(c=>c.options?.method==='POST'),false);           // cancelled at the popup
  context.confirm=()=>true;await form.onsubmit({preventDefault:()=>{}});
  const sent=calls.find(call=>call.options?.method==='POST');
  assert.equal(sent.url,'/api/me/matches/m1/meetings');
  assert.deepEqual(JSON.parse(sent.options.body),{options:[{starts_at:'2026-12-05T10:30:00.000Z',venue:'합성 카페',address:'시험시 1번길'}]});
});

test('accepted option waits for staff and cannot be edited',async()=>{
  const context=moduleContext('meetings.js'),panel=new Element('div');
  const data={items:[{proposal_id:'p1',status:'proposed',starts_at:'2026-12-01T10:30:00Z',venue:'카페',address:'주소',
    can_reply:true,both_available:true,proposed_by_me:true,my_reply:{availability:'available',private_note:''}}]};
  await context.window.MeetingsUI.render({match_id:'m1'},panel,{api:async()=>({json:async()=>data}),toast:()=>{}});
  assert.equal(elements(panel).some(n=>n.textContent==='제안한 일정 수정'||n.className==='meeting-propose'),false);
});
