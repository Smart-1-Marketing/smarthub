/**
 * Can an alert actually leave this process?
 *
 * notify.ts has said in its own header since it was written that a missing
 * transport is "appended to out/notifications/outbox.jsonl instead of being
 * lost, and diagnostics flags the missing configuration". Diagnostics did
 * not: there was no such check, and `notificationsConfigured()` -- written
 * for exactly this -- had no caller anywhere in the repo.
 *
 * What that costs is the point. Nine call sites in server.ts raise an alert
 * and every one discards the NotifyResult, so the route reports success
 * whether or not anything was sent. The self-health timer is the worst of
 * them: it runs the diagnostics every three hours so a 2am failure pages
 * somebody instead of waiting for a customer to find it, and with no
 * transport that page is a line in a JSONL file on the output directory,
 * which a deploy wipes. The thing built to say the tool is broken was itself
 * unrouted, and nothing could report it.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { notify, notificationsConfigured, notificationsState, outboxState } from '../src/notify';
import { runDiagnostics } from '../src/diagnostics';

const KEYS = ['RESEND_API_KEY', 'EMAIL_TO', 'NOTIFY_WEBHOOK_URL'] as const;

function withEnv<T>(env: Record<string, string>, fn: () => T): T {
  const saved = Object.fromEntries(KEYS.map((k) => [k, process.env[k]]));
  try {
    for (const k of KEYS) delete process.env[k];
    Object.assign(process.env, env);
    return fn();
  } finally {
    for (const k of KEYS) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k] as string;
    }
  }
}

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'notify-test-'));
}

test('a key with nowhere to send it is not a configured channel', () => {
  // sendResend() throws before it reaches the API when EMAIL_TO is unset, so
  // this transport is set up and fails every time. A boolean over the two
  // keys called it healthy -- which is worse than off, because somebody set
  // the key and believes alerts are on.
  withEnv({ RESEND_API_KEY: 'k' }, () => {
    const { ready, blocked } = notificationsState();
    assert.deepEqual(ready, []);
    assert.equal(blocked.length, 1);
    assert.match(blocked[0].why, /EMAIL_TO/);
    assert.equal(notificationsConfigured(), false);
  });
});

test('and the states that do work say which transport is carrying them', () => {
  withEnv({ RESEND_API_KEY: 'k', EMAIL_TO: 'ops@example.com' }, () => {
    assert.deepEqual(notificationsState().ready, ['email']);
  });
  withEnv({ NOTIFY_WEBHOOK_URL: 'https://hooks.example.com/x' }, () => {
    assert.deepEqual(notificationsState().ready, ['webhook']);
    assert.deepEqual(notificationsState().blocked, [], 'a webhook needs no second variable');
  });
  withEnv({}, () => {
    assert.deepEqual(notificationsState(), { ready: [], blocked: [] });
  });
});

test('a half-configured email does not hide a working webhook', () => {
  withEnv({ RESEND_API_KEY: 'k', NOTIFY_WEBHOOK_URL: 'https://hooks.example.com/x' }, () => {
    const { ready, blocked } = notificationsState();
    assert.deepEqual(ready, ['webhook'], 'the webhook still delivers');
    assert.equal(blocked.length, 1, 'and the broken half is still reported');
  });
});

test('the outbox says how many alerts went nowhere, and when', async () => {
  const dir = tmp();
  assert.deepEqual(outboxState(dir), { count: 0 }, 'no file yet is not an error');
  await withEnv({}, async () => {
    const r = await notify({ subject: 'Proof approved', body: 'eight sizes' }, dir);
    // The return value nine call sites in server.ts discard.
    assert.equal(r.sent, false);
    assert.match(r.error ?? '', /RESEND_API_KEY or NOTIFY_WEBHOOK_URL/);
  });
  const state = outboxState(dir);
  assert.equal(state.count, 1);
  assert.ok(state.latest, 'and when the last one was');
});

test('a torn line costs the date, not the count', () => {
  // The process can be killed mid-append, and a count that throws on one bad
  // line reports zero alerts outstanding -- the reassuring direction.
  const dir = tmp();
  fs.mkdirSync(path.join(dir, 'notifications'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'notifications', 'outbox.jsonl'),
    '{"at":"2026-01-01T00:00:00Z","subject":"a"}\n{not json\n');
  const state = outboxState(dir);
  assert.equal(state.count, 2, 'both lines are alerts that went nowhere');
  assert.equal(state.latest, '2026-01-01T00:00:00Z', 'read back past the torn one');
});

test('diagnostics reports the channel, in three states rather than two', async () => {
  const dir = tmp();
  const level = async (env: Record<string, string>) => {
    const saved = Object.fromEntries(KEYS.map((k) => [k, process.env[k]]));
    for (const k of KEYS) delete process.env[k];
    Object.assign(process.env, env);
    try {
      const r = await runDiagnostics({ outDir: dir, assetRoot: process.cwd() });
      const c = r.checks.find((x) => x.id === 'notify.transport');
      assert.ok(c, 'the check notify.ts promises is actually registered');
      return c!;
    } finally {
      for (const k of KEYS) {
        if (saved[k] === undefined) delete process.env[k];
        else process.env[k] = saved[k] as string;
      }
    }
  };

  const none = await level({});
  assert.equal(none.level, 'warn');
  assert.match(none.detail, /outbox\.jsonl/, 'names where the alerts are going');
  assert.match(none.fix ?? '', /NOTIFY_WEBHOOK_URL/, 'and what would fix it');

  const half = await level({ RESEND_API_KEY: 'k' });
  assert.equal(half.level, 'fail', 'set up and throwing is worse than off, not the same');

  const ok = await level({ NOTIFY_WEBHOOK_URL: 'https://hooks.example.com/x' });
  assert.equal(ok.level, 'ok');
  assert.equal(ok.fix, undefined, 'a passing check carries no fix');
});
