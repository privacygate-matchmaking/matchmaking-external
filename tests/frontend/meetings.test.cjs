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
