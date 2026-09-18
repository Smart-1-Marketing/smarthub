import * as fs from 'node:fs';
import * as path from 'node:path';
import { randomUUID } from 'node:crypto';
import { artworkFingerprint, campaignRevision, CampaignConflict, readCampaign } from './campaign-state';
import { readReview, fileHash } from './review-set';
import { buildZip } from './deliver';
import { notify, type Notification, type NotifyResult } from './notify';
import type { Project, ProjectStore } from './projects';

/** Fire-and-forget alert transport, injectable for tests. */
export type Notifier = (n: Notification, outDir: string) => Promise<NotifyResult>;

type ProofCell = { conceptId: string; platform: string; size: string; file: string; fileHash: string; inputHash: string };
export type ClientProof = { token: string; version: number; projectId: string; reviewId: string; revision: string; client: string;
  campaign: string; createdAt: string; cells: ProofCell[]; status: 'ready'|'sent'|'changes-requested'|'approved'|'complete';
  sentAt?: string; messageId?: string; decisionAt?: string; notes?: string; size?: string; download?: string;
  /** Notes the client left on single ads while looking, before any decision. */
  comments?: ProofComment[] };
/** One note from the client about one ad on the proof, or about the set when `cell` is blank. */
export type ProofComment = { id: string; cell: string; size: string; text: string; at: string };
export const COMMENT_LIMIT = 40;
export const COMMENT_LENGTH = 1000;
const cellKey = (c: { conceptId: string; platform: string; size: string }) => `${c.conceptId}/${c.platform}/${c.size}`;
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
/**
 * A note from the client about one ad.
 *
 * "Request changes" was the only way a client could say anything, and it
 * closes the proof: one note, then a new version. Looking through eleven
 * sizes, people notice things one at a time -- the 728x90 crops the logo, the
 * story is fine -- and a single box at the bottom loses which ad each remark
 * was about. A note lands on the ad it was written under, stays with the
 * proof, and is written onto the project record so the build screen shows it
 * on that size. It is not a decision: the proof stays open for the approve or
 * the change request that follows.
 */
