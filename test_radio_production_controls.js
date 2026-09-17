const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('modules/fan_radio/templates/index.html','utf8');
const functionText=(start,end)=>html.slice(html.indexOf(start),html.indexOf(end,html.indexOf(start)));
const context=vm.createContext({Number,Math});
vm.runInContext(functionText('  function timingBadge(', '  function tagControls('),context);
for(const [seconds,color] of [[30,'ok'],[29,'ok'],[28,'warn'],[26,'warn'],[25.9,'bad'],[30.01,'bad']])
  assert.match(context.timingBadge(seconds,30,true),new RegExp('timing '+color),'duration '+seconds);
assert.match(context.timingBadge(null,30,true),/unavailable/);
assert.doesNotMatch(context.timingBadge(27,30,false),/timing (ok|warn|bad)/);
const gains=[],lengths=[],rates=[];
class AudioContext {
 // The render's own length and the rate each source plays at, both captured:
 // an over-long read is got back inside its slot by playing it faster, and
 // the only proof that worked is a shorter buffer and a rate on the voice.
 constructor(ch,len){this.destination={};if(len>1024) lengths.push(len);}
 createBufferSource(){const src={connect(){},playbackRate:{value:1},
   start(){rates.push(src.playbackRate.value);}};return src;}
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

 // An uploaded read that overruns, and the rate that gets it back. The mix
 // renders at the longer of the slot and the read, so a 33s read on a :30 is
 // a 33s file — and played at 1.12x it is 29.5s, which the slot's own floor
 // rounds back up to exactly :30 with the bed filling the tail.
 Object.assign(context,{decodeRef:async(sid,role)=>({duration:role==='vo'?33:40})});
 lengths.length=0; rates.length=0;
 const overlong=await context.buildMix({id:'sample',seconds:30,bed:{audio_url:'bed'}});
 assert.equal(lengths.at(-1),Math.round(33*44100),'an over-long read renders over its slot');
 assert.equal(rates[0],1,'and at its own pace, because nothing was approved');
 assert.equal(overlong.speed,1);
 assert.equal(overlong.voSeconds,33,'the read its own length is reported back for the suggestion');
 lengths.length=0; rates.length=0;
 const fitted=await context.buildMix({id:'sample',seconds:30,bed:{audio_url:'bed'}},1.12);
 assert.equal(lengths.at(-1),Math.round(30*44100),'played faster, the mix lands on the slot');
 assert.equal(rates[0],1.12,'the approved rate reaches the voice source itself');
 assert.equal(rates[1],1,'and not the bed, which is composed at length already');
 assert.equal(fitted.speed,1.12,'the render reports the rate it was made at');
 lengths.length=0; rates.length=0;
 await context.buildMix({id:'sample',seconds:30,bed:{audio_url:'bed'}},0.8);
 assert.equal(rates[0],1,'a rate below 1 is ignored — a mix is never short of its slot');
 Object.assign(context,{decodeRef:async(sid,role)=>({duration:role==='vo'?28:33})});

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

