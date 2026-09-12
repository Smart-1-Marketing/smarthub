const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync('modules/io_builder/templates/index.html','utf8');
const functions=source.slice(source.indexOf('function mediaMixPayload(){'),source.indexOf('function creativeForProduct('));
const contexts=[];
function setup(){
 const status={textContent:''};
 const context={state:{client:'Demo',objectives:['Leads'],industry:'Retail',geo:'Columbus',items:[{product:'d',budget:400,start:'2026-10-01',end:'2026-10-31'},{product:'s',budget:600,start:'2026-10-01',end:'2026-10-31'}],audiences:[],creativeAssets:[],mediaMixRecommendation:{suggested_allocations:[{product:'Display',monthly_budget:450},{product:'Search',monthly_budget:550}]}},productConfig:{d:{product:'Display'},s:{product:'Search'}},campaignLength:()=>({days:31}),document:{getElementById:()=>status},recalculateItem:()=>{},calculateGuardrails:()=>{},render:()=>{},console};
 vm.createContext(context);vm.runInContext(functions,context);
 context.state.mediaMixSignature=vm.runInContext('JSON.stringify(mediaMixPayload())',context);
 return {context,status};
}
{
 const {context,status}=setup();vm.runInContext('acceptMediaMix()',context);
 assert.equal(context.state.items[0].budget,450);assert.equal(context.state.items[1].budget,550);assert.match(status.textContent,/applied/);
}
for(const change of [c=>c.state.items[0].budget=401,c=>c.state.mediaMixRecommendation.suggested_allocations[1].product='Unknown',c=>c.state.mediaMixRecommendation.suggested_allocations[1].monthly_budget=549,c=>c.state.mediaMixRecommendation.suggested_allocations[1].monthly_budget=NaN,c=>c.state.mediaMixRecommendation.suggested_allocations[1].product='Display',c=>c.state.mediaMixRecommendation.suggested_allocations={}]){
 const {context,status}=setup();change(context);const before=context.state.items.map(i=>i.budget);
 vm.runInContext('acceptMediaMix()',context);assert.deepEqual(context.state.items.map(i=>i.budget),before);assert.ok(status.textContent);
}
{
 const {context}=setup();context.state.items[0].budgetChanges=[{budget:200}];context.state.mediaMixSignature=vm.runInContext('JSON.stringify(mediaMixPayload())',context);
 assert.throws(()=>vm.runInContext('mediaMixUpdates()',context),/scheduled/);
}
// Every inline script must still parse after Jinja substitutions.
let count=0;
for(const match of source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)){
 const script=match[1].replaceAll('{{ (request.script_root or "")|tojson }}','""').replaceAll('{{ (own_api_paths or [])|tojson }}','[]').replace(/\{\{[^}]+\}\}/g,'');
 new vm.Script(script);count++;
}
{
 const {context,status}=setup();let calls=0;
 context.recalculateItem=()=>{if(++calls===2)throw new Error('Calculation failed')};
 vm.runInContext('acceptMediaMix()',context);
 assert.deepEqual(context.state.items.map(i=>i.budget),[400,600]);assert.match(status.textContent,/Calculation failed/);
}
console.log(`PASS: atomic acceptance, stale plan, unknown/duplicate products, invalid totals/numbers/shapes, scheduled budgets; ${count} inline scripts parse.`);

// Optional conversions can be empty; required product selection still blocks.
{
 const c={index:0,selectedCountFor:()=>0,addMsg:()=>{},render:()=>{},ask:()=>{},document:{querySelectorAll:()=>[]}};
 vm.createContext(c);
 vm.runInContext(source.slice(source.indexOf('function completeMultiQuestion('),source.indexOf('function finishRoster(')),c);
 c.completeMultiQuestion({key:'selected'});assert.equal(c.index,0);
 c.completeMultiQuestion({key:'secondaryConversions',allowEmpty:true});assert.equal(c.index,1);
}

async function testSubmissionReview(){
 let active, restored=0;
 const previous={isConnected:true,focus(){restored++}};
 function element(tag){return {tag,style:{},children:[],events:{},setAttribute(){},append(...children){this.children.push(...children)},addEventListener(name,fn){this.events[name]=fn},showModal(){active=this},close(){},remove(){if(active===this)active=null},focus(){}};}
 const doc={activeElement:previous,createElement:element,body:{appendChild(){}},getElementById:()=>({}),querySelector:()=>({style:{}})};
 const c={document:doc,Promise};vm.createContext(c);
 const review=source.slice(source.indexOf('let _ioConfirmActive='),source.indexOf('let _ioSubmitting='));
 vm.runInContext(review,c);
 let result=c.confirmIOReview('<img src=x onerror=alert(1)>');
 assert.equal(active.children[1].textContent,'<img src=x onerror=alert(1)>');
 assert.equal(active.children[1].innerHTML,undefined);
 assert.equal(await c.confirmIOReview('duplicate'),false);
 active.children[2].children[0].events.click();assert.equal(await result,false);assert.equal(restored,1);
 result=c.confirmIOReview('escape');active.events.cancel({preventDefault(){}});assert.equal(await result,false);
 result=c.confirmIOReview('accept');active.children[2].children[1].events.click();assert.equal(await result,true);
 assert.equal(active,null);

 // No PDF or submission calls before review approval. Guardrail overrides remain explicit.
 let pdfs=0,sends=0,cleared=0;
 Object.assign(c,{state:{items:[{product:'display',budget:700,campaignBudget:700}],guardrailWarnings:[],documents:{}},
  productConfig:{display:{product:'Display'}},submissionMissing:()=>[],calculateGuardrails:()=>{},money:String,
  ensurePdfForSuite:async kind=>{pdfs++;return 'https://example.com/'+kind+'.pdf'},
  fetch:async()=>{sends++;return {ok:true,json:async()=>({ok:true,delivered_to_suite:true})}},
  addMsg:()=>{},clearDraft:()=>{cleared++},console,window:{}});
 vm.runInContext(source.slice(source.indexOf('let _ioSubmitting='),source.indexOf('</script>',source.indexOf('let _ioSubmitting='))),c);
 let submit=c.submitCompletedIO();assert.equal(pdfs,0);
 await c.submitCompletedIO();assert.equal(pdfs,0);
 active.children[2].children[0].events.click();await submit;assert.equal(sends,0);
 c.state.guardrailWarnings=[{message:'Management fee is marked NONE'}];
 submit=c.submitCompletedIO();active.children[2].children[1].events.click();await new Promise(setImmediate);
 assert.equal(pdfs,0);assert.match(active.children[1].textContent,/Management fee/);
 active.children[2].children[0].events.click();await submit;assert.equal(sends,0);
 c.state.guardrailWarnings=[];
 submit=c.submitCompletedIO();active.children[2].children[1].events.click();await submit;
 assert.equal(pdfs,2);assert.equal(sends,1);assert.equal(cleared,1);
 assert.equal(c.state.submission.deliveredToSuite,true);
}

// Rich help never trusts HTML supplied in the help registry.
{
 const help=fs.readFileSync('hub/static/hub-help.js','utf8');
 const c={};vm.createContext(c);
 vm.runInContext(help.slice(help.indexOf('  function esc('),help.indexOf('  function bubble(')),c);
 assert.equal(c.helpBody('Use **bold**\n<script>bad()</script>'),'Use <b>bold</b><br>&lt;script&gt;bad()&lt;/script&gt;');
}
testSubmissionReview().then(()=>console.log('PASS: optional conversions, review cancel/Escape/duplicate/approval, guardrail override, PDF-before-submit, escaped help emphasis.')).catch(e=>{console.error(e);process.exitCode=1});
