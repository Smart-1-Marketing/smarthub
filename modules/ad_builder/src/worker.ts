/**
 * Render Background Worker entrypoint.
 *
 * Runs the same queue loop without binding a port. A Render Background Worker
 * has no HTTP interface and no health check, so it must not call listen().
 *
 * With the current in-memory queue this process cannot see jobs enqueued by the
 * web service — they are separate processes. Deploy this only once the queue is
 * backed by Render Key Value. Until then run the service in its default
 * single-process mode, where WORKER_MODE is unset and the loop runs in-band.
 */

import { startWorkerLoop } from './jobs';

console.log('smart1-ad-builder worker started');
if (!process.env.REDIS_URL && !process.env.KEY_VALUE_URL) {
  console.warn(
    'WARNING: no shared queue configured (REDIS_URL / KEY_VALUE_URL unset). ' +
      'This worker has its own in-memory queue and will not see jobs from the web service.',
  );
}

startWorkerLoop();

// Keep the process alive; the loop is unref'd.
setInterval(() => {}, 1 << 30);

for (const signal of ['SIGTERM', 'SIGINT'] as const) {
  process.on(signal, () => {
    console.log(`${signal} received, exiting`);
    process.exit(0);
  });
}