 // The same over-long uploaded read, in the other builder, answered by the
 // same shared rule. A 33s read on a :30 renders 33s; at 1.12x it is 29.5s,
 // which the slot's own floor rounds back up to exactly :30.
 Object.assign(context,{decodeRef:async ref=>({duration:ref.startsWith('vo:')?33:40})});
 lengths.length=0; rates.length=0;
 const promoLong=await context.buildMix('thirty');
 assert.equal(lengths.at(-1),Math.round(33*44100),'an over-long read renders over its slot here too');
 assert.equal(rates[0],1,'and at its own pace, because nothing was approved');
 assert.equal(promoLong.voSeconds,33,'the read its own length is reported back for the suggestion');
 lengths.length=0; rates.length=0;
 const promoFitted=await context.buildMix('thirty',1.12);
 assert.equal(lengths.at(-1),Math.round(30*44100),'played faster, the mix lands on the slot');
 assert.equal(rates[0],1.12,'the approved rate reaches the voice source itself');
 assert.equal(rates[1],1,'and not the bed');
 assert.equal(promoFitted.speed,1.12,'the render reports the rate it was made at');
 lengths.length=0; rates.length=0;
 await context.buildMix('thirty',0.8);
 assert.equal(rates[0],1,'a rate below 1 is ignored — a mix is never short of its slot');
 Object.assign(context,{decodeRef:async ref=>({duration:ref.startsWith('vo:')?28:33})});
 const share=fs.readFileSync('modules/radio_promo/templates/share.html','utf8');
 context.AUDIO_BASE='/tools/radio-promo/';
 vm.runInContext(share.slice(share.indexOf('  function audioSrc('),share.indexOf('  function render(')),context);
 assert.equal(context.audioSrc('/tools/radio-promo/file/sample.mp3'),'/tools/radio-promo/file/sample.mp3');
 assert.equal(context.audioSrc('file/sample.mp3'),'/tools/radio-promo/file/sample.mp3');
 Object.assign(context,{mixRevision:0,RENDERED:{},slotName:()=>":30",say(){},P:{id:'test',spots:[]},
   buildMix:async()=>{context.mixRevision++;return {buffer:{},note:''};}});
 // From APPROVED rather than makeMix: the approved-rate bookkeeping sits just
 // above makeMix and the function does not run without it.
 vm.runInContext(promo.slice(promo.indexOf('const APPROVED = {}'),promo.indexOf('async function fileMix(')),context);
 await context.makeMix('thirty');
 assert.equal(Object.keys(context.RENDERED).length,0,'an in-flight mix is discarded when its inputs change');
 Object.assign(context,{P:{id:'test'},QCC:{},FormData:class {append(){}},URL:{revokeObjectURL(){}},
   post:async()=>({project:{id:'test'},qc:{status:'pass'},mix:{seconds:30}}),renderMixPanel(){}});
 context.RENDERED.thirty={blob:{},url:'blob:sample',level:'Soft'};
 vm.runInContext(promo.slice(promo.indexOf('async function fileMix('),promo.indexOf('/* ---------- QC')),context);
 assert.equal(await context.fileMix('thirty',false),true);
 assert.equal(context.QCC.thirty.status,'pass','filing refreshes the QC verdict');
 assert.equal(context.RENDERED.thirty,undefined,'filing retires the temporary preview');
 const elements={};const element=id=>elements[id]||(elements[id]={value:'',textContent:'',innerHTML:'',replaceChildren(){},append(){}});
 let calls=[];
 Object.assign(context,{$:element,esc:s=>s,volumeSave:Promise.resolve(),
   document:{createElement:()=>({})},slotsOf:()=>['thirty','fifteen'],
   P:{id:'test',spots:[{slot:'thirty',audio_url:'/voice'}],beds:{thirty:{audio_url:'/bed'}},mixes:{}},
   bedOf:s=>context.P.beds[s],mixOf:s=>context.P.mixes[s],discardMixes(){context.RENDERED={};},
   makeMix:async s=>{calls.push('render:'+s);context.RENDERED[s]={};return true;},
   fileMix:async s=>{calls.push('save:'+s);context.P.mixes[s]={audio_url:'/mix',level:'Soft'};return true;},
   api:async(path,opts)=>{if(!opts)return {project:context.P};calls.push('share');assert.equal(JSON.parse(opts.body).require_mixes,true);return {share:{},share_url:'/review'};}});
 vm.runInContext(controls.slice(controls.indexOf('function renderCustomerAudio(')),context);
 await context.createReviewLink();
 assert.deepEqual(calls,['render:thirty','save:thirty','share'],'the combined file is saved before sharing');
 assert.match(element('customerAudioBox').innerHTML,/With music/);
 assert.match(element('customerAudioBox').innerHTML,/src="\/mix"/);
 assert.match(element('customerAudioBox').innerHTML,/src="\/voice"/);
 calls=[];await context.createReviewLink();assert.deepEqual(calls,['share'],'a current saved mix is reused');
 context.P.mixes={};context.fileMix=async()=>false;calls=[];
 await context.createReviewLink();assert.deepEqual(calls,['render:thirty'],'a failed mix save never publishes a link');
 assert.match(element('shareResult').textContent,/could not be saved/);
 assert.equal(element('createReviewLink').disabled,false,'a failed save permits retry');
 console.log('Timing, mix gains and customer music publishing passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
