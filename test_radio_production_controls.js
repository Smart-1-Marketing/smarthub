const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

/* The dead air cutter, driven for real. It is the one piece of this feature
   with arithmetic nobody can eyeball on a panel -- a gap finder and a splice
   -- so it is exercised against synthetic audio whose gaps are known exactly,
   and the seconds it claims to have saved are checked against the ones it
   should have. */
(function deadAirCutter(){
  const OAC = function(){};
  OAC.prototype.createBuffer = function(ch, len, rate){
    const d = []; for(let i=0;i<ch;i++) d.push(new Float32Array(len));
    return {numberOfChannels:ch, length:len, sampleRate:rate, duration:len/rate,
            getChannelData:i=>d[i||0]};
  };
  globalThis.OfflineAudioContext = OAC;
  const T = require('./hub/static/audio-trim.js').AudioTrim;
  const rate = 1000, n = 10*rate, d = new Float32Array(n);
  // speech 0-2s, 1s gap, speech 3-5s, 2s gap, speech 7-10s
  for(let i=0;i<n;i++){ const t=i/rate; d[i] = ((t<2)||(t>=3&&t<5)||(t>=7)) ? 0.5 : 0; }
  const buf = {numberOfChannels:1, length:n, sampleRate:rate, duration:10,
               getChannelData:()=>d};
  const opts = {threshold_db:-45, min_gap_ms:350, keep_ms:180, head_ms:120, tail_ms:200};

  const gaps = T.findGaps(buf, opts);
  assert.equal(gaps.length, 2, 'both gaps are found');
  assert.deepEqual(gaps.map(g=>[g.start/rate, g.end/rate]), [[2,3],[5,7]],
                   'and they are where they actually are');
  assert.ok(!gaps[0].head && !gaps[0].tail, 'a gap between speech is neither head nor tail');

  const cut = T.trim(buf, opts);
  // Each gap is shortened TO keep_ms rather than removed: 1s->0.18 and 2s->0.18.
  assert.equal(cut.closed, 2, 'both gaps are closed');
  assert.equal(cut.saved, Math.round(((1-0.18)+(2-0.18))*100)/100,
               'and the seconds it says it saved are the ones it saved');
  assert.equal(Math.round(cut.buffer.duration*100)/100, 7.36, 'the read comes back shorter');
  assert.ok(cut.changed, 'and it says it changed something');

  // A gap shorter than min_gap_ms is the rhythm of the read, not dead air.
  const tight = new Float32Array(n);
  for(let i=0;i<n;i++){ const t=i/rate; tight[i] = (t>=2 && t<2.2) ? 0 : 0.5; }
  const rhythm = T.trim({numberOfChannels:1,length:n,sampleRate:rate,duration:10,
                         getChannelData:()=>tight}, opts);
  assert.equal(rhythm.changed, false, 'a 200ms pause is left alone');
  assert.equal(rhythm.buffer.duration, 10, 'and the read is handed back untouched');

  // Leading silence is trimmed harder than a gap, and is the usual big win.
  const lead = new Float32Array(n);
  for(let i=0;i<n;i++) lead[i] = (i/rate) < 3 ? 0 : 0.5;
  const headCut = T.trim({numberOfChannels:1,length:n,sampleRate:rate,duration:10,
                          getChannelData:()=>lead}, opts);
  assert.equal(Math.round(headCut.buffer.duration*100)/100, 7.12,
               'three seconds of lead-in becomes the 120ms allowance');

  // Defaults stand in for anything a caller leaves out. NaN would compare
  // false against every sample and silently find no gaps at all.
  assert.equal(T.trim(buf, {}).closed, 2, 'the house settings apply when none are sent');
  assert.equal(T.trim(buf, {min_gap_ms:'wide'}).closed, 2, 'and when one is nonsense');
  delete globalThis.OfflineAudioContext;
})();

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

 // Fan Radio's mix IS the slot now. Step 5 fits the read -- measured on the
 // way in, dead air cut, a rate offered if that was not enough -- so by the
 // time a bed exists the length question has been asked and answered against
 // the thing that can actually be changed. A :30 renders 30.00s whatever the
 // read runs, and nothing in the mix speeds anything up: the music must not be
 // sped, and there is no rate left to apply to the voice.
 Object.assign(context,{decodeRef:async(sid,role)=>({duration:role==='vo'?33:40})});
 lengths.length=0; rates.length=0;
 const overlong=await context.buildMix({id:'sample',seconds:30,bed:{audio_url:'bed'}});
 assert.equal(lengths.at(-1),Math.round(30*44100),'the mix is the slot, not the read');
 assert.equal(rates[0],1,'and nothing is played faster in the mix');
 assert.equal(overlong.voSeconds,33,"the read's own length still rides back");
 assert.match(overlong.note,/cuts the last/,'an over-long read is SAID, not silently clipped');
 assert.match(overlong.note,/Step 5/,'and it names where to fix it');
 lengths.length=0;
 const fits=await context.buildMix({id:'sample',seconds:30,bed:{audio_url:'bed'}});
 assert.equal(lengths.at(-1),Math.round(30*44100),'a read that fits renders the same slot');
 Object.assign(context,{decodeRef:async(sid,role)=>({duration:role==='vo'?28:33})});
 const shortRead=await context.buildMix({id:'sample',seconds:30,bed:{audio_url:'bed'}});
 assert.doesNotMatch(shortRead.note||'',/cuts the last/,'a read inside its slot is not warned about');

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
