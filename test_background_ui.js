const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('modules/bg_remover/templates/index.html', 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/\{\{ 'true' if (?:cloud|configured|ai_configured) else 'false' \}\}/g, 'true');

function setup(responses = []) {
  const elements = new Map(), requests = [];
  const get = id => {
    if (!elements.has(id)) elements.set(id, {value: '', textContent: '', innerHTML: '', style: {},
      classList: {add(){}, remove(){}}, addEventListener(){}, querySelectorAll(){return [];},
      scrollIntoView(){}, files: []});
    return elements.get(id);
  };
  get('mode').value = 'cutout';
  get('preResize').value = get('postResize').value = 'auto';
  class FormData { constructor(){this.fields = {}} append(k,v){this.fields[k] = v} }
  const context = vm.createContext({document: {getElementById: get}, window: {addEventListener(){}},
    FormData, setTimeout, clearTimeout,
    fetch: async (url, options) => {
      requests.push(options.body.fields);
      const next = responses.shift();
      if (next instanceof Error) throw next;
      return {json: async()=>next};
    }});
  vm.runInContext(source, context);
  return {get, context, requests};
}
const result = cached => ({name:'qa.png',original_name:'qa.png',bytes:100,original_bytes:200,cached,image:'data:image/png;base64,test'});
(async()=>{
  let s=setup(); await s.get('go').onclick(); assert.equal(s.requests.length,0);
  s=setup(); s.get('mode').value='replace'; s.get('imageUrl').value='https://saved.test/image.png';
  await s.get('go').onclick(); assert.equal(s.requests.length,0); assert.match(s.get('msg').textContent,/Describe/);
  s=setup([{results:[result(false)],provider_calls:1},{error:'<provider failed>'},{results:[result(true)],provider_calls:0}]);
  vm.runInContext("setPicked([{name:'a.png',type:'image/png'},{name:'b.png',type:'image/png'},{name:'c.png',type:'image/png'}])",s.context);
  await s.get('go').onclick();
  assert.equal(s.requests.length,3); assert.equal(s.requests[0].mode,'cutout');
  assert.match(s.get('resultMeta').textContent,/2 done · 1 processed by the service · 1 reused/);
  assert.match(s.get('errors').innerHTML,/&lt;provider failed&gt;/);
  assert.equal(s.get('go').disabled,false);
  s=setup([{results:[result(false)],provider_calls:1}]);
  s.get('mode').value='replace'; s.get('mode').onchange();
  assert.equal(s.get('promptRow').hidden,false);
  s.get('imageUrl').value=' https://saved.test/qa.png '; s.get('prompt').value=' A kitchen ';
  await s.get('go').onclick();
  assert.equal(s.requests[0].image_url,'https://saved.test/qa.png');
  assert.equal(s.requests[0].prompt,'A kitchen'); assert.equal(s.requests[0].mode,'replace');
  console.log('4 background UI scenarios passed');
})().catch(e=>{console.error(e);process.exitCode=1});
