// Exercise the real Fan Radio request and navigation functions with controlled responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('modules/fan_radio/templates/index.html', 'utf8');
const tracking = html.slice(html.indexOf('  var activeWrites'), html.indexOf('  // Keep draft fields'));
const navigation = html.slice(html.indexOf('  async function goStep('), html.indexOf("  $('steps').onclick"));
const multipart = html.slice(html.indexOf('  function postForm('), html.indexOf('  function spotById('));
let resolveResponse, rejectResponse, shown = [], messages = [], saves = 0;
const context = vm.createContext({
  BASE:'/tools/fan-radio', moving:false, stepIndex:3, stepCards:['voicecard'],
  canStep:()=>true, $:()=>({querySelector:()=>({disabled:true})}),
  saveDraft:async()=>{saves++;}, drawSpots:()=>{}, showStep:i=>shown.push(i),
  say:(id,text,kind)=>messages.push({text,kind}),
  fetch:()=>new Promise((resolve,reject)=>{resolveResponse=resolve;rejectResponse=reject;}),
  window:{dispatchEvent:()=>{}}, Event:class {},
});
vm.runInContext(tracking + navigation + multipart, context);
(async () => {
  await context.goStep(4);
  assert.deepEqual(shown,[4], 'a disabled customer-voice button must not block navigation');
  assert.equal(saves,1);
  const writing = context.api('/api/projects/demo/voice',{method:'POST',body:{voice_id:'saved'}});
  await context.goStep(5);
  assert.equal(shown.length,1,'navigation stays locked during a real write');
  assert.equal(messages.at(-1).kind,'warn','blocked navigation must not start a spinner');
  resolveResponse({ok:true,json:async()=>({ok:true})}); await writing;
  assert.equal(context.activeWrites,0);
  assert.equal(messages.at(-1).text,'Ready to continue.');
  await context.goStep(5); assert.deepEqual(shown,[4,5]);
  const failed = context.api('/api/projects/demo',{method:'POST',body:{}});
  rejectResponse(new Error('offline')); await assert.rejects(failed,/offline/);
  assert.equal(context.activeWrites,0,'failed writes release the navigation lock');
  const reading = context.api('/api/voices');
  await context.goStep(6); assert.deepEqual(shown,[4,5,6],'background reads do not lock the workflow');
  resolveResponse({ok:true,json:async()=>({ok:true})}); await reading;
  const upload = context.postForm('/api/projects/demo/audio',{});
  assert.equal(context.activeWrites,1);
  resolveResponse({ok:false,status:502,json:async()=>({ok:false,error:'Upload failed'})});
  await assert.rejects(upload,/Upload failed/); assert.equal(context.activeWrites,0);
  context.localMixes=1; await context.goStep(2); assert.equal(shown.length,3);
  context.localMixes=0; context.finishRadioWork(); await context.goStep(2); assert.equal(shown.at(-1),2);
  console.log('Fan Radio navigation: disabled controls, pending writes, failure recovery, reads, uploads and local mixes passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
