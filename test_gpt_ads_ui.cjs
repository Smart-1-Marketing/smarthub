const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('modules/gpt_ads/templates/index.html', 'utf8');
new vm.Script(html.slice(html.indexOf('<script>') + 8, html.lastIndexOf('</script>')));
const handlers = {}, nodes = {copyBlocks: {addEventListener: (name, fn) => handlers[name] = fn}};
let rows = [], rendered;
nodes.copyBlocks.querySelectorAll = () => rows.map(([kind, value]) => ({value, getAttribute: () => kind}));
const ctx = {state:{pack:{copy:{}}}, $:id => nodes[id], renderCopy(){rendered = JSON.parse(JSON.stringify(ctx.state.pack.copy));}, save(){throw Error('Row edit unexpectedly saves');}};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('function collectCopy('), html.indexOf('$("copyBlocks").addEventListener("input"')), ctx);
function click(attrs){handlers.click({target:{getAttribute:name => attrs[name] || null}});}
rows = [['headlines','Keep headline'],['bodies','Keep body'],['ctas','Learn More']];
click({'data-add':'bodies'});
assert.equal(rendered.headlines[0].text, 'Keep headline');
assert.equal(rendered.ctas[0].text, 'Learn More');
assert.equal(rendered.bodies.length, 2);
rows = [['headlines',''],['headlines','Keep second'],['bodies','Body edit']];
click({'data-drop':'headlines','data-i':'0'});
assert.equal(rendered.headlines[0].text, 'Keep second');
assert.equal(rendered.bodies[0].text, 'Body edit');
click({'data-cta':'Learn More'});
assert.equal(rendered.bodies[0].text, 'Body edit');
assert.equal(rendered.ctas[0].text, 'Learn More');
rows.push(['ctas','Learn More']);
click({'data-cta':'Learn More'});
assert.equal(rendered.ctas.length, 1);

(async () => {
  const controls = [{disabled:false},{disabled:true}];
  let resolveFetch, calls = 0;
  const net = {Promise,Array,AbortController,setTimeout,clearTimeout, document:{querySelectorAll:()=>controls},
    fetch(){calls++;return new Promise(resolve=>resolveFetch=resolve);}};
  vm.createContext(net);
  vm.runInContext(html.slice(html.indexOf('var requestBusy'),html.indexOf('function get(url)')),net);
  const pending = net.request('/test',{});
  assert(controls.every(c=>c.disabled));
  assert.equal((await net.request('/test',{})).ok,false);
  assert.equal(calls,1);
  resolveFetch({json:async()=>({ok:true})});
  assert((await pending).ok);
  assert.equal(controls[0].disabled,false);
  assert.equal(controls[1].disabled,true);
  net.fetch=async()=>{throw Error('offline');};
  assert.equal((await net.request('/test',{})).ok,false);
  assert.equal(controls[0].disabled,false);
  console.log('Passed: cross-group copy retention, blank-row removal, CTA selection/deduplication, duplicate request guard, failure recovery.');
})().catch(error=>{console.error(error);process.exitCode=1;});
