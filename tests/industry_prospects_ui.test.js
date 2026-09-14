// Offline UI workflow test. Runs the actual browser script against a tiny DOM
// fixture; no browser, provider credentials or external requests are involved.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.disabled=false; this.value=''; this.textContent=''; this.checked=false; this.hidden=false; this.listeners={}; this.classList={toggle(){}}; }
  append(...els) { this.children.push(...els); }
  replaceChildren(...els) { this.children=els; }
  add(el) { this.append(el); }
  setAttribute() {}
  removeAttribute() {}
  scrollIntoView() {}
  addEventListener(name,fn) { this.listeners[name]=fn; }
  fire(name,event={}) { return (this.listeners[name] || this['on'+name])?.({target:this,currentTarget:this,preventDefault(){},...event}); }
}
const ids={};
const get=id => ids[id] ||= new Element();
const inputs={industry:'hvac',keywords:'HVAC',geography:'Ohio',name:'Test audience',employees_min:'5',employees_max:'100',revenue_min:'1000000',revenue_max:'30000000',titles:'Owner',landing_page:'https://example.com/hvac',owner:''};
get('ip-form').elements=Object.fromEntries(Object.entries(inputs).map(([key,value])=>[key,Object.assign(new Element('input'),{name:key,value})]));
const flatten = node => [node,...node.children.flatMap(flatten)];
const document={getElementById:get,createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text,children:[]}),querySelectorAll(selector){
  if(selector.startsWith('#ip-results')) return flatten(get('ip-results')).filter(el=>el.tag==='input'&&(!selector.includes(':not')||!el.disabled));
  return [...Object.values(ids),...Object.values(get('ip-form').elements)];
}};
let saved=false,bought=false,imported=false,fresh=true,paid=true;
const calls=[];
const campaign={id:'c1',name:'Test audience',landing_page:'https://example.com/hvac'};
const status=()=>({ok:true,missing:[],sync:{phase:'complete',read:100,saved:100,completed:1},fresh,paid_enabled:paid,auto_sync:false,campaigns:saved?[campaign]:[]});
const fetch=async(url,options={})=>{
  let data;
  if(url.endsWith('/status')) data=status();
  else if(url.includes('/history/')) data={ok:true,rows:bought?[{id:'p1',email:'test@example.com',status:imported?'imported':'ready'}]:[]};
  else {
    const body=JSON.parse(options.body);calls.push(body);
    assert.equal(options.headers['X-Prospect-Request'],'1');
    switch(body.action){
      case 'create': assert.equal(body.geography,'Ohio');assert.equal(body.industry,'hvac'); saved=true;data=campaign;break;
      case 'search':data={campaign,people:[{id:'p1',name:'Test Owner',company:'Test HVAC',title:'Owner',reason:''},{id:'p2',name:'Existing Owner',company:'Existing HVAC',title:'Owner',reason:'Existing email'}],page:1,total:2,eligible_on_page:1};break;
      case 'quote':assert.deepEqual(body.ids,['p1']);data={id:'plan1',ids:body.ids,max_email_credits:1,note:'GHL billed separately'};break;
      case 'approve':assert.equal(body.confirmed,true);data={id:'plan1',ids:['p1']};break;
      case 'purchase_queue':data={status:'queued'};break;
      case 'import':imported=true;data={status:'imported'};break;
      default:throw new Error('Unexpected action '+body.action);
    }
    data={ok:true,result:data};
  }
  return {ok:true,json:async()=>data};
};
const context={document,fetch,Option:function(text,value){return Object.assign(new Element('option'),{textContent:text,value});},FormData:class {constructor(form){this.form=form;}*[Symbol.iterator](){for(const [key,el]of Object.entries(this.form.elements)){if(!el.disabled)yield[key,el.value];}}},Set,Error,Date,Math,console,encodeURIComponent};
vm.runInNewContext(fs.readFileSync('hub/static/industry-prospects.js','utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await tick();
  get('ip-form').fire('submit');await tick();
  assert.equal(calls[0].action,'create');assert.equal(calls[1].action,'search');
  assert.equal(get('ip-results').children.length,2);
  const checks=document.querySelectorAll('#ip-results input');
  assert.equal(checks[1].disabled,true);
  checks[0].checked=true;checks[0].fire('change');
  get('ip-review').fire('click');await tick();
  assert.equal(get('ip-review-box').hidden,false);
  get('ip-buy').fire('click');await tick();
  assert.equal(bought,false,'Unchecked approval must not buy');
  get('ip-confirm').checked=true;
  get('ip-buy').fire('click');await tick();
  assert.equal(bought,false,'Queueing must not buy in the browser request');
  assert.equal(imported,false,'Queueing must not auto-import');
  assert.equal(calls.at(-1).action,'purchase_queue');
  bought=true; // Simulate the separately tested scheduler completing a contact.
  get('ip-refresh').fire('click');await tick();
  const importButton=flatten(get('ip-history')).find(el=>el.tag==='button');
  assert.ok(importButton);importButton.fire('click');await tick();
  assert.equal(imported,true);
  assert.equal(get('ip-message').textContent,'Contact confirmed in GHL.');
  fresh=false; paid=false;
  get('ip-refresh').fire('click');await tick();
  assert.equal(get('ip-create').disabled,true);
  assert.equal(get('ip-search').disabled,true);
  assert.equal(get('ip-buy').disabled,true);
  const before=calls.length;
  get('ip-campaign').value='c1';get('ip-open').fire('click');await tick();
  assert.equal(calls.length,before,'Opening history must not search or spend with stale suppression');
  assert.match(get('ip-message').textContent,/history loaded/);
  console.log('PASS: UI form capture, search, duplicate exclusion, purchase approval, verification handoff and explicit import.');
})().catch(error=>{console.error(error);process.exitCode=1;});
