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
