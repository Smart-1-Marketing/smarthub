"""The acceptance criterion for taking this service off the Render disk.

Stated at the start of that work, in these words:

    with HUB_DATA_DIR pointed at an empty temp dir on every boot, the full
    test suite passes and no saved data is lost across two restarts.

Every store moved so far has its own test -- the activity log, SmartForecast,
the leads, the Google tokens, the delivery receipts. Each proves its own half.
What none of them proves is the sentence above, because that is a claim about
the *composed application* across a restart, and four suites passing
separately is an inference from four pieces rather than an answer.

This file is the answer. It boots the real `wsgi.application` twice, in two
fresh interpreters, with a BRAND NEW EMPTY data directory each time and one
database shared between them, writes real records through the real store APIs
in the first boot and reads them back in the second.

Two things make it mean something:

**Separate processes, not separate function calls.** The failure this guards
against is a store that works because its rows are still in a module-level
cache, or an engine bound at import. In one process, a "restart" that is
really a re-import proves nothing -- the caches are warm and the second read
never touches the backend. Each boot here is `subprocess.run([sys.executable,
...])`, so nothing but the database crosses the gap.

**The second directory is new, not emptied.** Emptying the first would leave
its inode, its permissions and anything a test had already opened. A fresh
mkdtemp is what a redeployed container actually gets.

What it deliberately does NOT assert: that the disk can be detached. That is
an operational decision with a one-way door -- a detached Render disk takes
its contents with it -- and the safe order is to point HUB_DATA_DIR off the
disk first, leave the disk mounted and untouched, and revert by unsetting one
variable if anything here turns out to be incomplete.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))

# The database has to OUTLIVE both data directories, so it cannot live inside
# either one -- a SQLite file under HUB_DATA_DIR would be wiped by the very
# thing being tested, and the failure would look like data loss when it was
# the fixture eating itself. CI passes a real Postgres.
_DB_HOME = tempfile.mkdtemp(prefix="s1-restart-db-")
DATABASE_URL = (os.environ.get("DISK_FREE_TEST_DATABASE_URL")
                or "sqlite:///" + os.path.join(_DB_HOME, "shared.sqlite3"))

MARKER = "restart-probe-" + os.urandom(4).hex()

# Written in boot one, read back in boot two. `json.dumps` of the result is the
# only channel between the two processes, so an exception inside a boot comes
# back as a readable failure rather than an empty string.
BOOT = r'''
import json, os, sys
sys.path.insert(0, %(repo)r)
out = {"ok": False}
try:
    action = sys.argv[1]
    marker = sys.argv[2]
    from hub import audit, jsonstore, leads

    if action == "write":
        audit.log("disk_free_probe", marker, actor="restart-test",
                  client="Restart Probe")
        lead = leads.capture("disk_free_probe", marker,
                             {"email": "probe@example.com", "name": marker})
        jsonstore.write_json(os.path.join(jsonstore.data_dir("disk_free_probe"),
                                          "probe.json"),
                             {"marker": marker})
        out["lead_id"] = lead.get("id", "")
        out["ok"] = True

    elif action == "read":
        lead_id = sys.argv[3]
        # The directory this boot was handed must have started empty, or the
        # read below could be satisfied by a file the previous boot left and
        # the test would pass while proving nothing about the database.
        root = jsonstore.data_root()
        out["dir_started_empty"] = sys.argv[4] == "empty"

        rows = audit.read(limit=200, module="disk_free_probe")
        out["audit_found"] = any(r.get("type") == marker for r in rows)

        got = leads.get(lead_id) or {}
        out["lead_found"] = bool(got) and got.get("page") == marker

        blob = jsonstore.read_json(
            os.path.join(jsonstore.data_dir("disk_free_probe"), "probe.json"),
            default=None)
        out["blob_restored"] = bool(blob) and blob.get("marker") == marker
        out["ok"] = True
except Exception as exc:                      # noqa: BLE001
    out["error"] = f"{type(exc).__name__}: {exc}"
print("RESULT:" + json.dumps(out))
''' % {"repo": REPO}


def _boot(data_dir, *args):
    """One interpreter, one data directory, the shared database."""
    env = dict(os.environ)
    env["HUB_DATA_DIR"] = data_dir
    env["DATABASE_URL"] = DATABASE_URL
    env.setdefault("SECRET_KEY", "fixture-only")
    # Owned rather than inherited: a developer's real credentials would send
    # these writes somewhere other than the fixture.
    for name in ("CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME",
                 "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"):
        env.pop(name, None)
    proc = subprocess.run([sys.executable, "-c", BOOT, *args],
                          capture_output=True, text=True, timeout=180,
                          cwd=REPO, env=env)
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise AssertionError(
        "the boot produced no result line.\n"
        f"stdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}")


class NothingIsLostAcrossTwoRestarts(unittest.TestCase):
    """The criterion itself, driven end to end."""

    @classmethod
    def setUpClass(cls):
        cls.first = tempfile.mkdtemp(prefix="s1-restart-a-")
        cls.second = tempfile.mkdtemp(prefix="s1-restart-b-")
        cls.wrote = _boot(cls.first, "write", MARKER)
        if not cls.wrote.get("ok"):
            raise AssertionError(f"the first boot failed: {cls.wrote}")
        # Measured, not assumed: if the second directory is not empty the
        # reads below could be answered from a file rather than the database.
        empty = "empty" if not os.listdir(cls.second) else "not-empty"
        cls.read = _boot(cls.second, "read", MARKER,
                         cls.wrote.get("lead_id", ""), empty)
        if not cls.read.get("ok"):
            raise AssertionError(f"the second boot failed: {cls.read}")

    @classmethod
    def tearDownClass(cls):
        for d in (cls.first, cls.second, _DB_HOME):
            shutil.rmtree(d, ignore_errors=True)

    def test_the_second_boot_started_with_an_empty_directory(self):
        """Guard the premise before trusting anything it produced."""
        self.assertTrue(
            self.read.get("dir_started_empty"),
            "The second boot was handed a directory that already had files in "
            "it, so nothing below proves the database carried the data.")

    def test_the_two_boots_used_different_directories(self):
        self.assertNotEqual(self.first, self.second)

    def test_the_activity_log_survives(self):
        self.assertTrue(self.read.get("audit_found"),
                        "An activity entry written before the restart is not "
                        "readable after it.")

    def test_a_captured_lead_survives(self):
        self.assertTrue(self.read.get("lead_found"),
                        "A lead captured before the restart is not readable "
                        "after it.")

    def test_a_mirrored_json_file_is_restored(self):
        """The mirror's whole purpose: the file is gone, the row is not."""
        self.assertTrue(self.read.get("blob_restored"),
                        "A JSON file written before the restart was not "
                        "restored from the database into the new directory.")


if __name__ == "__main__":
    unittest.main(verbosity=1)
