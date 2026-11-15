const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element {
  constructor(tag,text){this.tagName=tag;this.textContent=text;this.children=[];this.isConnected=true;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(k,v){this[k]=v;}
}
const nodes=n=>[n,...n.children.flatMap(nodes)];
function setup(file){
  const context={window:{},document:{createElement:t=>new Element(t),createTextNode:t=>new Element('text',t)},confirm:()=>true};
  vm.createContext(context);vm.runInContext(fs.readFileSync(file,'utf8'),context);return context;
}
const file=path.join(__dirname,'../../external-web/public/journey.js');
test('member home shows eligibility and actionable stages without inventing API results',async()=>{
  const c=setup(file),root=new Element('div'),moves=[];
  const responses={'/api/me/candidates':{self:{eligible:false},items:[]},'/api/me/membership':{wallet:{active:false}},'/api/me/matches':{items:[]},'/api/me/deliveries':{items:[]},'/api/me/evidence-files':{items:[]}};
  await c.window.JourneyUI.home({root,api:async p=>({json:async()=>responses[p]}),isCurrent:()=>true,navigate:x=>moves.push(x)});
  assert.ok(nodes(root).some(n=>n.textContent?.includes('학력 확인이 필요')));
  const b=nodes(root).find(n=>n.textContent==='증빙 제출·결과 확인');b.onclick();assert.deepEqual(moves,['evidence']);
});
test('failed service section remains an error rather than zero successful records',async()=>{
  const c=setup(file),root=new Element('div');
  await c.window.JourneyUI.home({root,api:async()=>{throw Error('offline');},isCurrent:()=>true,navigate:()=>{}});
  assert.ok(nodes(root).some(n=>n.textContent?.includes('제공 자료를 불러오지 못')));
  assert.ok(!nodes(root).some(n=>n.textContent?.includes('승인 자료 0건')));
});
test('stale home response cannot overwrite another screen',async()=>{
  const c=setup(file),root=new Element('div');
  await c.window.JourneyUI.home({root,api:async()=>({json:async()=>({})}),isCurrent:()=>false});
  assert.equal(root.children.length,0);
});
test('staff sends only explicitly selected supported consent scopes',async()=>{
  const c=setup(path.join(__dirname,'../../../matchmaking-internal/internal-web/public/matching-staff.js'));
  const root=new Element('div'),sent=[];
  const item={label:'Assigned member',status:'matched',created_at:'2026-01-01',releases:[],grants:[{
    grant_id:'g1',owner_label:'Owner',recipient_label:'Recipient',active:true,can_prepare:true,
    supported_scopes:['education','employment'],scopes:['education','employment'],expires_at:'2026-12-01'}]};
  await c.window.StaffMatchingUI.render({root,api:async(p,o)=>{if(o)sent.push({p,o});return {items:[item]};}});
  assert.equal(nodes(root).some(n=>n.tagName==='form'),false);           // list first, detail on demand
  nodes(root).find(n=>n.textContent==='상세').onclick();
  const checks=nodes(root).filter(n=>n.tagName==='input');checks[1].checked=false;
  await nodes(root).find(n=>n.tagName==='form').onsubmit({preventDefault(){}});
  assert.equal(sent[0].p,'/api/matching/releases');
  assert.deepEqual(JSON.parse(JSON.stringify(sent[0].o.body)),{grant_id:'g1',scopes:['education']});
});
