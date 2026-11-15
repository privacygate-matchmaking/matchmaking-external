const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element {
  constructor(tag,text){this.tagName=tag;this.textContent=text;this.children=[];this.isConnected=true;this.hidden=true;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
}
const nodes=n=>[n,...n.children.flatMap(nodes)];
function setup(){
  const context={window:{},document:{createElement:t=>new Element(t)},encodeURIComponent};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../external-web/public/material.js'),'utf8'),context);
  return context;
}
const png='iVBORw0KGgo=';
function api(doc,type='application/json'){return async()=>({headers:{get:()=>type},json:async()=>doc});}
test('approved material shows reviewed text, safe images and contact only',async()=>{
  const c=setup(),panel=new Element('div');
  const doc={fields:[
    {scope:'contact',label:'연락처',value:'전화 010-1111-2222',reviewed_at:'2026-11-01T00:00:00Z'},
    {scope:'photo',label:'사진',value:'검토 완료',reviewed_at:'2026-11-01T00:00:00Z',
     attachments:[{media_type:'image/png',data_base64:png,label:'프로필 사진'},
                  {media_type:'text/html',data_base64:png,label:'위험'},
                  {media_type:'image/png',data_base64:'"><script>',label:'깨진 값'}]}]};
  await c.window.MaterialUI.show({delivery_id:'d1'},panel,{api:api(doc)});
  const all=nodes(panel);
  assert.ok(all.some(n=>n.textContent==='전화 010-1111-2222'));
  const images=all.filter(n=>n.tagName==='img');
  assert.equal(images.length,1);
  assert.ok(images[0].src.startsWith('data:image/png;base64,'));
});
test('non-JSON material is not rendered on screen',async()=>{
  const c=setup(),panel=new Element('div');
  await c.window.MaterialUI.show({delivery_id:'d1'},panel,{api:api({},'text/csv')});
  assert.ok(nodes(panel).some(n=>n.textContent?.includes('다운로드')));
});
