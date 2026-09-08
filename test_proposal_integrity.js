const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {allocate,capture}=require('./modules/sales_builder/static/proposal-integrity.js');
const items=[{product:'PPC',pctSeed:40},{product:'Meta',pctSeed:30},{product:'Retargeting',pctSeed:20},{product:'Display',pctSeed:10}];
for(const budget of [0,200,400,1000,2000,3000,4500,8000.03]){
 const amounts=allocate(items,budget,i=>i.product==='PPC'?400:500);
 amounts.forEach((n,i)=>assert.ok(n===0||n>=(i===0?400:500)));
 if(budget>=400)assert.equal(Math.round(amounts.reduce((a,b)=>a+b,0)*100),Math.round(budget*100));
}
const answers={primaryConversion:'old'};
capture({querySelectorAll:()=>[{dataset:{convField:'primaryConversion'},value:'Form Submissions'},{dataset:{convField:'creativeFee'},value:'535'}]},answers);
assert.deepEqual(answers,{primaryConversion:'Form Submissions',creativeFee:'535'});
const html=fs.readFileSync('./modules/sales_builder/templates/index.html','utf8');
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
function functionSource(name,next){return html.slice(html.indexOf('function '+name+'('),html.indexOf('function '+next+'('));}
const context={S:{items:[{product:'Retargeting',category:'RETARGETING',dollars:600,rate:'CPM',sellRate:9.5},{product:'Display',category:'DISPLAY',dollars:500,rate:'CPM',sellRate:11}],months:3,selPkg:1},ProposalIntegrity:{allocate},isConsultingLine:()=>false,consultingPrice:()=>300,minimumFor:()=>500,itemLabel:i=>i.product,itemDelivery:(i,d)=>({unit:'impr',val:Math.round(d/i.sellRate*1000)})};
vm.createContext(context);
vm.runInContext(functionSource('planRecurring','syncBudgetToPlan')+functionSource('buildPackages','recommendItems')+';buildPackages()',context);
const pkg=context.S.packages[1];
assert.equal(pkg.monthly,1100);
assert.equal(pkg.total,3300);
assert.deepEqual(Array.from(pkg.lines,l=>l.amt),[600,500]);
assert.equal(pkg.impr,Math.round(1800/9.5*1000)+Math.round(1500/11*1000));
assert.ok(html.includes('Est. impressions / campaign'));
console.log('PASS: allocations, exact totals, form capture, campaign delivery and all inline JavaScript syntax');

context.saveNow=async()=>true;context.drawPkgs=()=>{};context.toast=()=>{};
vm.runInContext(functionSource('choosePackage','conversionItems')+';choosePackage(0);buildPackages()',context);
assert.equal(context.S.items.length,1);
assert.equal(context.S.selectedPackage.monthly,context.S.packages[0].monthly);
vm.runInContext('choosePackage(2);buildPackages()',context);
assert.equal(context.S.items.length,2,'larger option restores channels removed by Essential');
assert.equal(context.S.selectedPackage.monthly,context.S.items.reduce((a,i)=>a+i.dollars,0));
context.CONV={quote:{data:{months:3,items:[{product:'Media',dollars:3000},{product:'Shoot',dollars:100,basis:'one_time'}]},investment:{lines:[{kind:'saas',label:'Suite',amount:199,recurs:'Monthly'},{kind:'setup',label:'Creative',amount:535,recurs:'One-time'},{kind:'setup',label:'Shoot',amount:100,recurs:'One-time',source:'plan'}]}}};
context.lineForIO=(i,m)=>({product:i.product,budget:i.basis==='one_time'?0:i.dollars,campaignBudget:i.dollars*(i.basis==='one_time'?1:m)});
vm.runInContext(functionSource('conversionItems','drawPkgs')+';var ioItems=conversionItems(true)',context);
assert.equal(context.ioItems.reduce((a,i)=>a+i.budget,0),3199);
assert.equal(context.ioItems.reduce((a,i)=>a+i.campaignBudget,0),10232,'IO carries licensing and creative, with plan production counted once');
console.log('PASS: package switching, render stability and complete IO investment');

assert.ok(!html.includes('No central CRM'),'default browser copy must not invent a CRM gap');

context.isConsultingLine=i=>i.product==='Strategy';
context.consultingPrice=n=>300+50*n;
context.S={items:[{product:'Display',category:'DISPLAY',dollars:1000,sellRate:10},{product:'PPC',category:'SEARCH',dollars:1000,sellRate:10},{product:'Strategy',category:'CUSTOM',dollars:400}],months:3};
vm.runInContext('buildPackages()',context);
for(const p of context.S.packages){
 const media=p.lines.filter(l=>!l.consulting);
 assert.equal(p.lines.find(l=>l.consulting).amt,300+50*media.length);
 assert.equal(p.monthly,p.lines.reduce((sum,l)=>sum+l.amt,0));
}
context.CONV.quote.data.consulting={include:true,hours:3};
context.CONV.quote.investment.lines.push({kind:'consulting',amount:450,recurs:'Monthly'});
context.retainerLineForIO=()=>({product:'Consulting & Strategy Retainer',retainer:true,description:'Monthly strategy scope'});
vm.runInContext('ioItems=conversionItems(true)',context);
assert.equal(context.ioItems.filter(i=>i.retainer).length,1);
assert.equal(context.ioItems.find(i=>i.retainer).campaignBudget,1350);
assert.equal(context.ioItems.find(i=>i.retainer).description,'Monthly strategy scope');
console.log('PASS: merged consulting formula and retainer scope');
