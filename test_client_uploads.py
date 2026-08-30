"""The client upload link, and the client an insertion order brings into being.

    python3 test_client_uploads.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

**The link existed and nobody could reach it.** Client Image Uploads has
always been able to hand a client a page they upload their own photographs
through. Getting one meant opening that tool, finding or adding the client and
copying a link out of a row — so the two screens that actually need it, the
client's own record and an insertion order that has just said "creative is
being supplied", offered nothing, and the assets arrived by email instead.

The matching rules are the expensive half. A link that collects one client's
photographs into **another client's gallery** is worse than no link at all, so
a gallery matches on the derived client key or an exactly normalised name and
on nothing else, two candidates propose neither, and creating one is a thing
somebody pressed rather than something a page load did.

**And a client whose only trace is an IO was invisible.** Client 360 reads
Knack's products and website records; a business written up on their first
insertion order has neither until the campaign is set up. So the day their
record is most worth opening it came back empty — which reads exactly like a
name typed wrong. `hub/io_clients.py` registers them at submit, and only when
they resolve to nobody, so it can never shadow a real client.

The trap in that, which this file exists to keep shut: those rows are merged
into the client registry, so the check for "is this client already known
elsewhere?" can read *its own output* as proof somebody else knew them. The
second order for that client would then be dropped — and only once the
registry's two-minute per-process cache had refreshed, so it would pass in a
test, work on one gunicorn worker and fail on the other.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1upl_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "client-uploads-test-secret"
os.environ["PANEL_PASSWORD"] = "client-uploads-test-password"

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from wsgi import application                                  # noqa: E402
from werkzeug.test import Client as WClient                   # noqa: E402

http = WClient(application)
http.post("/login", data={"password": os.environ["PANEL_PASSWORD"]})

C360 = (ROOT / "hub" / "templates" / "client360.html").read_text()
IOHTML = (ROOT / "modules" / "io_builder" / "templates" / "index.html").read_text()
IOAPP = (ROOT / "modules" / "io_builder" / "app.py").read_text()


def link(name, url="", create=False, path="/tools/image-picker/api/clients/for-hub-client"):
    body = {"name": name, "client": name, "url": url, "create": create}
    return http.post(path, json=body).get_json()


# =====================================================================
section("One gallery, or none — never a guess")
# =====================================================================

first = link("Icon Solar", "iconsolar.com")
check("asking does not create one", first["share_url"], "")
check("and says so rather than erroring", first["ok"], True)
check("the page is told it may create one", first["can_create"], True)

made = link("Icon Solar", "iconsolar.com", create=True)
check("creating gives a link", bool(made["share_url"]), True)
check("and says it created it", made["created"], True)
check("the link is the client-facing picker page",
      "/tools/image-picker/pick/" in made["share_url"], True)

again = link("Icon Solar", "iconsolar.com", create=True)
check("asking twice does not make a second gallery", again["created"], False)
check("and hands back the same link", again["share_url"], made["share_url"])
check("saying how it matched", again["matched_on"] in ("client key", "exact name"), True)

# The rule hub/client_key.py argues at length. Attributing one company's
# uploads to another is the worst outcome available to this module.
supply = link("Icon Solar Supply", create=False)
check("a longer name is not this client", supply["share_url"], "")
check("it is offered its own gallery instead", supply["can_create"], True)

check("an empty name is refused, not filed somewhere",
      link("")["ok"], False)


# =====================================================================
section("The link is absolute, and carries no mount with it")
# =====================================================================

# A dispatcher-mounted module's request root carries its own prefix, so
# pasting the picker's path onto it builds /tools/io/tools/image-picker/… —
# a 404 the client meets and nobody else does.
from modules.image_picker import provisioning                 # noqa: E402

_origin = provisioning._origin
check("a mounted module's root is trimmed to the origin",
      _origin("https://smart1-hub.onrender.com/tools/io/"),
      "https://smart1-hub.onrender.com")
check("a bare origin is left alone",
      _origin("https://smart1-hub.onrender.com"), "https://smart1-hub.onrender.com")
# PUBLIC_BASE_URL is documented as an origin, and one env group here has held
# a callback URL in it before now.
check("a path in PUBLIC_BASE_URL cannot reach the link",
      _origin("https://h.com/tools/ads/oauth/callback"), "https://h.com")
check("nothing in, nothing out", _origin(""), "")

io_side = link("Icon Solar", path="/tools/io/api/client-upload-link")
check("the IO builder answers with the same link", io_side["share_url"], made["share_url"])
check("and it does not carry the IO mount",
      "/tools/io/tools/" in io_side["share_url"], False)


# =====================================================================
section("It is offered where somebody needs it")
# =====================================================================

check("on the Client 360 images card", 'id="c-img-uplink"' in C360, True)
check("which asks before it creates", "clientUploadLink(name, web0)" in C360, True)
check("in the IO wizard's creative checklist", 'id="uploadLinkBtn"' in IOHTML, True)
check("above the checklist it belongs to",
      IOHTML.index('id="uploadLinkBox"') < IOHTML.index('id="creativeChecklist"'), True)
check("carried on the order so every document agrees",
      "clientUploadUrl" in IOHTML, True)
# Both PDFs and the webhook, from one helper — three descriptions of one
# address is how the client PDF and the internal one come to disagree.
check("both documents read one helper", IOAPP.count("_upload_link_for(") >= 3, True)
check("and the Suite payload carries it", '"client_upload_url"' in IOAPP, True)
# Building a PDF must never create a gallery: the client and internal
# documents are generated repeatedly, often twice in a row.
check("a document only ever reads", "create=False" in IOAPP, True)

# navigator.clipboard is absent on http and is allowed to refuse.
for label, src in (("Client 360", C360), ("the IO wizard", IOHTML)):
    check(f"the copy button on {label} does not lie about copying",
          "execCommand" in src and "Ctrl-C" in src, True)


# =====================================================================
section("A new IO registers a client nobody has a record of")
# =====================================================================

from hub import io_clients, knack_data, clients_registry      # noqa: E402

out = io_clients.register_from_io({
    "client": "Brand New Roofing", "url": "https://brandnewroofing.com",
    "orderNumber": "IO-441", "salesContact": "Todd",
    "clientContactEmail": "someone@brandnewroofing.com"})
check("a client nobody knows is registered", out["registered"], True)
check("the order is recorded on the row", out["client"]["orders"], ["IO-441"])
check("with the website the order carried", out["client"]["domain"], "brandnewroofing.com")
check("and it says where the row came from", out["client"]["source"], "io")
check("a contact we can actually reach is kept",
      out["client"]["contact"].get("email"), "someone@brandnewroofing.com")

hits = knack_data.search_client("Brand New Roofing")
check("Client 360 now finds them", [h["client"] for h in hits], ["Brand New Roofing"])
check("marked as having only an IO behind them", hits[0]["io_only"], True)
check("with the order named", hits[0]["io_orders"], ["IO-441"])
check("and the website carried onto the record",
      [w["domain"] for w in hits[0]["websites"]], ["brandnewroofing.com"])
# Every card below is going to be empty; the banner is what stops that reading
# as a failed search.
check("the record says why it is empty", "registered from an insertion order" in C360, True)

rows = [r for r in clients_registry.all_clients(refresh=True)
        if r["name"] == "Brand New Roofing"]
check("the registry lists them once", len(rows), 1)
check("labeled, so nothing reads a quote as a confirmed client",
      (rows[0]["source"], rows[0]["is_io_only"]), ("io", True))
# An earlier version of the discovered-URL merge reused house_clients() for
# this job and quietly relabelled real Knack clients as ours.
check("and never as one of ours", rows[0]["is_house"], False)


# =====================================================================
section("It never registers a client we already have")
# =====================================================================

# The registry cache is warm now, exactly as it is in production — which is
# when the self-detection bug bites.
check("the row we just wrote is not proof somebody else knew them",
      io_clients.known_elsewhere("Brand New Roofing", ""), (False, ""))

third = io_clients.register_from_io({"client": "Brand New Roofing",
                                     "orderNumber": "IO-443"})
check("a second order lands on the same row", third["registered"], True)
check("it is not a new client", third["new"], False)
check("and both orders are on it", third["client"]["orders"], ["IO-441", "IO-443"])
check("still exactly one row", len(io_clients.overlay()), 1)

# A name that merely contains a registered one is a different company.
other = io_clients.register_from_io({"client": "Brand New Roofing Supply",
                                     "orderNumber": "IO-444"})
check("a longer name is registered in its own right", other["registered"], True)
check("so there are two rows, not one", len(io_clients.overlay()), 2)

# A client the Hub already knows writes nothing at all.
_real_find = clients_registry.find_client
clients_registry.find_client = lambda n: ({"name": n, "is_io_only": False}
                                          if n == "Established Co" else None)
try:
    known = io_clients.register("Established Co", "established.com", order="IO-500")
finally:
    clients_registry.find_client = _real_find
check("an existing client is not duplicated", known["registered"], False)
check("and the answer says where they already are",
      known["known_in"], "the client registry")
check("nothing was written for them", "established co" in io_clients.overlay(), False)

# "We could not look" must never read as "nobody has them" — that mistake
# invents a duplicate of a client Knack holds and cannot be undone by
# deleting a row.
def _boom(_n):
    raise RuntimeError("Knack timed out")


clients_registry.find_client = _boom
try:
    blind = io_clients.known_elsewhere("Someone Entirely New", "")
finally:
    clients_registry.find_client = _real_find
check("an unreadable registry counts as known", blind[0], True)
check("and says that is what happened", "could not be read" in blind[1], True)

check("an order with no client name is refused by name",
      io_clients.register("", order="IO-9")["reason"], "no name")

# Nothing here is written to Knack: it is an overlay, and it is removable.
check("a row can be dropped again", io_clients.forget("Brand New Roofing Supply"), True)
check("and dropping one that is not there is not an error",
      io_clients.forget("Nobody At All"), False)

# Submitting must never fail over its own bookkeeping. Asked of the code
# rather than of a line of it: this used to match the comment beside the call,
# so moving the call one function along broke the check while the property it
# is about held perfectly well — the "prose is not a call site" rule, running
# the other way.
import ast as _ast


def _register_is_guarded(src: str) -> bool:
    tree = _ast.parse(src)
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Try):
            continue
        calls = [n for n in _ast.walk(node)
                 if isinstance(n, _ast.Call)
                 and isinstance(n.func, _ast.Attribute)
                 and n.func.attr == "register_from_io"]
        if not calls:
            continue
        # Every handler must swallow: a bare `raise` or a re-raise would put
        # the bookkeeping back in front of the submit.
        for handler in node.handlers:
            if any(isinstance(n, _ast.Raise) for n in _ast.walk(handler)):
                return False
        if node.handlers:
            return True
    return False


check("the IO submit path cannot be broken by this",
      _register_is_guarded(IOAPP), True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
