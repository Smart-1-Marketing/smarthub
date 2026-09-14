import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import {randomUUID} from 'node:crypto';
import {ProjectStore} from '../src/projects';
import {artworkFingerprint,campaignRevision} from '../src/campaign-state';
import {approveReview,fileHash} from '../src/review-set';
import {createClientProof,getClientProof,recordProofSent,decideClientProof,clientProofHtml,proofDownload,recoverProofDeliveries} from '../src/workflow';
function fixture(t:any){
  const out=fs.mkdtempSync(path.join(os.tmpdir(),'guided-ad-'));t.after(()=>fs.rmSync(out,{recursive:true,force:true}));
  const doc:any={campaign:{requestId:'fictional',campaignName:'Example offer',brand:{name:'Example Studio',logos:{primary:'logo.png'},colors:{primary:'#000',light:'#fff'},fonts:{headline:'Montserrat',body:'Open Sans'}},concepts:[{conceptId:'A',name:'A',layoutFamily:'T07',hero:{},copy:{default:{headline:'See our work',cta:'Learn more'}}}]},platforms:['google']};
  fs.mkdirSync(path.join(out,'campaigns'));const file=path.join(out,'campaigns','fictional.json');fs.writeFileSync(file,JSON.stringify(doc));
  const store=new ProjectStore(out),project=store.create({projectName:'Example offer',client:'Example Studio',domain:'example.test',campaignName:'Example offer',requestId:'fictional'});
  const reviewId=randomUUID(),dir=path.join(out,'reviews',reviewId);fs.mkdirSync(dir,{recursive:true});
  const key={conceptId:'A',platform:'google',size:'300x250'},image=path.join(dir,'ad.png');fs.writeFileSync(image,'frozen artwork');
  const cell={...key,file:image,inputHash:artworkFingerprint(doc,key,out),fileHash:fileHash(image),status:'pass',qa:[]};
  fs.writeFileSync(path.join(dir,'review.json'),JSON.stringify({id:reviewId,projectId:project.projectId,revision:campaignRevision(doc),createdAt:new Date().toISOString(),status:'ready',cells:[cell]}));
  const approve=()=>{approveReview(out,out,store,project,reviewId,campaignRevision(doc),'Staff');return store.get(project.projectId)!;};
  return {out,file,doc,store,project,reviewId,dir,cell,approve};
}
test('fictional campaign: staff QA approval, frozen proof, GHL receipt, client approval and exact final package',t=>{
  const f=fixture(t),project=f.approve();const proof=createClientProof(f.out,f.out,project,f.reviewId);
  assert.notEqual(proof.cells[0].file,f.cell.file);assert.equal(createClientProof(f.out,f.out,project,f.reviewId).token,proof.token);
  recordProofSent(f.out,f.store,project,proof.token,'ghl-message-1');
  const done=decideClientProof(f.out,f.out,f.store,proof.token,{action:'approve'});
  assert.equal(done.status,'complete');assert.ok(fs.readFileSync(proofDownload(f.out,proof.token)).includes(Buffer.from('frozen artwork')));
  assert.equal(f.store.get(project.projectId)?.delivered?.length,1);assert.equal(f.store.get(project.projectId)?.client,'Example Studio');
  decideClientProof(f.out,f.out,f.store,proof.token,{action:'approve'});assert.equal(f.store.get(project.projectId)?.delivered?.length,1);
  assert.ok(clientProofHtml(done).includes('Download approved final files'));
});
test('proof requires staff sign-off and refuses modified source before a client decision',t=>{
  const f=fixture(t);assert.throws(()=>createClientProof(f.out,f.out,f.project,f.reviewId),/approval changed/);
  const proof=createClientProof(f.out,f.out,f.approve(),f.reviewId);f.doc.campaign.campaignName='Changed';fs.writeFileSync(f.file,JSON.stringify(f.doc));
  assert.throws(()=>decideClientProof(f.out,f.out,f.store,proof.token,{action:'approve'}),/superseded/);assert.equal(getClientProof(f.out,proof.token).status,'ready');
});
test('client changes stay tied to a valid size and version and cannot later become a silent approval',t=>{
  const f=fixture(t),proof=createClientProof(f.out,f.out,f.approve(),f.reviewId);
  assert.throws(()=>decideClientProof(f.out,f.out,f.store,proof.token,{action:'changes',size:'other',notes:'Fix copy'}),/Choose a size/);
  const changed=decideClientProof(f.out,f.out,f.store,proof.token,{action:'changes',size:'A/google/300x250',notes:'Please shorten the headline.'});
  assert.equal(f.store.get(f.project.projectId)!.approvals.length,0);
  assert.equal(f.store.get(f.project.projectId)!.status,'in-build');
  assert.equal(changed.status,'changes-requested');assert.ok(f.store.get(f.project.projectId)!.notes.some(n=>n.includes('A/google/300x250')));
  assert.throws(()=>decideClientProof(f.out,f.out,f.store,proof.token,{action:'approve'}),/already been requested/);
});
test('unapproval or tampered frozen files prevent client approval',t=>{
  const f=fixture(t),proof=createClientProof(f.out,f.out,f.approve(),f.reviewId);
  fs.appendFileSync(proof.cells[0].file,'changed');assert.throws(()=>decideClientProof(f.out,f.out,f.store,proof.token,{action:'approve'}),/artwork or its approval changed/);
});
test('interrupted packaging recovers without duplicate delivery receipts',t=>{
  const f=fixture(t),proof=createClientProof(f.out,f.out,f.approve(),f.reviewId);proof.status='approved';proof.decisionAt=new Date().toISOString();
  fs.writeFileSync(path.join(f.out,'client-proofs',proof.token,'proof.json'),JSON.stringify(proof));
  recoverProofDeliveries(f.out,f.store);recoverProofDeliveries(f.out,f.store);
  assert.equal(getClientProof(f.out,proof.token).status,'complete');assert.equal(f.store.get(f.project.projectId)?.delivered?.length,1);
});

test('receipt replay repairs campaign history without duplicating its entry',t=>{
  const f=fixture(t),project=f.approve(),proof=createClientProof(f.out,f.out,project,f.reviewId);
  proof.messageId='receipt-1';proof.sentAt=new Date().toISOString();proof.status='sent';
  fs.writeFileSync(path.join(f.out,'client-proofs',proof.token,'proof.json'),JSON.stringify(proof));
  recordProofSent(f.out,f.store,project,proof.token,'receipt-1');
  recordProofSent(f.out,f.store,project,proof.token,'receipt-1');
  assert.equal(f.store.get(project.projectId)!.notes.filter(n=>n.includes('receipt-1')).length,1);
  assert.throws(()=>recordProofSent(f.out,f.store,project,proof.token,'different'),/different GHL receipt/);
});
