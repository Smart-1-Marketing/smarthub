const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('modules/fan_radio/templates/index.html','utf8');
const functionText=(start,end)=>html.slice(html.indexOf(start),html.indexOf(end,html.indexOf(start)));
const context=vm.createContext({Number,Math});
vm.runInContext(functionText('  function timingBadge(', '  function tagControls('),context);
for(const [seconds,color] of [[30,'ok'],[29,'ok'],[28,'warn'],[26,'warn'],[25.9,'bad'],[30.01,'bad']])
  assert.match(context.timingBadge(seconds,30,true),new RegExp('timing '+color),'duration '+seconds);
assert.match(context.timingBadge(null,30,true),/unavailable/);
assert.doesNotMatch(context.timingBadge(27,30,false),/timing (ok|warn|bad)/);
const gains=[];
class AudioContext {
 constructor(){this.destination={};}
 createBufferSource(){return {connect(){},start(){}};}
 createGain(){return {connect(){},gain:{setValueAtTime(v){gains.push(v);},linearRampToValueAtTime(v){gains.push(v);}}};}
 startRendering(){return Promise.resolve({duration:30});}
}
Object.assign(context,{window:{OfflineAudioContext:AudioContext},MIXCFG:{mix:{bed_db:-10,ducked_db:-15}},
 selectedMixLevel:()=>({label:'Soft',bed_db:-25,ducked_db:-32}),decodeRef:async(sid,role)=>({duration:role==='vo'?28:33})});
vm.runInContext('function dbGain(db){return Math.pow(10,db/20);}'+functionText('  function buildMix(', '  /* Peak,'),context);
(async()=>{
 await context.buildMix({id:'sample',seconds:30,bed:{audio_url:'bed'}});
 assert.ok(gains.includes(Math.pow(10,-25/20)),'selected bed gain is used');
 assert.ok(gains.includes(Math.pow(10,-32/20)),'selected under-voice gain is used');
 assert.ok(!gains.includes(Math.pow(10,-10/20)),'old default gain is not used');
 const promo=fs.readFileSync('modules/radio_promo/templates/index.html','utf8');
 const controls=fs.readFileSync('hub/static/radio-promo-production.js','utf8');
 vm.runInContext(controls.slice(controls.indexOf('function timingBadge('),controls.indexOf('function selectedMixLevel(')),context);
 for(const [seconds,color] of [[30,'ok'],[29,'ok'],[28,'warn'],[26,'warn'],[25.9,'bad'],[30.01,'bad']])
   assert.match(context.timingBadge(seconds,30,true),new RegExp('timing '+color));
 assert.doesNotMatch(context.timingBadge(27,30,false),/timing (ok|warn|bad)/);
 gains.length=0;
 Object.assign(context,{bedOf:()=>({audio_url:'bed'}),slotSeconds:()=>30,decodeRef:async ref=>({duration:ref.startsWith('vo:')?28:33})});
 vm.runInContext(promo.slice(promo.indexOf('async function buildMix('),promo.indexOf('/* Peak,')),context);
 await context.buildMix('thirty');
 assert.ok(gains.includes(Math.pow(10,-25/20)),'Radio Ad Creator uses selected bed gain');
 assert.ok(gains.includes(Math.pow(10,-32/20)),'Radio Ad Creator uses selected ducked gain');
 const share=fs.readFileSync('modules/radio_promo/templates/share.html','utf8');
 context.AUDIO_BASE='/tools/radio-promo/';
 vm.runInContext(share.slice(share.indexOf('  function audioSrc('),share.indexOf('  function render(')),context);
 assert.equal(context.audioSrc('/tools/radio-promo/file/sample.mp3'),'/tools/radio-promo/file/sample.mp3');
 assert.equal(context.audioSrc('file/sample.mp3'),'/tools/radio-promo/file/sample.mp3');
 Object.assign(context,{mixRevision:0,RENDERED:{},slotName:()=>":30",say(){},buildMix:async()=>{context.mixRevision++;return {buffer:{},note:''};}});
 vm.runInContext(promo.slice(promo.indexOf('async function makeMix('),promo.indexOf('async function fileMix(')),context);
 await context.makeMix('thirty');
 assert.equal(Object.keys(context.RENDERED).length,0,'an in-flight mix is discarded when its inputs change');
 Object.assign(context,{P:{id:'test'},QCC:{},FormData:class {append(){}},URL:{revokeObjectURL(){}},
   post:async()=>({project:{id:'test'},qc:{status:'pass'},mix:{seconds:30}}),renderMixPanel(){}});
 context.RENDERED.thirty={blob:{},url:'blob:sample',level:'Soft'};
 vm.runInContext(promo.slice(promo.indexOf('async function fileMix('),promo.indexOf('/* ---------- QC')),context);
 await context.fileMix('thirty',false);
 assert.equal(context.QCC.thirty.status,'pass','filing refreshes the QC verdict');
 assert.equal(context.RENDERED.thirty,undefined,'filing retires the temporary preview');
 console.log('Timing thresholds and selected mix gains passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
