"""The integrity check that finds a `.first()` which cannot name one row.

    python3 test_unordered_first.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database.

## Why this file exists

SQL has no default order. `.first()` after a filter that matches two rows
returns whichever row the planner hands back, and the same call can answer
differently on Postgres as a table grows or a plan changes -- while SQLite,
which every local run and every test here uses, obliges by usually returning
the lowest rowid. `docs/claude/56` already records two members of that family
(a forward foreign key SQLite resolves lazily, a SELECT alias in HAVING), both
invisible in development by construction.

The check went in with three live findings:

  * `hub/industry.py` wrote a client's resolved industry onto the Image
    Picker's row for that name. `image_picker_clients.name` is not unique --
    only `slug` is -- so it wrote one row and left the other. Production
    carried `marco-island-rental` on "general" beside
    `marco-island-rental-2` on "tourism": one client, two answers, depending
    on which row a reader landed on.
  * `modules/sales_builder` read "has this revision been accepted" and then
    inserted, with nothing unique on (quote_id, revision), then read the
    acceptance back with `.first()`.
  * `modules/creative_studio/binder.py` picked a seed template out of four
    non-unique fields three times, so two assets built from one brief could
    start from different templates.

## What this file holds, and why the last part matters most

Each shape the check must catch, each look-alike it must not -- and **its own
coverage**. That last one is not decoration. This check's first three drafts
each reported a clean repository while reading almost none of it: one
understood `db.Column(...)` but not the bare `Column(...)` that `hub/users.py`
uses, so `User.email` read as unconstrained; one followed `filter_by` and
skipped every `.filter(Model.col == v)` in `modules/scans`; one read a row as
unused unless a field was read off it, so `return exact` looked like an
existence check. Every one of those produced an empty list, which is exactly
what a clean repository produces.

So "the repo is clean" is asserted here together with "and the check read all
of it", because the first claim is worth only as much as the second.
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1unordered_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["SECRET_KEY"] = "unordered-first-test"
os.environ["PANEL_PASSWORD"] = "unordered-first-pass"

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import integrity                                          # noqa: E402


def _run(files: dict):
    return integrity.unordered_first_findings(
        {rel: ast.parse(src) for rel, src in files.items()})


def findings(files: dict):
    """Drive the sweep over a handful of synthetic modules."""
    return [f for f in _run(files) if f["file"] != "hub/integrity.py"]


def coverage(files: dict):
    """The check's own report of what it could not read."""
    return [f for f in _run(files) if f["file"] == "hub/integrity.py"]




# Two spellings of a model, because reading only one of them is how two drafts
# of this check came to report a clean repository.
MODELS = '''
from hub.extensions import db
from sqlalchemy import Column, Integer, String, UniqueConstraint


class Gallery(db.Model):
    __tablename__ = "galleries"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200))
    slug = db.Column(db.String(200), unique=True)
    industry_key = db.Column(db.String(60))


class Person(db.Model):
    """Bare `Column(...)`, the way hub/users.py spells it."""
    __tablename__ = "people"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True)
    team = Column(String(60))


class Snapshot(db.Model):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("property_id", "week_start"),)
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer)
    week_start = db.Column(db.String(20))


class Finding(db.Model):
    """The column is `query`; the attribute deliberately is not -- the shape
    check_shadowed_model_query() exists for. UniqueConstraint names the
    COLUMN and filter_by() is written in the ATTRIBUTE."""
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("property_id", "query"),)
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer)
    search_query = db.Column("query", db.String(500))
'''


def caught(label, source, path="modules/probe.py", models=MODELS):
    check(label, [f["file"] for f in findings({"models.py": models, path: source})],
          [path])


def quiet(label, source, path="modules/probe.py", models=MODELS):
    check(label, findings({"models.py": models, path: source}), [])


# ===========================================================================
section("It finds the row it cannot name")
# ===========================================================================

caught("filter_by on a column with no unique key, and the row is kept", '''
from models import Gallery
def industry_of(name):
    row = Gallery.query.filter_by(name=name).first()
    return row.industry_key
''')

caught("...and the .filter(Model.col == v) spelling, which one draft skipped "
       "entirely and so read modules/scans as clean", '''
from models import Gallery
def industry_of(db, name):
    row = db.query(Gallery).filter(Gallery.name == name).first()
    return row.industry_key
''')

caught("a row kept only by being returned — no field is ever read off it, "
       "which one draft treated as an existence check", '''
from models import Gallery
def pick(name):
    row = Gallery.query.filter_by(name=name).first()
    if row is not None:
        return row
    return None
''')

caught("a model reached under an import alias", '''
from models import Gallery as Shelf
def pick(name):
    row = Shelf.query.filter_by(name=name).first()
    return row.industry_key
''')

caught("a query built into a variable on an earlier line", '''
from models import Gallery
def pick(name):
    q = Gallery.query.filter_by(name=name)
    row = q.first()
    return row.industry_key
''')

caught("part of a composite key is not the key — one column of two", '''
from models import Snapshot
def pick(pid):
    row = Snapshot.query.filter_by(property_id=pid).first()
    return row.week_start
''')

