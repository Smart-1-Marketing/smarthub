const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('modules/io_builder/templates/index.html','utf8');
function setup(){
 const storage=new Map(),status={},calls=[];let resolveSave;
 const c={state:{client:'Fixture'},index:3,currentProduct:0,tempItem:null,Date,Set,JSON,encodeURIComponent,setTimeout:()=>1,clearTimeout(){},document:{getElementById:()=>status},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},fetch:(url,opts)=>{calls.push({url,opts});if(opts.method==='DELETE')return Promise.resolve({});return new Promise(resolve=>{resolveSave=resolve});}};
 vm.createContext(c);
 vm.runInContext(source.slice(source.indexOf('const DRAFT_KEY='),source.indexOf('/* A tab being closed')),c);
 return {c,status,storage,calls,resolve:(body)=>resolveSave({json:async()=>body})};
}
const flush=()=>new Promise(setImmediate);
async function run(){
 let f=setup();f.c.saveDraft();assert.match(f.status.textContent,/Saved in this browser at/);assert.equal(f.c.loadDraft().state.client,'Fixture');
 f.c.localStorage.setItem=()=>{throw Error('full')};f.c.saveDraft();assert.match(f.status.textContent,/Browser save failed/);
 f=setup();f.c.saveDraft();f.c.saveDraftServer();f.resolve({ok:false});await flush();assert.match(f.status.textContent,/account save failed/);
 f=setup();f.c.saveDraftServer();f.c.clearDraft();f.resolve({ok:true,id:'late-save'});await flush();assert.ok(f.calls.some(x=>x.opts.method==='DELETE'&&x.url.endsWith('late-save')));assert.equal(f.c.loadDraft(),null);
 f=setup();f.c.saveDraftServer();f.c.forgetLocalDraft();f.resolve({ok:true,id:'older-order'});await flush();assert.ok(!f.calls.some(x=>x.opts.method==='DELETE'));assert.equal(f.c.loadDraft(),null);
 const box={};const c={state:{client:'<img>',items:[],selected:[],salesContact:'',guardrailWarnings:[{type:'management_fee_missing',message:'Fee <missing>'}]},submissionMissing:()=>['client','valid product dates'],document:{getElementById:()=>box}};
 vm.createContext(c);
 vm.runInContext(source.slice(source.indexOf('function esc('),source.indexOf('function editorEmailError(')),c);
 vm.runInContext(source.slice(source.indexOf('function renderPreflight('),source.indexOf('function render(){')),c);
 c.renderPreflight();assert.match(box.innerHTML,/e-client/);assert.match(box.innerHTML,/e-products/);assert.match(box.innerHTML,/e-management-fee/);assert.match(box.innerHTML,/Fee &lt;missing&gt;/);
 console.log('PASS: saved timestamps, browser/account save failures, late draft responses, preserved older drafts, escaped actionable checklist.');
}
run().catch(e=>{console.error(e);process.exitCode=1});
