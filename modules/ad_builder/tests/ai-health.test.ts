import { test } from 'node:test';
import assert from 'node:assert/strict';
import { authenticatedAiCheck } from '../src/ai-health';
test('a reachable 401 fails authenticated AI diagnostics',async()=>{
  const old=process.env.OPENAI_API_KEY;process.env.OPENAI_API_KEY='test-only';
  try{const r=await authenticatedAiCheck(async()=>new Response('{}',{status:401}));assert.equal(r.level,'fail');assert.match(r.detail,/401/);}finally{if(old===undefined)delete process.env.OPENAI_API_KEY;else process.env.OPENAI_API_KEY=old;}
});
test('authenticated success is explicitly not proof of generation',async()=>{
  const old=process.env.OPENAI_API_KEY;process.env.OPENAI_API_KEY='test-only';
  try{const r=await authenticatedAiCheck(async()=>new Response('{}'));assert.equal(r.level,'ok');assert.match(r.detail,/generation separately/);}finally{if(old===undefined)delete process.env.OPENAI_API_KEY;else process.env.OPENAI_API_KEY=old;}
});
