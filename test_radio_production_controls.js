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
 console.log('Timing thresholds and selected mix gains passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
