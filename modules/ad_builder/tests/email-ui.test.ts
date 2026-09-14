import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vm from 'node:vm';
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function page(attempts:any[]=[]) {
  const html=fs.readFileSync(path.resolve(__dirname,'../../../hub/templates/ad_proof_send.html'),'utf8');
  const code=html.match(/<script>([\s\S]*?)<\/script>/)![1].replace('{{ project|tojson }}','"example"').replace('{{ review|tojson }}','"reviewA"');
  const nodes=new Map<string,any>();
  const node=(id:string)=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:['preview','history'].includes(id),disabled:false});return nodes.get(id);};
  const calls:any[]=[];
  const draft={id:'draftA',review_id:'reviewA',recipient:'client@example.test',sender:'team@example.test',subject:'Review',message:'Hello',proof_url:'https://example.test/client-proof/frozen'};
  vm.runInNewContext(code,{document:{getElementById:node},console,encodeURIComponent,fetch:async(url:string,options:any)=>{
    calls.push({url,options});
    const body=url.includes('/context?')?{project:{client:'Example Studio',name:'Offer',domain:'example.test'},contact:{linked:true,contact:{name:'Client',email:'client@example.test'}},attempts}:url.endsWith('/prepare')?draft:{...draft,status:'queued'};
    return {ok:true,json:async()=>body};
  }});
  await settle();return {nodes,node,calls};
}
test('email UI shows exact recipient and proof before the separate send action',async()=>{
  const {node,calls}=await page();assert.match(node('recipient').textContent,/client@example.test/);
  node('sender').value='team@example.test';node('subject').value='Review';node('message').value='Hello';
  await node('compose').onsubmit({preventDefault(){}});
  assert.equal(node('preview').hidden,false);assert.match(node('addresses').textContent,/team@example.test.*client@example.test/);
  assert.equal(node('proof').href,'https://example.test/client-proof/frozen');
  assert.ok(!calls.some(c=>c.url.endsWith('/send')));
  await node('send').onclick();assert.equal(calls.filter(c=>c.url.endsWith('/send')).length,1);
  assert.match(node('result').textContent,/accepted the email/);assert.doesNotMatch(node('result').textContent,/was delivered/);
});
test('reopening Send shows the existing attempt instead of a new composer',async()=>{
  const {node,calls}=await page([{review_id:'reviewA',status:'queued'}]);
  assert.equal(node('compose').hidden,true);assert.equal(node('history').hidden,false);
  assert.match(node('result').textContent,/accepted this proof email/);assert.equal(calls.length,1);
});
