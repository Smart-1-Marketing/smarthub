import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { randomUUID } from 'node:crypto';
import { ProjectStore } from '../src/projects';
import { artworkFingerprint, campaignRevision, saveCampaignDocument } from '../src/campaign-state';
import { captureVersion, versions, changes, comparisonVersions, resolveVersion } from '../src/history';
import { approveReview, fileHash } from '../src/review-set';
import { sweep } from '../src/retention';

function fixture(t: any) {
  const out=fs.mkdtempSync(path.join(os.tmpdir(),'ad-review-'));
  t.after(()=>fs.rmSync(out,{recursive:true,force:true}));
  const doc:any={campaign:{requestId:'TEST',campaignName:'Example',brand:{name:'Example',logos:{primary:'logo.png'},colors:{primary:'#000',light:'#fff'},fonts:{headline:'Montserrat',body:'Open Sans'}},concepts:[{conceptId:'A',name:'A',layoutFamily:'T07',hero:{},copy:{default:{headline:'See our work',cta:'Learn more'}}}]},platforms:['google']};
  fs.mkdirSync(path.join(out,'campaigns'));const file=path.join(out,'campaigns','TEST.json');fs.writeFileSync(file,JSON.stringify(doc));
  const store=new ProjectStore(out);const project=store.create({projectName:'Example',client:'Example',domain:'example.test',campaignName:'Example',requestId:'TEST'});
  const id=randomUUID(), dir=path.join(out,'reviews',id);fs.mkdirSync(dir,{recursive:true});
  const cells=['300x250','728x90'].map((size,i)=>{const key={conceptId:'A',platform:'google',size};const file=path.join(dir,size+'.png');fs.writeFileSync(file,'image-'+size);return {...key,file,inputHash:artworkFingerprint(doc,key,out),fileHash:fileHash(file),status:i?'warn':'pass',qa:i?[{check:'contrast',status:'warn',detail:'Review contrast'}]:[]};});
  const review={id,projectId:project.projectId,revision:campaignRevision(doc),createdAt:new Date().toISOString(),status:'ready',cells};fs.writeFileSync(path.join(dir,'review.json'),JSON.stringify(review));
  return {out,doc,file,store,project,review,dir};
}
test('bulk approval signs only passing sizes and preserves warned sizes for individual review',t=>{
  const f=fixture(t);const r=approveReview(f.out,f.out,f.store,f.project,f.review.id,f.review.revision,'Reviewer');
  assert.deepEqual(r.approved,['A/300x250']);assert.deepEqual(r.skipped,['A/728x90']);assert.equal(r.approvals.length,1);assert.equal(r.approvals[0].artifact,f.review.cells[0].file);
});
test('bulk approval rejects a changed file without writing any sign-offs',t=>{
  const f=fixture(t);fs.appendFileSync(f.review.cells[0].file,'changed');
  assert.throws(()=>approveReview(f.out,f.out,f.store,f.project,f.review.id,f.review.revision),/Artwork changed/);assert.equal(f.store.get(f.project.projectId)?.approvals?.length??0,0);
});
test('bulk approval rejects stale source and a review belonging to a different project',t=>{
  const f=fixture(t);f.doc.campaign.campaignName='New';fs.writeFileSync(f.file,JSON.stringify(f.doc));
  assert.throws(()=>approveReview(f.out,f.out,f.store,f.project,f.review.id,f.review.revision),/saved version changed/);
  assert.throws(()=>approveReview(f.out,f.out,f.store,{...f.project,projectId:'other'},f.review.id,f.review.revision),/another project/);
});
test('save history retains before and after versions and exposes actual field changes',t=>{
  const f=fixture(t);captureVersion(f.file,f.doc,campaignRevision(f.doc));const next=structuredClone(f.doc);next.campaign.campaignName='Updated';
  saveCampaignDocument(f.file,next,campaignRevision(f.doc),f.project,f.out);
  assert.equal(versions(f.file).length,2);assert.deepEqual(changes(f.doc,next),[{path:'campaign.campaignName',before:'Example',after:'Updated'}]);
});
test('retention removes abandoned approvals but preserves referenced artifacts and fails closed on corrupt records',t=>{
  const f=fixture(t);const dir=path.join(f.out,'approvals');fs.mkdirSync(dir);const kept=path.join(dir,'signed.png'),orphan=path.join(dir,'orphan.png');
  for(const file of [kept,orphan]){fs.writeFileSync(file,'image');fs.utimesSync(file,0,0);}
  f.project.approvals=[{conceptId:'A',platform:'google',size:'300x250',at:'old',artifact:kept}];f.store.save(f.project);
  sweep({outDir:f.out});assert.ok(fs.existsSync(kept));assert.ok(!fs.existsSync(orphan));
  fs.writeFileSync(orphan,'image');fs.utimesSync(orphan,0,0);fs.writeFileSync(path.join(f.out,'projects','corrupt.json'),'{');
  sweep({outDir:f.out});assert.ok(fs.existsSync(orphan));
});


test('bulk approval refuses newly added copy warnings and a sheet from older review checks',t=>{
  const f=fixture(t);f.doc.notes=['OpenAI copy failed; using form answers'];fs.writeFileSync(f.file,JSON.stringify(f.doc));
  assert.throws(()=>approveReview(f.out,f.out,f.store,f.project,f.review.id,f.review.revision),/Copy warnings/);
  delete f.doc.notes;fs.writeFileSync(f.file,JSON.stringify(f.doc));
  fs.writeFileSync(path.join(f.dir,'review.json'),JSON.stringify({...f.review,qaVersion:'older-release'}));
  assert.throws(()=>approveReview(f.out,f.out,f.store,f.project,f.review.id,f.review.revision),/review checks changed/);
  assert.equal(f.store.get(f.project.projectId)?.approvals?.length??0,0);
});


test('comparison keeps distinct artwork captures even when campaign JSON did not change',()=>{
  const saved=[{revision:'same-source',savedAt:'2026-09-10',doc:{campaign:'unchanged'}}];
  const captured=[{id:'old',revision:'same-source',createdAt:'2026-09-11',cells:[{image:'old.png'}]}, {id:'new',revision:'same-source',createdAt:'2026-09-12',cells:[{image:'new.png'}]}];
  assert.deepEqual(comparisonVersions(saved,captured).map(v=>v.id),['new','old']);
  assert.equal(resolveVersion(saved,captured,'old').review.cells[0].image,'old.png');
  assert.equal(resolveVersion(saved,captured,'new').review.cells[0].image,'new.png');
});
