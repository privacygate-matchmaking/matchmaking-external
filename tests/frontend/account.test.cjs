const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(status) {
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.hidden = false; this.isConnected = true; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
  }
  const nodes = Object.fromEntries(['#dashboard .overview','#dashboard .content-layout','#member-status-panel','#account']
    .map(name => [name, new Element('div')]));
  const context = {window:{}, document:{createElement:tag => new Element(tag), querySelector:selector => nodes[selector]}, confirm:() => true};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../external-web/public/account.js'),'utf8'),context);
  const calls = [];
  const ctx = {root:new Element('div'), toast:() => {}, reload:async () => {}, isCurrent:() => true,
    api:async (url,options) => { calls.push({url,options});return {json:async () => url==='/api/me/status' ? status : {logout_url:'/logged-out'}}; }};
  return {ui:context.window.AccountUI,nodes,ctx,calls};
}

test('staff withdrawal hides member services and never offers cancellation',async () => {
  const {ui,nodes,ctx} = setup({status:'withdraw_pending',by:'staff',can_cancel:true,message:'직원 처리 안내'});
  assert.equal(await ui.check(ctx),false);
  assert.equal(nodes['#dashboard .content-layout'].hidden,true);
  assert.equal(nodes['#member-status-panel'].children[1].textContent,'직원 처리 안내');
  assert.equal(nodes['#member-status-panel'].children.some(n => n.textContent==='탈퇴 신청 철회'),false);
});

test('self withdrawal offers cancellation without exposing member services',async () => {
  const {ui,nodes,ctx,calls} = setup({status:'withdraw_pending',by:'self',can_cancel:true,message:'본인 신청 안내'});
  await ui.check(ctx);
  const cancel = nodes['#member-status-panel'].children.find(n => n.textContent==='탈퇴 신청 철회');
  assert.ok(cancel);
  await cancel.onclick();
  assert.equal(calls.at(-1).url,'/api/me/withdrawal/cancel');
  assert.equal(calls.at(-1).options.method,'POST');
});

test('withdrawn notice remains visible after one local logout',async () => {
  const {ui,nodes,ctx,calls} = setup({status:'withdrawn',message:'<script>private notice</script>'});
  await ui.check(ctx);
  await ui.check(ctx);
  assert.equal(calls.filter(x => x.url==='/auth/logout').length,1);
  assert.equal(nodes['#member-status-panel'].children[1].textContent,'<script>private notice</script>');
  assert.equal(nodes['#member-status-panel'].children.at(-1).href,'/logged-out');
  assert.equal(nodes['#account'].hidden,true);
});

test('a stale status response cannot replace the current screen',async () => {
  const {ui,nodes,ctx} = setup({status:'withdrawn',message:'outdated'});
  ctx.isCurrent = () => false;
  assert.equal(await ui.check(ctx),false);
  assert.equal(nodes['#member-status-panel'].children.length,0);
});

test('active status permits the normal lounge without logging out',async () => {
  const {ui,nodes,ctx,calls} = setup({status:'active'});
  assert.equal(await ui.check(ctx),true);
  assert.equal(nodes['#dashboard .content-layout'].hidden,false);
  assert.equal(nodes['#member-status-panel'].hidden,true);
  assert.equal(calls.length,1);
});
