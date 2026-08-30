/**
 * Notifications.
 *
 * The pattern used everywhere else in this codebase: a real transport when
 * credentials are configured, and an honest, durable fallback when they are
 * not. A missing key must never mean a message silently vanishes.
 *
 * Two transports, because small teams split roughly in half between "we use
 * email" and "we use Slack":
 *
 *   Resend, if RESEND_API_KEY is set — a plain REST call, no SDK needed.
 *   A Slack (or any) incoming webhook, if NOTIFY_WEBHOOK_URL is set.
 *
 * Both can be set at once; the message goes to both. If neither is set, the
 * notification is appended to out/notifications/outbox.jsonl instead of being
 * lost, and diagnostics flags the missing configuration.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

export interface Notification {
  subject: string;
  /** Plain text. Kept deliberately simple — this is an alert, not a newsletter. */
  body: string;
  /** A link back into the app, appended to the body for both transports. */
  url?: string;
  /** Overrides EMAIL_TO for this one message. */
  to?: string;
}

export interface NotifyResult {
  sent: boolean;
  transports: string[];
  error?: string;
}

async function sendResend(n: Notification): Promise<void> {
  const apiKey = process.env.RESEND_API_KEY!;
  const to = n.to ?? process.env.EMAIL_TO;
  const from = process.env.EMAIL_FROM ?? 'Smart 1 Ad Builder <onboarding@resend.dev>';
  if (!to) throw new Error('RESEND_API_KEY is set but EMAIL_TO is not');

  const text = n.url ? `${n.body}\n\n${n.url}` : n.body;
  const res = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ from, to, subject: n.subject, text }),
  });
  if (!res.ok) throw new Error(`Resend returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
}

async function sendWebhook(n: Notification): Promise<void> {
  const url = process.env.NOTIFY_WEBHOOK_URL!;
  const text = `*${n.subject}*\n${n.body}${n.url ? `\n${n.url}` : ''}`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text }), // Slack-compatible; most webhook receivers accept this shape.
  });
  if (!res.ok) throw new Error(`Webhook returned ${res.status}`);
}

function writeOutbox(outDir: string, n: Notification, reason: string): void {
  const dir = path.join(outDir, 'notifications');
  fs.mkdirSync(dir, { recursive: true });
  const line = JSON.stringify({ at: new Date().toISOString(), reason, ...n }) + '\n';
  fs.appendFileSync(path.join(dir, 'outbox.jsonl'), line);
}

export async function notify(n: Notification, outDir: string): Promise<NotifyResult> {
  const transports: string[] = [];
  const errors: string[] = [];

  if (process.env.RESEND_API_KEY) {
    try { await sendResend(n); transports.push('email'); }
    catch (e: any) { errors.push(`email: ${e?.message ?? e}`); }
  }
  if (process.env.NOTIFY_WEBHOOK_URL) {
    try { await sendWebhook(n); transports.push('webhook'); }
    catch (e: any) { errors.push(`webhook: ${e?.message ?? e}`); }
  }

  if (!transports.length) {
    writeOutbox(outDir, n, errors.length ? `all transports failed: ${errors.join('; ')}` : 'no transport configured');
    return { sent: false, transports: [], error: errors.join('; ') || 'No RESEND_API_KEY or NOTIFY_WEBHOOK_URL configured' };
  }
  if (errors.length) writeOutbox(outDir, n, `partial failure: ${errors.join('; ')}`);
  return { sent: true, transports, error: errors.join('; ') || undefined };
}

export interface NotifyState {
  /** Transports that can actually deliver a message right now. */
  ready: string[];
  /** Transports set up but unable to send, and why. */
  blocked: { transport: string; why: string }[];
}

/**
 * What the alert channel can actually do, rather than which keys are present.
 *
 * The distinction earns its place on one case: `RESEND_API_KEY` set with no
 * `EMAIL_TO` is a transport that is configured and throws on every send --
 * sendResend() raises before it reaches the API. A boolean over the two keys
 * calls that healthy, which is the confident wrong answer: somebody who set
 * the key believes alerts are on, and every one of them lands in the outbox
 * behind an error nobody reads. Set up and failing is not the same state as
 * not set up, and only one of the two is somebody halfway through a job.
 */
export function notificationsState(): NotifyState {
  const ready: string[] = [];
  const blocked: { transport: string; why: string }[] = [];

  if (process.env.RESEND_API_KEY) {
    if (process.env.EMAIL_TO) ready.push('email');
    else blocked.push({ transport: 'email', why: 'RESEND_API_KEY is set but EMAIL_TO is not' });
  }
  if (process.env.NOTIFY_WEBHOOK_URL) ready.push('webhook');

  return { ready, blocked };
}

export function notificationsConfigured(): boolean {
  return notificationsState().ready.length > 0;
}

/**
 * How many alerts have gone to the outbox, and when the last one did.
 *
 * A warning that says a channel is unconfigured is abstract; one that says
 * thirty-seven alerts went nowhere, the most recent an hour ago, is a thing
 * somebody acts on. Counted rather than read: these lines carry client names
 * and this feeds a page.
 */
export function outboxState(outDir: string): { count: number; latest?: string } {
  const file = path.join(outDir, 'notifications', 'outbox.jsonl');
  try {
    const lines = fs.readFileSync(file, 'utf8').split('\n').filter((l) => l.trim());
    let latest: string | undefined;
    for (let i = lines.length - 1; i >= 0 && !latest; i--) {
      try { latest = JSON.parse(lines[i])?.at; } catch { /* a torn line costs the date, not the count */ }
    }
    return { count: lines.length, latest };
  } catch {
    // No file is the ordinary case on a fresh boot and is not an error.
    return { count: 0 };
  }
}
