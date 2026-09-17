/**
 * One line of health per campaign, for the projects list.
 *
 * The list said a status pill and a render count, and neither answers the
 * question somebody opening it has: is this one done, and if not, what is
 * left? The answer is spread across three records -- the project's approvals,
 * the latest contact-sheet review's per-size verdicts, and the client proofs
 * with their notes -- and the only way to read it was to open the build
 * screen of each campaign in turn.
 *
 * This reads the three once and writes one sentence. Absent data is named
 * ("no review yet") rather than shown as a zero that reads like a fact.
 */
import type { Project } from './projects';

export interface HealthReview { status: 'building' | 'ready' | 'failed'; createdAt: string;
  cells: Array<{ conceptId: string; size: string; status: 'pass' | 'warn' | 'fail' }> }
export interface HealthProof { status: string; version: number; createdAt: string; sentAt?: string;
  decisionAt?: string; comments?: Array<{ at: string }> }

export interface CampaignHealth {
  /** Sizes the latest review measured; null when nothing has been reviewed. */
  sizes: number | null;
  approved: number;
  failing: number;
  warning: number;
  /** Notes the client left on the latest proof. */
  clientNotes: number;
  /** The latest proof's state, when there is one. */
  proof?: { status: string; version: number; at: string };
  /** What to look at first, or 'done'. */
  tone: 'done' | 'attention' | 'waiting' | 'quiet';
  line: string;
}

const plural = (n: number, one: string, many = one + 's') => `${n} ${n === 1 ? one : many}`;
const day = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

export function campaignHealth(project: Project, review: HealthReview | null, proofs: HealthProof[]): CampaignHealth {
  const approvedKeys = new Set((project.approvals ?? []).map((a) => `${a.conceptId}/${a.size}`));
  const ready = review && review.status === 'ready' ? review : null;
  const sizes = ready ? ready.cells.length : null;
  const failing = ready ? ready.cells.filter((c) => c.status === 'fail').length : 0;
  const warning = ready ? ready.cells.filter((c) => c.status === 'warn').length : 0;
  // Approvals that name a size the latest review measured; with no review,
  // every approval counts, because there is nothing to check it against.
  const approved = ready
    ? ready.cells.filter((c) => approvedKeys.has(`${c.conceptId}/${c.size}`)).length
    : approvedKeys.size;
  const latest = [...proofs].sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
  const clientNotes = latest?.comments?.length ?? 0;
  const proof = latest ? { status: latest.status, version: latest.version,
    at: latest.decisionAt ?? latest.sentAt ?? latest.createdAt } : undefined;

  const parts: string[] = [];
  let tone: CampaignHealth['tone'] = 'quiet';
  if (project.status === 'complete') {
    parts.push('Delivered');
    tone = 'done';
  } else if (sizes === null) {
    parts.push('No review yet');
    if (approved) parts.push(`${plural(approved, 'size')} approved`);
  } else {
    parts.push(`${approved} of ${plural(sizes, 'size')} approved`);
    if (failing) parts.push(`${failing} failing`);
    if (warning) parts.push(`${warning} with warnings`);
    tone = failing ? 'attention' : approved === sizes ? 'done' : 'quiet';
  }
  if (proof) {
    const when = day(proof.at);
    const said: Record<string, string> = {
      ready: 'proof ready to send', sent: 'proof with the client', 'changes-requested': 'client asked for changes',
      approved: 'client approved', complete: 'client approved',
    };
    parts.push((said[proof.status] ?? `proof ${proof.status}`) + (when ? ` ${when}` : ''));
    if (proof.status === 'changes-requested') tone = 'attention';
    else if (proof.status === 'sent' && tone !== 'attention') tone = 'waiting';
  }
  if (clientNotes) {
    parts.push(plural(clientNotes, 'client note'));
    if (tone === 'quiet' || tone === 'waiting') tone = 'attention';
  }
  return { sizes, approved, failing, warning, clientNotes, proof, tone, line: parts.join(' · ') };
}
