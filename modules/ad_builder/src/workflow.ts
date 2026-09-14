import * as fs from 'node:fs';
import * as path from 'node:path';
import { randomUUID } from 'node:crypto';
import { artworkFingerprint, campaignRevision, CampaignConflict, readCampaign } from './campaign-state';
import { readReview, fileHash } from './review-set';
import { buildZip } from './deliver';
import type { Project, ProjectStore } from './projects';

type ProofCell = { conceptId: string; platform: string; size: string; file: string; fileHash: string; inputHash: string };
export type ClientProof = { token: string; version: number; projectId: string; reviewId: string; revision: string; client: string;
  campaign: string; createdAt: string; cells: ProofCell[]; status: 'ready'|'sent'|'changes-requested'|'approved'|'complete';
  sentAt?: string; messageId?: string; decisionAt?: string; notes?: string; size?: string; download?: string };
const esc = (v: unknown) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const proofFile = (out: string, token: string) => {
  if (!/^[a-f0-9-]{36}$/.test(token)) throw new Error('Invalid proof link.');
  return path.join(out, 'client-proofs', token, 'proof.json');
};
function save(out: string, proof: ClientProof) {
  const file = proofFile(out, proof.token); fs.mkdirSync(path.dirname(file), {recursive:true});
  const temp = file + '.' + randomUUID() + '.tmp'; fs.writeFileSync(temp, JSON.stringify(proof)); fs.renameSync(temp, file);
}
export function getClientProof(out: string, token: string): ClientProof {
  try { return JSON.parse(fs.readFileSync(proofFile(out, token), 'utf8')); }
  catch { throw Object.assign(new Error('This proof link is unavailable. Please ask Smart 1 for a new link.'), {statusCode:404}); }
}
export function clientProofs(out: string, projectId: string): ClientProof[] {
  const dir = path.join(out, 'client-proofs'); if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).flatMap(token => {try {const p=getClientProof(out,token);return p.projectId===projectId?[p]:[];} catch{return [];}})
    .sort((a,b)=>b.createdAt.localeCompare(a.createdAt));
}
function verify(out: string, root: string, proof: ClientProof, project: Project) {
  const doc=readCampaign(path.join(out,'campaigns',project.requestId+'.json'));
  if(campaignRevision(doc)!==proof.revision) throw new CampaignConflict('This proof has been superseded. Please ask Smart 1 for the latest version.');
  for(const c of proof.cells) {
    const approval=project.approvals?.find(a=>a.conceptId===c.conceptId&&a.platform===c.platform&&a.size===c.size);
    if(!approval || approval.fileHash!==c.fileHash || approval.inputHash!==c.inputHash || !fs.existsSync(c.file) || fileHash(c.file)!==c.fileHash || artworkFingerprint(doc,c,root)!==c.inputHash)
      throw new CampaignConflict('The artwork or its approval changed. Please ask Smart 1 for a new proof.');
  }
}
export function createClientProof(out: string, root: string, project: Project, reviewId: string): ClientProof {
  const review=readReview(out,reviewId,project.projectId);
  if(review.status!=='ready'||!review.cells.length||review.cells.some(c=>c.status==='fail'||!c.file||!c.fileHash))
    throw new CampaignConflict('Finish review and resolve failed sizes before sending a proof.');
  const existing=clientProofs(out,project.projectId).find(p=>p.reviewId===reviewId && p.status!=='changes-requested');
  if(existing){verify(out,root,existing,project);return existing;}
  const proof:ClientProof={token:randomUUID(),version:clientProofs(out,project.projectId).length+1,projectId:project.projectId,reviewId,revision:review.revision,client:project.client,
    campaign:project.projectName,createdAt:new Date().toISOString(),status:'ready',cells:review.cells.map(c=>({conceptId:c.conceptId,platform:c.platform,size:c.size,file:c.file!,fileHash:c.fileHash!,inputHash:c.inputHash}))};
  verify(out,root,proof,project);
  // Freeze independent copies so retention of old review caches cannot break a sent proof.
  const dir=path.dirname(proofFile(out,proof.token)); fs.mkdirSync(dir,{recursive:true});
  proof.cells.forEach((c,i)=>{const file=path.join(dir,`${i}${path.extname(c.file)}`);fs.copyFileSync(c.file,file);c.file=file;});
  save(out,proof);return proof;
}
export function recordProofSent(out:string, store:ProjectStore, project:Project, token:string, messageId:string) {
  const proof=getClientProof(out,token);
  if(proof.projectId!==project.projectId)throw new Error('Proof belongs to another client.');
  if(!messageId || messageId.length>200)throw new Error('A GHL message receipt is required.');
  if(proof.messageId && proof.messageId!==messageId)throw new CampaignConflict('This proof already has a different GHL receipt.');
  if(!proof.messageId){proof.messageId=messageId;proof.sentAt=new Date().toISOString();if(proof.status==='ready')proof.status='sent';save(out,proof);}
  const fresh=store.get(project.projectId)!;
  if(proof.status==='sent' && !['approved','complete'].includes(fresh.status))fresh.status='proof-sent';
  const note=`[${proof.sentAt}] GHL accepted proof email. Version ${proof.version}. Message ${messageId}.`;
  if(!fresh.notes.includes(note))fresh.notes.push(note);
  store.save(fresh);return proof;
}
export function decideClientProof(out:string,root:string,store:ProjectStore,token:string,body:any) {
  const proof=getClientProof(out,token), project=store.get(proof.projectId);
  if(!project)throw new Error('This campaign is unavailable.');
  if(proof.status==='complete'||proof.status==='approved'){finishProofDelivery(out,store,proof);return getClientProof(out,token);}
  if(proof.status==='changes-requested')throw new CampaignConflict('Changes have already been requested. Smart 1 will send a new version.');
  verify(out,root,proof,project);
  if(body.action==='changes') {
    const notes=String(body.notes||'').trim();if(!notes||notes.length>3000)throw Object.assign(new Error('Describe the changes you need (up to 3,000 characters).'),{statusCode:400});
    const size=String(body.size||'');if(size && !proof.cells.some(c=>`${c.conceptId}/${c.platform}/${c.size}`===size))throw Object.assign(new Error('Choose a size from this proof.'),{statusCode:400});
    proof.status='changes-requested';proof.notes=notes;proof.size=size;proof.decisionAt=new Date().toISOString();save(out,proof);
    project.notes.push(`[${proof.decisionAt}] Client requested changes to version ${proof.revision.slice(0,8)}${size?' ('+size+')':''}: ${notes}`);
    // A requested revision must be editable; retain the immutable prior proof.
    const selected=size ? proof.cells.find(c=>`${c.conceptId}/${c.platform}/${c.size}`===size) : null;
    project.approvals=(project.approvals||[]).filter(a=>selected && (a.conceptId!==selected.conceptId || a.size!==selected.size));
    project.status='in-build';store.save(project);return proof;
  }
  if(body.action!=='approve')throw Object.assign(new Error('Choose approve or request changes.'),{statusCode:400});
  proof.status='approved';proof.decisionAt=new Date().toISOString();save(out,proof);
  finishProofDelivery(out,store,proof);return getClientProof(out,token);
}
export function finishProofDelivery(out:string,store:ProjectStore,proof:ClientProof) {
  if(!['approved','complete'].includes(proof.status))return;
  if(proof.status==='complete' && store.get(proof.projectId)?.delivered?.some(d=>d.zipUrl===proof.download))return;
  const entries=proof.cells.map(c=>{if(fileHash(c.file)!==c.fileHash)throw new Error('Approved file changed. Delivery needs staff attention.');return {name:`${c.platform}/${c.conceptId}_${c.size}${path.extname(c.file)}`,data:fs.readFileSync(c.file)};});
  entries.push({name:'README.txt',data:Buffer.from(`Approved display ads for ${proof.client}\n${proof.campaign}\nVersion ${proof.revision}\nApproved ${proof.decisionAt}\nFiles are grouped by their purchased platform.\n`)});
  const file=path.join(path.dirname(proofFile(out,proof.token)),'approved.zip');
  fs.writeFileSync(file+'.tmp',buildZip(entries));fs.renameSync(file+'.tmp',file);
  // Stable URL and receipt make retries and restart recovery idempotent.
  proof.download=`/client-proof/${proof.token}/download`;proof.status='complete';save(out,proof);
  const project=store.get(proof.projectId);if(!project)return;
  project.delivered=project.delivered||[];
  if(!project.delivered.some(d=>d.zipUrl===proof.download)) {
    project.delivered.push({at:proof.decisionAt!,zipUrl:proof.download,fileCount:proof.cells.length});
    project.notes.push(`[${proof.decisionAt}] Client approved version ${proof.revision.slice(0,8)}; ${proof.cells.length} final files delivered.`);
    project.status='complete';store.save(project);
  }
}
export function recoverProofDeliveries(out:string,store:ProjectStore) {
  const dir=path.join(out,'client-proofs');if(!fs.existsSync(dir))return;
  for(const token of fs.readdirSync(dir))try{const p=getClientProof(out,token);if(['approved','complete'].includes(p.status))finishProofDelivery(out,store,p);}catch(e){console.error('Proof delivery needs attention',token,String(e));}
}
export function proofDownload(out:string,token:string) {const p=getClientProof(out,token);if(p.status!=='complete')throw new Error('Final files are not ready.');return path.join(path.dirname(proofFile(out,token)),'approved.zip');}
export function clientProofHtml(proof:ClientProof) {
  const cells=proof.cells.map(c=>`<article><h2>${esc(c.size)} · ${esc(c.platform)}</h2><img alt="${esc(c.conceptId+' '+c.size)}" src="data:image/${path.extname(c.file)==='.png'?'png':'jpeg'};base64,${fs.readFileSync(c.file).toString('base64')}"></article>`).join('');
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(proof.client)} — Review your ads</title><style>body{font:17px/1.5 system-ui;margin:0;background:#f5f7fa;color:#192631}main{max-width:1100px;margin:auto;padding:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}article{background:white;padding:16px;border-radius:12px}img{max-width:100%;max-height:350px;object-fit:contain}button,select,textarea{font:inherit;padding:12px;max-width:100%}textarea{display:block;width:90%;min-height:100px}button{cursor:pointer;margin:12px 8px 0 0}#status{padding:16px;background:white}label{display:block;margin-top:12px}</style><main><h1>${esc(proof.client)}: your ad proof</h1><p>${esc(proof.campaign)} · Version ${proof.version} · ${esc(new Date(proof.createdAt).toLocaleDateString())}</p><p>Review the full set below. Approve this version or tell Smart 1 what to change. No sign-in is needed.</p><p id="status" role="status">${esc(proof.status.replace(/-/g,' '))}</p><div class="grid">${cells}</div><section id="actions"><h2>Your decision</h2><button id="approve">Approve this ad set</button><details><summary>Request changes</summary><label>Which ad? <select id="size"><option value="">The whole set</option>${proof.cells.map(c=>`<option value="${esc(c.conceptId+'/'+c.platform+'/'+c.size)}">${esc(c.conceptId+' · '+c.size+' · '+c.platform)}</option>`).join('')}</select></label><label>What should change?<textarea id="notes" maxlength="3000"></textarea></label><button id="changes">Send change request</button></details></section><p id="download">${proof.download?`<a href="${esc(proof.download)}">Download approved final files</a>`:''}</p></main><script>(function(){const status=document.getElementById('status'),buttons=[...document.querySelectorAll('button')];async function decide(action){buttons.forEach(b=>b.disabled=true);status.textContent='Saving your decision…';try{const r=await fetch('/client-proof/${proof.token}/decision',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action,notes:document.getElementById('notes').value,size:document.getElementById('size').value})});const b=await r.json();if(!r.ok)throw Error(b.error||'Please try again.');status.textContent=b.status==='complete'?'Approved. Your final files are ready.':'Thank you. Smart 1 will review your requested changes.';document.getElementById('actions').hidden=true;if(b.download){const a=document.createElement('a');a.href=b.download;a.textContent='Download approved final files';document.getElementById('download').replaceChildren(a);}}catch(e){status.textContent=e.message;buttons.forEach(b=>b.disabled=false);}}document.getElementById('approve').onclick=()=>decide('approve');document.getElementById('changes').onclick=()=>decide('changes');if(${JSON.stringify(['complete','approved','changes-requested'].includes(proof.status))})document.getElementById('actions').hidden=true;})();</script></html>`;
}
