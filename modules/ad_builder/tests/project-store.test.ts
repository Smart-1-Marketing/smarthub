import {test,mock} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import {ProjectStore} from '../src/projects';
test('a failed replacement preserves the previous complete campaign record',t=>{
  const out=fs.mkdtempSync(path.join(os.tmpdir(),'ad-atomic-'));t.after(()=>fs.rmSync(out,{recursive:true,force:true}));
  const store=new ProjectStore(out),project=store.create({projectName:'Saved campaign',client:'Example Studio',domain:'example.test',campaignName:'Offer',requestId:'atomic-test'});
  const native=require('node:fs'),rename=native.renameSync;
  const fail=mock.method(native,'renameSync',(from:string,to:string)=>{
    if(to.endsWith(project.projectId+'.json'))throw new Error('Simulated interrupted replacement');
    return rename(from,to);
  });
  try { assert.throws(()=>store.save({...project,projectName:'Unfinished write'}),/interrupted replacement/); }
  finally { fail.mock.restore(); }
  assert.equal(new ProjectStore(out).get(project.projectId)!.projectName,'Saved campaign');
  store.save({...project,projectName:'Retried successfully'});
  assert.equal(new ProjectStore(out).get(project.projectId)!.projectName,'Retried successfully');
});
