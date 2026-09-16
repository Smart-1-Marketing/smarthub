"""Two instances, one mirror: the lock that stops working when the disk goes.

`jsonstore.exclusive()` held a `threading.Lock` and an `flock` on a sidecar
file. The flock serialises workers that share a filesystem -- which is every
worker for as long as this service has one disk mounted at one path, and is
NOT the two halves of a zero-downtime deploy. Those are separate instances
with separate filesystems: each takes its own flock on its own file, succeeds,
and serialises nothing. `status()` reported no lock error, because each flock
really was taken.

Measured before the fix, two processes each appending 30 rows through
`update_json()`:

    one data root  (one filesystem):   60 of 60 survived
    two data roots (two filesystems):  34 of 60 survived

Two halves were needed and each was confirmed red on its own:

    no advisory lock, authoritative read:  39 of 60
    advisory lock, read from local disk:   43 of 60
    both:                                  60 of 60

The second number is the one worth keeping. A lock alone does not fix this:
it serialises the two instances perfectly, and each one then reads its OWN
copy of the file, mutates that and writes the whole collection back. So the
read half of a read-modify-write has to come from the mirror as well.

THREADS CANNOT SHOW ANY OF THIS. It takes two real processes with two real
data roots, which is what `_run_two_instances` does.

This file sets HUB_DATA_DIR per child and inherits DATABASE_URL, which is
normally the combination CLAUDE.md warns about -- an empty disk in front of a
full mirror. Here that is the scenario under test rather than a mistake, and
every key is namespaced per run so two runs against one database cannot
collide.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hub import jsonstore  # noqa: E402


REPO = os.path.dirname(os.path.abspath(__file__))
ROWS_EACH = 40

# Bounded runs the burst below in ~0.7s and unbounded in ~120s, so anywhere in
# between reads as a verdict rather than as a stopwatch on a loaded runner.
CEILING_SECONDS = 45.0


def _on_postgres() -> bool:
    """True when the mirror is Postgres, which is what the lock needs."""
    try:
        if not jsonstore._init():
            return False
        return jsonstore._engine.dialect.name.startswith("postgres")
    except Exception:                                   # noqa: BLE001
        return False


ON_PG = _on_postgres()
# Said out loud rather than reported as a clean run: a skipped path that
# prints nothing is indistinguishable from one that passed.
SKIP_PG = "no Postgres mirror configured; the advisory lock has nothing to take"


# Both children wait for one shared instant before their first write, and the
# mutate is deliberately slow. Without that the test is a FLAKE AS A RED
# TEST -- measured, the unfixed code passed it two runs in three, because
# interpreter startup jitter let one child finish before the other began. A
# check that catches the defect sometimes reads as a flake somebody re-runs,
# which is how a real finding gets a re-run instead of a fix.
CHILD = textwrap.dedent("""
    import os, sys, time
    os.environ["HUB_DATA_DIR"] = sys.argv[1]
    sys.path.insert(0, %r)
    from hub import jsonstore
    tag, name = sys.argv[2], sys.argv[3]
    rows, start_at = int(sys.argv[4]), float(sys.argv[5])
    offset = int(sys.argv[6])

    def append(n):
        def mutate(cur):
            time.sleep(0.003)      # widen the read-modify-write window
            return (cur or []) + ["%%s-%%d" %% (tag, n)]
        return mutate

    path = os.path.join(jsonstore.data_root(), name)
    jsonstore._init()              # pay the connect cost before the barrier
    while time.time() < start_at:
        time.sleep(0.001)
    for i in range(rows):
        jsonstore.update_json(path, append(offset + i), default=[])
