// Runs the real wizard calculation/editor/PDF/submission code against the
// Flask test server from test_io_completion.py. No customer services are used.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('modules/io_builder/templates/index.html','utf8');
const fields={};
const get=id=>fields[id]||(fields[id]={value:'',style:{},classList:{add(){},remove(){}},setAttribute(){},removeAttribute(){},focus(){}});
const c={console,URL,crypto:require('node:crypto').webcrypto,document:{getElementById:get,addEventListener(){},querySelector:()=>get('submit'),querySelectorAll:selector=>selector==='#e-products input'?[{dataset:{row:'0',field:'budget'},value:'900'}]:[]},window:{},fetch:(url,options)=>fetch(process.env.IO_TEST_URL+url.replace('{{ request.script_root }}',''),options)};
vm.createContext(c);
vm.runInContext(source.slice(source.indexOf('const objectiveKPIs='),source.indexOf('const rateCard=[')),c);
vm.runInContext(source.slice(source.indexOf('const rateCard=['),source.indexOf('async function generateStoredPDF')),c);
vm.runInContext(source.slice(source.indexOf('async function ensurePdfForSuite('),source.indexOf('// A page dialog keeps')),c);
vm.runInContext(source.slice(source.indexOf('let _ioSubmitting='),source.indexOf('</script>',source.indexOf('let _ioSubmitting='))),c);
vm.runInContext(`
var _ioConfirmActive=false;
var saved=null;
function saveDraft(){saved=JSON.stringify(state);}
function scheduleServerSave(){}
function clearDraft(){saved=null;}
function render(){}
function addMsg(){}
async function confirmIOReview(){return true;}
const selected=Object.keys(productConfig).find(p=>/RON/.test(p));
if(!selected)throw new Error('Test product missing from real rate card');
Object.assign(state,{client:'IO completion fixture',orderNumber:'QA-COMPLETION',salesContact:'Test rep',salesEmail:'rep@example.com',clientContactName:'Test only',clientContactEmail:'fixture@example.com',managementFee:'INCLUDED',creativeFee:'INCLUDED',sameDates:true,start:'2026-10-01',end:'2026-10-31',selected:[selected],items:[{product:selected,budget:700,rate:3.5,start:'2026-10-01',end:'2026-10-31'}]});
openEditor();saveEditor();
this.testState=state;
`,c);
async function run(){
 assert.equal(c.testState.salesEmail,'rep@example.com');assert.equal(c.testState.items[0].budget,900);
 // The server fails the first internal PDF upload. The client PDF survives.
 await c.submitCompletedIO();assert.ok(c.testState.documents.client_pdf_url);assert.ok(!c.testState.submission);
 const clientUrl=c.testState.documents.client_pdf_url;
 // Recover from a refresh using the actual serialized draft state.
 vm.runInContext('Object.assign(state,JSON.parse(saved))',c);
 await c.submitCompletedIO();assert.equal(c.testState.documents.client_pdf_url,clientUrl);assert.equal(c.testState.submission.deliveredToSuite,true);
 // Simulate loss of the HTTP receipt: the same request must replay safely.
 const payload=c.deliveryRequest(clientUrl,c.testState.documents.internal_pdf_url);
 const replay=await c.fetch('/api/submit-io',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 assert.equal((await replay.json()).delivered_to_suite,true);
 // A genuine edit must create new PDFs and update, not duplicate, the order.
 c.testState.items[0].budget=1000;c.recalculateItem(c.testState.items[0]);
 await c.submitCompletedIO();assert.equal(c.testState.submission.deliveredToSuite,true);assert.notEqual(c.testState.documents.client_pdf_url,clientUrl);
 const stats=await (await c.fetch('/_test/results')).json();
 assert.equal(stats.uploads,5);assert.equal(stats.pushes,2);assert.deepEqual(stats.opportunity_ids,['','fixture-opportunity']);assert.equal(stats.budget,1000);assert.equal(stats.orders,1);
 const originalFetch=c.fetch;let editDuringUpload=true;
 c.fetch=async(url,options)=>{const r=await originalFetch(url,options);if(editDuringUpload&&url.includes('generate-client-pdf')){editDuringUpload=false;c.testState.items[0].budget=1200;c.recalculateItem(c.testState.items[0]);}return r;};
 c.testState.items[0].budget=1100;c.recalculateItem(c.testState.items[0]);
 await c.submitCompletedIO();assert.match(get('submitStatus').textContent,/changed while the PDFs/);
 assert.equal((await (await c.fetch('/_test/results')).json()).pushes,2);
 c.fetch=async(url,options)=>{const r=await originalFetch(url,options);if(url.includes('/api/submit-io')){c.testState.items[0].budget=1300;c.recalculateItem(c.testState.items[0]);}return r;};
 await c.submitCompletedIO();assert.equal((await (await c.fetch('/_test/results')).json()).pushes,3);
 assert.equal(JSON.parse(c.saved).items[0].budget,1300);assert.equal(JSON.parse(c.testState.submission.contentSignature).items[0].budget,1200);
 assert.match(get('submitStatus').textContent,/Newer edits are saved/);
 console.log('PASS: real editor, interrupted PDF generation, restored draft, both real PDFs, delivery receipt replay, changed-answer regeneration, one order/opportunity.');
}
run().catch(e=>{console.error(e);process.exitCode=1});