export function commentOnClientProof(out:string,store:ProjectStore,token:string,body:any,notifier:Notifier=notify): ProofComment {
  const proof=getClientProof(out,token), project=store.get(proof.projectId);
  if(!project)throw new Error('This campaign is unavailable.');
  if(proof.status==='complete'||proof.status==='approved')throw new CampaignConflict('This version has been approved, so notes on it are closed. Ask Smart 1 if something needs changing.');
  const text=String(body?.text||'').replace(/\s+/g,' ').trim();
  if(!text||text.length>COMMENT_LENGTH)throw Object.assign(new Error(`Write a note of up to ${COMMENT_LENGTH} characters.`),{statusCode:400});
  const cell=String(body?.cell||'');
  const found=cell?proof.cells.find(c=>cellKey(c)===cell):null;
  if(cell && !found)throw Object.assign(new Error('Choose an ad from this proof.'),{statusCode:400});
  const comments=proof.comments??[];
  if(comments.length>=COMMENT_LIMIT)throw Object.assign(new Error('This proof has all the notes it can hold. Use Request changes for the rest.'),{statusCode:400});
  const note:ProofComment={id:randomUUID(),cell,size:found?found.size:'',text,at:new Date().toISOString()};
  proof.comments=[...comments,note];save(out,proof);
  const where=found?found.size+' ('+found.platform+')':'the whole set';
  project.notes.push(`[${note.at}] Client note on ${where}, version ${proof.version}: ${text}`);
  store.save(project);
  // A note on the proof lands on the project record and is drawn on the build
  // screen, but nobody sees it until they open that campaign. Page the team
  // as soon as it lands, with the size to jump to.
  const base=(process.env.PUBLIC_URL??'').replace(/\/$/,'');
  const link=`${base}/build?request=${encodeURIComponent(project.requestId)}${found?`&size=${encodeURIComponent(found.size)}`:''}`;
  void notifier({
    subject:`Client note — ${project.client} / ${project.projectName}`,
    body:`${project.client} left a note on ${where}, version ${proof.version}:\n\n${text}`,
    url:link,
  }, out).catch(e=>console.error(`[client-note] notify failed: ${e?.message??e}`));
  return note;
}
/** Every proof in the store, grouped by project, read once for a list view. */
export function clientProofsByProject(out:string): Map<string,ClientProof[]> {
  const dir=path.join(out,'client-proofs'); const byProject=new Map<string,ClientProof[]>();
  if(!fs.existsSync(dir))return byProject;
  for(const token of fs.readdirSync(dir)){try{const p=getClientProof(out,token);const rows=byProject.get(p.projectId)??[];rows.push(p);byProject.set(p.projectId,rows);}catch{/* not a proof */}}
  for(const rows of byProject.values())rows.sort((a,b)=>b.createdAt.localeCompare(a.createdAt));
  return byProject;
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
  const open=!['complete','approved'].includes(proof.status);
  const notesFor=(key:string)=>(proof.comments??[]).filter(n=>n.cell===key);
  const noteList=(key:string)=>`<ul class="notes" data-notes="${esc(key)}">${notesFor(key).map(n=>`<li>${esc(n.text)}</li>`).join('')}</ul>`;
  const noteBox=(key:string,what:string)=>open?`<details class="note"><summary>Add a note about ${esc(what)}</summary><textarea maxlength="${COMMENT_LENGTH}" data-note-for="${esc(key)}" placeholder="What you noticed on this ad"></textarea><button type="button" data-send-note="${esc(key)}">Send note</button></details>`:'';
  const cells=proof.cells.map(c=>{const key=cellKey(c);return `<article data-cell="${esc(key)}"><h2>${esc(c.size)} · ${esc(c.platform)}</h2><img alt="${esc(c.conceptId+' '+c.size)}" src="data:image/${path.extname(c.file)==='.png'?'png':'jpeg'};base64,${fs.readFileSync(c.file).toString('base64')}">${noteList(key)}${noteBox(key,'this ad')}</article>`;}).join('');
  const setNotes=`<section id="setnotes"><h2>Notes on the set</h2><p>Each ad above has its own note box, so a remark stays with the ad it is about. This one is for the set as a whole.</p>${noteList('')}${noteBox('','the whole set')}</section>`;
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(proof.client)} — Review your ads</title><style>body{font:17px/1.5 system-ui;margin:0;background:#f5f7fa;color:#192631}main{max-width:1100px;margin:auto;padding:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}article{background:white;padding:16px;border-radius:12px}img{max-width:100%;max-height:350px;object-fit:contain}button,select,textarea{font:inherit;padding:12px;max-width:100%}textarea{display:block;width:90%;min-height:100px}button{cursor:pointer;margin:12px 8px 0 0}#status{padding:16px;background:white}label{display:block;margin-top:12px}.notes{list-style:none;padding:0;margin:8px 0 0}.notes li{background:#eef3f8;border-radius:8px;padding:8px 10px;margin-top:6px;font-size:15px}.notes li::before{content:"Your note: ";color:#4a5c6a}.note summary{cursor:pointer;color:#1f5fbf;margin-top:10px}.note textarea{min-height:70px}.said{font-size:14px}.sent{color:#1a7f4b}.notsent{color:#b3261e}#decision-said{margin:8px 0 0;min-height:1.4em}</style><main><h1>${esc(proof.client)}: your ad proof</h1><p>${esc(proof.campaign)} · Version ${proof.version} · ${esc(new Date(proof.createdAt).toLocaleDateString())}</p><p>Review the full set below. Approve this version or tell Smart 1 what to change. No sign-in is needed.</p><p id="status" role="status">${esc(proof.status.replace(/-/g,' '))}</p><div class="grid">${cells}</div>${setNotes}<section id="actions"><h2>Your decision</h2><button id="approve">Approve this ad set</button><details><summary>Request changes</summary><label>Which ad? <select id="size"><option value="">The whole set</option>${proof.cells.map(c=>`<option value="${esc(c.conceptId+'/'+c.platform+'/'+c.size)}">${esc(c.conceptId+' · '+c.size+' · '+c.platform)}</option>`).join('')}</select></label><label>What should change?<textarea id="notes" maxlength="3000"></textarea></label><button id="changes">Send change request</button></details><p id="decision-said" role="status" aria-live="polite"></p></section><p id="download">${proof.download?`<a href="${esc(proof.download)}">Download approved final files</a>`:''}</p></main><script>(function(){const status=document.getElementById('status'),said=document.getElementById('decision-said'),buttons=[...document.querySelectorAll('button')];const originalStatus=status.textContent;function decSay(text,cls){said.className='said '+(cls||'');said.textContent=text||'';}async function decide(action){buttons.forEach(b=>b.disabled=true);decSay('');status.textContent='Saving your decision…';try{const r=await fetch('/client-proof/${proof.token}/decision',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action,notes:document.getElementById('notes').value,size:document.getElementById('size').value})});const b=await r.json();if(!r.ok)throw Error(b.error||'Please try again.');status.textContent=b.status==='complete'?'Approved. Your final files are ready.':'Thank you. Smart 1 will review your requested changes.';document.getElementById('actions').hidden=true;if(b.download){const a=document.createElement('a');a.href=b.download;a.textContent='Download approved final files';document.getElementById('download').replaceChildren(a);}}catch(e){status.textContent=originalStatus;decSay(e.message||'That did not send. Please try again.','notsent');buttons.forEach(b=>b.disabled=false);}}document.getElementById('approve').onclick=()=>decide('approve');document.getElementById('changes').onclick=()=>decide('changes');document.querySelectorAll('[data-send-note]').forEach(btn=>{btn.onclick=async()=>{const key=btn.getAttribute('data-send-note'),box=document.querySelector('[data-note-for="'+key.replace(/"/g,'\\"')+'"]'),text=(box.value||'').trim();if(!text){box.focus();return;}btn.disabled=true;btn.textContent='Sending…';try{const r=await fetch('/client-proof/${proof.token}/comment',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({cell:key,text})});const b=await r.json();if(!r.ok)throw Error(b.error||'Please try again.');const li=document.createElement('li');li.textContent=b.text;document.querySelector('[data-notes="'+key.replace(/"/g,'\\"')+'"]').appendChild(li);box.value='';btn.textContent='Send note';say(btn,'Sent to Smart 1.','sent');}catch(e){btn.textContent='Send note';say(btn,e.message||'That did not send. Please try again.','notsent');}finally{btn.disabled=false;}};});function say(btn,text,cls){let el=btn.nextElementSibling;if(!el||!el.classList.contains('said')){el=document.createElement('span');el.className='said';btn.insertAdjacentElement('afterend',el);}el.className='said '+cls;el.setAttribute('role','status');el.textContent=' '+text;}if(${JSON.stringify(['complete','approved','changes-requested'].includes(proof.status))})document.getElementById('actions').hidden=true;})();</script></html>`;
}