""") % REPO


def _script_in(d: str) -> str:
    path = os.path.join(d, "child.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(CHILD)
    return path


def _spawn(script, root, tag, name, rows, start_at, offset):
    return subprocess.Popen(
        [sys.executable, script, root, tag, name, str(rows), repr(start_at),
         str(offset)],
        cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _finish(proc) -> None:
    _, err = proc.communicate(timeout=300)
    if proc.returncode != 0:
        raise AssertionError(
            f"child failed: {err.decode('utf-8', 'replace')[:2000]}")


def _run_alternating(name: str, turns: int = 3) -> list:
    """Two instances writing strictly in turn. No race, no timing, no luck.

    The concurrent version below is the realistic shape and it is a poor
    *red* test: measured against the unfixed code it failed three runs in
    five, because whichever child happened to finish first left the other one
    a clean restore-from-mirror and nothing was lost. A check that catches the
    defect sometimes reads as a flake somebody re-runs.

    Alternating removes the race entirely and still reproduces the defect
    every time. A writes, B writes, A writes again -- and that third step is
    the whole thing: A's own file is now a version behind, so a disk-first
    read starts from it and drops what B did. The roots persist across the
    turns exactly as two instances' filesystems do.
    """
    d1 = tempfile.mkdtemp()
    d2 = tempfile.mkdtemp()
    try:
        script = _script_in(d1)
        for turn in range(turns):
            for root, tag in ((d1, "A"), (d2, "B")):
                _finish(_spawn(script, root, tag, name, 1, 0.0, turn))
    finally:
        for d in (d1, d2):
            shutil.rmtree(d, ignore_errors=True)
    raw = jsonstore._fetch(name)
    return json.loads(raw) if raw else []


def _run_concurrently(name: str, rows: int = ROWS_EACH) -> list:
    """Both at once, from one shared start instant -- the realistic shape."""
    d1 = tempfile.mkdtemp()
    d2 = tempfile.mkdtemp()
    try:
        script = _script_in(d1)
        start_at = time.time() + 2.0    # both interpreters up and connected
        procs = [_spawn(script, d, tag, name, rows, start_at, 0)
                 for d, tag in ((d1, "A"), (d2, "B"))]
        for proc in procs:
            _finish(proc)
    finally:
        for d in (d1, d2):
            shutil.rmtree(d, ignore_errors=True)
    raw = jsonstore._fetch(name)
    return json.loads(raw) if raw else []


class TwoInstances(unittest.TestCase):
    """The measurement this whole change exists for."""

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_alternating_instances_lose_nothing(self):
        """The gate. Deterministic: no race, so no run of it is luck."""
        turns = 3
        name = f"alt-{uuid.uuid4().hex}.json"
        self.addCleanup(jsonstore._forget, name)
        rows = _run_alternating(name, turns=turns)
        want = {f"{tag}-{i}" for tag in ("A", "B") for i in range(turns)}
        # The contents are asserted, not just the count: a set of the right
        # size and the wrong members is the same failure one step on.
        self.assertEqual(set(rows), want,
                         f"lost updates between instances: got {sorted(rows)}, "
                         f"wanted {sorted(want)}")
        self.assertEqual(len(rows), len(want), f"duplicated rows: {rows}")

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_concurrent_instances_lose_nothing(self):
        """The realistic shape, kept for what alternating cannot show.

        Two instances genuinely writing at once is what a zero-downtime
        deploy produces, and it exercises the lock rather than only the read.
        It is a weaker *red* test than the one above -- see _run_alternating
        -- so it stands beside it rather than instead of it.
        """
        name = f"race-{uuid.uuid4().hex}.json"
        self.addCleanup(jsonstore._forget, name)
        rows = _run_concurrently(name)
        a = len([r for r in rows if r.startswith("A-")])
        b = len([r for r in rows if r.startswith("B-")])
        self.assertEqual(
            (len(rows), a, b), (ROWS_EACH * 2, ROWS_EACH, ROWS_EACH),
            f"lost updates across instances: {len(rows)} of {ROWS_EACH * 2} "
            f"(A:{a} B:{b})")


class AdvisoryKey(unittest.TestCase):
    """The id has to be the same on two instances or it locks nothing."""

    def test_key_is_the_same_for_two_roots(self):
        base = os.environ.get("HUB_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as d1, \
                    tempfile.TemporaryDirectory() as d2:
                os.environ["HUB_DATA_DIR"] = d1
                one = jsonstore._advisory_key(os.path.join(d1, "x", "y.json"))
                os.environ["HUB_DATA_DIR"] = d2
                two = jsonstore._advisory_key(os.path.join(d2, "x", "y.json"))
            self.assertEqual(one, two,
                             "two instances would take two different locks")
        finally:
            if base is None:
                os.environ.pop("HUB_DATA_DIR", None)
            else:
                os.environ["HUB_DATA_DIR"] = base

    def test_key_fits_a_signed_64_bit_column(self):
        # Postgres takes a bigint. An id outside that range is an error at the
        # moment of locking, which is the moment it must not be.
        for n in range(64):
            k = jsonstore._advisory_key(f"some/path/{n}.json")
            self.assertIsInstance(k, int)
            self.assertGreaterEqual(k, -(2 ** 63))
            self.assertLess(k, 2 ** 63)

    def test_different_files_take_different_locks(self):
        a = jsonstore._advisory_key("one.json")
        b = jsonstore._advisory_key("two.json")
        self.assertNotEqual(a, b, "unrelated files would serialise on each other")


class NeverCostsTheWrite(unittest.TestCase):
    """Failing to lock is a reason to serialise less, never to refuse a save."""

    def _isolated(self):
        """A temporary data root, put back afterwards."""
        d = tempfile.TemporaryDirectory()
        base = os.environ.get("HUB_DATA_DIR")
        os.environ["HUB_DATA_DIR"] = d.name

        def restore():
            if base is None:
                os.environ.pop("HUB_DATA_DIR", None)
            else:
                os.environ["HUB_DATA_DIR"] = base
            d.cleanup()

        self.addCleanup(restore)
        return d.name

    def _with_lock(self, fake):
        real = jsonstore._take_pg_lock
        jsonstore._take_pg_lock = fake
        self.addCleanup(setattr, jsonstore, "_take_pg_lock", real)

    def test_a_lock_that_cannot_be_taken_still_saves(self):
        """The ordinary degraded path: no advisory lock, fall back."""
        d = self._isolated()
        self._with_lock(lambda _p: None)
        path = os.path.join(d, f"still-saves-{uuid.uuid4().hex}.json")
        self.addCleanup(jsonstore._forget, jsonstore.key_for(path))
        out = jsonstore.update_json(path, lambda c: (c or []) + [1], default=[])
        self.assertEqual(out, [1])
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), [1])

    def test_a_lock_that_RAISES_still_saves(self):
        """The one the first draft of this file did not actually test.

        It stubbed _take_pg_lock with something that raises, asserted that the
        STUB raised -- which tests the stub -- and then swapped it for one
        returning None before going anywhere near update_json. So the raising
        path was never executed. Run properly it propagated straight out of
        exclusive() and the caller lost a save it would have made without any
        of this, against this module's own rule that acquiring a lock never
        costs the write.
        """
        d = self._isolated()

        def boom(_path):
            raise RuntimeError("lock machinery broke")

        self._with_lock(boom)
        path = os.path.join(d, f"raises-{uuid.uuid4().hex}.json")
        self.addCleanup(jsonstore._forget, jsonstore.key_for(path))
        out = jsonstore.update_json(path, lambda c: (c or []) + [1], default=[])
        self.assertEqual(out, [1], "a lock fault cost the caller their save")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), [1])
        # Degraded, and said so rather than swallowed.
        self.assertEqual(jsonstore.status()["lock_backend"], "thread-only")
        self.assertIn("locking raised", jsonstore.status()["lock_error"])

    def test_lock_backend_is_reported(self):
        st = jsonstore.status()
        self.assertIn("lock_backend", st)
        self.assertIn(st["lock_backend"], {"postgres", "flock", "thread-only"})
        self.assertIn("lock_timeouts", st)


class AuthoritativeRead(unittest.TestCase):
    """The read half, and the one case where reading the mirror is wrong."""

    def setUp(self):
        self._base = os.environ.get("HUB_DATA_DIR")
        self._dir = tempfile.TemporaryDirectory()
        os.environ["HUB_DATA_DIR"] = self._dir.name

    def tearDown(self):
        if self._base is None:
            os.environ.pop("HUB_DATA_DIR", None)
        else:
            os.environ["HUB_DATA_DIR"] = self._base
        self._dir.cleanup()

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_mirror_wins_over_a_stale_local_file(self):
        name = f"stale-{uuid.uuid4().hex}.json"
        path = os.path.join(jsonstore.data_root(), name)
        self.addCleanup(jsonstore._forget, jsonstore.key_for(path))
        jsonstore.write_json(path, ["mirrored"])
        # Another instance's newer write, landing in the mirror only.
        jsonstore._upsert(jsonstore.key_for(path), json.dumps(["newer"]))
        # Make the local file look older than that mirror write.
        old = time.time() - 600
        os.utime(path, (old, old))
        got = jsonstore._authoritative(path, default=[])
        self.assertEqual(got, ["newer"],
                         "read the local copy and would have dropped the "
                         "other instance's update")

    def test_an_unmirrored_local_change_is_not_reverted(self):
        """The guard that keeps this from becoming a regression.

        A local file newer than our last successful mirror holds a change the
        database has not got -- the breaker was open, or the payload was over
        the size cap. Reading the mirror there would quietly revert it.
        """
        name = f"unmirrored-{uuid.uuid4().hex}.json"
        path = os.path.join(jsonstore.data_root(), name)
        self.addCleanup(jsonstore._forget, jsonstore.key_for(path))
        jsonstore.write_json(path, ["mirrored"])
        # The mirror goes down; the disk takes a change it never sees.
        until = jsonstore._breaker_until
        jsonstore._breaker_until = time.time() + 3600
        try:
            jsonstore.write_json(path, ["disk-only"])
            got = jsonstore._authoritative(path, default=[])
        finally:
            jsonstore._breaker_until = until
        self.assertEqual(got, ["disk-only"],
                         "reverted a local change the mirror had not got")

    def test_a_cache_is_read_from_disk(self):
        """durable=False has no mirror by definition."""
        name = f"cache-{uuid.uuid4().hex}.json"
        path = os.path.join(jsonstore.data_root(), name)
        jsonstore.write_json(path, ["cached"], durable=False)
        self.assertEqual(
            jsonstore._authoritative(path, default=[], durable=False),
            ["cached"])


class LockIsReleased(unittest.TestCase):
    """A session-scoped lock left held wedges whoever gets that connection.

    conn.close() only returns the connection to the POOL -- the session
    survives it, so the unlock has to be explicit. This is the one failure
    here that would not show up as a lost row: it shows up as the next caller
    hanging.
    """

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_nothing_is_still_held_afterwards(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.environ.get("HUB_DATA_DIR")
            os.environ["HUB_DATA_DIR"] = d
            try:
                path = os.path.join(d, f"held-{uuid.uuid4().hex}.json")
                for _ in range(jsonstore.LOCK_MAX_HELD + 3):
                    with jsonstore.exclusive(path):
                        pass
                key = jsonstore._advisory_key(path)
                from sqlalchemy import text
                with jsonstore._engine.connect() as cx:
                    held = cx.execute(text(
                        "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                        "AND ((classid::bigint << 32) | objid::bigint) = :k"),
                        {"k": key}).scalar()
                self.assertEqual(held or 0, 0, "advisory lock left held")
            finally:
                if base is None:
                    os.environ.pop("HUB_DATA_DIR", None)
                else:
                    os.environ["HUB_DATA_DIR"] = base

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_slots_are_given_back(self):
        """The semaphore bounds pool use; a leaked slot deadlocks the Hub."""
        with tempfile.TemporaryDirectory() as d:
            base = os.environ.get("HUB_DATA_DIR")
            os.environ["HUB_DATA_DIR"] = d
            try:
                path = os.path.join(d, f"slots-{uuid.uuid4().hex}.json")
                for _ in range(jsonstore.LOCK_MAX_HELD * 3):
                    with jsonstore.exclusive(path):
                        pass
                # Every slot free means all of them can be taken at once.
                taken = [jsonstore._lock_slots.acquire(timeout=5)
                         for _ in range(jsonstore.LOCK_MAX_HELD)]
                for got in taken:
                    if got:
                        jsonstore._lock_slots.release()
                self.assertTrue(all(taken), "a lock slot was never given back")
            finally:
                if base is None:
                    os.environ.pop("HUB_DATA_DIR", None)
                else:
                    os.environ["HUB_DATA_DIR"] = base


class ManyThreadsAtOnce(unittest.TestCase):
    """The pool cost of holding a connection for the length of the lock.

    Each holder now uses two connections -- one for the advisory lock and one
    for its own _upsert -- against a pool of 5 + 10 overflow. Unbounded, a
    burst of writers takes every connection for locks and then queues on
    pool_timeout waiting for one to do the write with, each holding a lock the
    others are behind. LOCK_MAX_HELD is what stops that.

    MEASURED, AND THE NUMBER IS THE ASSERTION. The first version of this
    check asserted only that every thread eventually finished -- and the
    unbounded build finished too, all 24 of them, in 120.3s against 0.7s
    bounded. It was a check that could not fail, on a 170x slowdown that
    would show in production as every write route stalling for seconds. So
    the ceiling is what is asserted, set far above the bounded time and far
    below the unbounded one, which is the only gap wide enough to be read as
    a verdict rather than a stopwatch on a busy runner.
    """

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_a_burst_of_writers_is_not_slowed_to_a_crawl(self):
        import threading
        threads_count = jsonstore.LOCK_MAX_HELD * 4 + 4
        with tempfile.TemporaryDirectory() as d:
            base = os.environ.get("HUB_DATA_DIR")
            os.environ["HUB_DATA_DIR"] = d
            names = [f"burst-{uuid.uuid4().hex}.json"
                     for _ in range(threads_count)]
            for n in names:
                self.addCleanup(jsonstore._forget, n)
            errors: list = []

            def work(name: str) -> None:
                try:
                    path = os.path.join(d, name)
                    for i in range(5):
                        jsonstore.update_json(
                            path, lambda cur, n=i: (cur or []) + [n],
                            default=[])
                except Exception as exc:                # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")

            try:
                threads = [threading.Thread(target=work, args=(n,))
                           for n in names]
                started = time.monotonic()
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=CEILING_SECONDS)
                elapsed = time.monotonic() - started
                stuck = [t for t in threads if t.is_alive()]
                self.assertEqual(
                    stuck, [],
                    f"{len(stuck)} of {threads_count} writers never finished "
                    f"-- the pool is exhausted by lock connections")
                self.assertEqual(errors, [])
                self.assertLess(
                    elapsed, CEILING_SECONDS,
                    f"{threads_count} writers took {elapsed:.1f}s, over the "
                    f"{CEILING_SECONDS}s ceiling -- measured at 0.7s with "
                    f"LOCK_MAX_HELD={jsonstore.LOCK_MAX_HELD} and 120.3s "
                    f"with the bound removed, so this is pool contention "
                    f"rather than a slow runner")
            finally:
                if base is None:
                    os.environ.pop("HUB_DATA_DIR", None)
                else:
                    os.environ["HUB_DATA_DIR"] = base


class DiagnosticsSaysSo(unittest.TestCase):
    """status() is a dict; /diagnostics is the screen somebody reads.

    The whole finding here is a lock that succeeds and means nothing, with
    nothing anywhere saying so -- so putting the state in status() and not on
    the panel would leave it exactly as invisible as it was.
    """

    def setUp(self):
        self._saved = (jsonstore._lock_backend, jsonstore._lock_timeouts,
                       set(jsonstore._unmirrored_keys))

    def tearDown(self):
        (jsonstore._lock_backend, jsonstore._lock_timeouts, keys) = self._saved
        jsonstore._unmirrored_keys.clear()
        jsonstore._unmirrored_keys.update(keys)

    def _detail(self):
        from hub import diagnostics
        return diagnostics.check_json_backup()

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_the_advisory_lock_reads_ok(self):
        jsonstore._lock_backend = "postgres"
        jsonstore._lock_timeouts = 0
        jsonstore._unmirrored_keys.clear()
        self.assertEqual(self._detail().state, "ok")

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_falling_back_to_the_flock_is_a_warning(self):
        jsonstore._lock_backend = "flock"
        jsonstore._lock_timeouts = 0
        jsonstore._unmirrored_keys.clear()
        got = self._detail()
        self.assertEqual(got.state, "warn")
        self.assertIn("two instances", got.detail)

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_a_lock_timeout_is_a_warning(self):
        jsonstore._lock_backend = "postgres"
        jsonstore._lock_timeouts = 2
        jsonstore._unmirrored_keys.clear()
        got = self._detail()
        self.assertEqual(got.state, "warn")
        self.assertIn("unserialised", got.detail)

    @unittest.skipUnless(ON_PG, SKIP_PG)
    def test_a_file_the_mirror_would_not_take_is_named(self):
        jsonstore._lock_backend = "postgres"
        jsonstore._lock_timeouts = 0
        jsonstore._unmirrored_keys.add("some/big-thing.json")
        got = self._detail()
        self.assertEqual(got.state, "warn")
        self.assertIn("NOT backed up", got.detail)
        # Named, not counted: a number alone cannot be acted on.
        self.assertIn("some/big-thing.json", got.detail)


if __name__ == "__main__":
    if not ON_PG:
        print(f"NOTE: {SKIP_PG} -- the cross-instance checks will be skipped.")
    unittest.main(verbosity=2)