caught("a column selected off the model still names the model — reading the "
       "attribute instead named two call sites after `id`", '''
from models import Gallery
def pick(db, name):
    row = db.query(Gallery.industry_key).filter_by(name=name).first()
    return row.industry_key
''')


# ===========================================================================
section("...and stays quiet on what only looks like it")
# ===========================================================================

quiet("a unique column names one row", '''
from models import Gallery
def pick(slug):
    row = Gallery.query.filter_by(slug=slug).first()
    return row.industry_key
''')

quiet("so does a primary key", '''
from models import Gallery
def pick(pk):
    row = Gallery.query.filter_by(id=pk).first()
    return row.industry_key
''')

quiet("a bare Column(..., unique=True) is the same declaration as db.Column — "
      "reading only one spelling reported hub/users.py's sign-in lookup, "
      "which was right all along", '''
from models import Person
def sign_in(email):
    row = Person.query.filter_by(email=email).first()
    return row.team
''')

quiet("both columns of a composite key together name one row", '''
from models import Snapshot
def pick(pid, week):
    row = Snapshot.query.filter_by(property_id=pid, week_start=week).first()
    return row.id
''')

quiet("a UniqueConstraint naming the COLUMN covers the ATTRIBUTE that maps "
      "it — two words for one thing", '''
from models import Finding
def pick(pid, q):
    row = Finding.query.filter_by(property_id=pid, search_query=q).first()
    return row.id
''')

quiet("an existence check — any row answers it", '''
from models import Gallery
def has_one(name):
    if Gallery.query.filter_by(name=name).first():
        return True
    return False
''')

quiet("...including one spelled over two lines through a name, which is how "
      "three of modules/commercial_builder's reads are written", '''
from models import Gallery
def has_one(name):
    existing = Gallery.query.filter_by(name=name).first()
    if existing:
        return "already"
    return "no"
''')

quiet("an ordered read says which row it means", '''
from models import Gallery
def pick(name):
    row = (Gallery.query.filter_by(name=name)
           .order_by(Gallery.id).first())
    return row.industry_key
''')

quiet("...and the order_by may be on the variable the query was built into, "
      "three lines up — reporting that idiom is how a check gets scrolled "
      "past", '''
from models import Gallery
def pick(name):
    q = Gallery.query.filter_by(name=name).order_by(Gallery.id)
    row = q.first()
    return row.industry_key
''')

quiet("a test is not a call site", '''
from models import Gallery
def test_it():
    row = Gallery.query.filter_by(name="x").first()
    assert row.industry_key
''', path="test_probe.py")


# ===========================================================================
section("It reports what it could not read, as a finding of its own")
# ===========================================================================
# The part that matters most. An empty findings list means nothing without it,
# and three drafts of this check proved exactly that.

_unknown = coverage({"models.py": MODELS, "modules/p.py": '''
from somewhere.far.away import Mystery
def pick(name):
    row = Mystery.query.filter_by(name=name).first()
    return row.industry_key
'''})
check("a model it cannot resolve is named rather than dropped", len(_unknown), 1)
check("...and the entry says the check is reporting its own reach rather "
      "than a defect in the file it names",
      "reporting its own reach" in (_unknown[0]["fix"] if _unknown else ""))
check("...and says how many of how many it read",
      "of 1 `.first()` call sites" in (_unknown[0]["detail"] if _unknown else ""))

check("a model that declares no unique key at all is a coverage gap, not a "
      "clean answer — nothing can be proved about a filter on it",
      len(coverage({"models.py": '''
from hub.extensions import db
class Loose(db.Model):
    __tablename__ = "loose"
    name = db.Column(db.String(200))
''', "modules/q.py": '''
from models import Loose
def pick(name):
    row = Loose.query.filter_by(name=name).first()
    return row.name
'''})), 1)

check("a Core select() through a connection is out of scope, not unread — "
      "there is no model to carry a key",
      coverage({"models.py": MODELS, "modules/r.py": '''
from sqlalchemy import select
def pick(conn, table, key):
    row = conn.execute(select(table).where(table.c.id == key)).first()
    return row.name
'''}), [])

check("nothing unresolved means no coverage entry at all",
      coverage({"models.py": MODELS, "modules/s.py": '''
from models import Gallery
def pick(pk):
    row = Gallery.query.filter_by(id=pk).first()
    return row.industry_key
'''}), [])


# ===========================================================================
section("And on this repository")
# ===========================================================================

_live = integrity.check_unordered_first()
_gaps = [f for f in _live if f["file"] == "hub/integrity.py"]
_real = [f for f in _live if f["file"] != "hub/integrity.py"]

check("every `.first()` in the repo is resolved, ordered, an existence check "
      "or a Core query — the check reads all of it",
      [f["detail"] for f in _gaps], [])
check("...and with that established, none of them keeps a row it cannot name",
      [f["file"] for f in _real], [])
check("it is registered on /api/integrity",
      any(row[0] == "unordered_first" for row in integrity.CHECKS))
check("every exemption says why",
      all(str(v).strip() for v in integrity.UNORDERED_FIRST_EXEMPT.values()))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
