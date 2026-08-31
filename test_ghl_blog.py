"""A client's document, published to the agency's own blog.

    python3 test_ghl_blog.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one. Nothing here
reaches HighLevel: `_call` is stubbed, because what is worth asserting is what
this module does around it.

## Why this file exists

`hub/ghl_blog.py` publishes a client's llms.txt into Smart 1 Suite as a blog
post — a public URL on somebody's blog, which is as client-facing as anything
in this Hub gets. No test named it.

**companyId is not locationId**, and this is the third module to have made
that mistake. `hub/ghl_contacts.py` spends a whole section of its docstring on
it and `location_id()` refuses a value matching the company id;
`hub/suite_opportunity.py` was fixed for it, with a comment saying so. This
module still read `GHL_COMPANY_ID` and `SUITE_COMPANY_ID` as location
fallbacks — and on this deployment those hold the same value as the company
id, so **a client's document was published to the agency's own blog**, under
the agency's domain, titled with the client's name. It does not fail: the
agency location is a real location with a real blog, real authors and real
categories, so the post is created and a URL comes back.

**The duplicate guard was switched off by the failure it was written for.**
`slug_taken()` swallowed every error and answered `False`, which the caller
reads as *there is no post at that address*. The module's own docstring says a
missing scope "produces a 401 from HighLevel that looks like a bad token" —
and `blogs/check-slug.readonly` is precisely the scope whose absence made this
answer `False`, so a token missing it silently created the second post the
guard exists to refuse. `check_access()` did not test that scope either, so it
reported the token healthy.

**And the link was built from the slug we asked for.** HighLevel suffixes a
collision rather than refusing, and `urlSlug` came back on the response and was
never read — so the address handed to somebody as the published one pointed at
a page that is not there. A blog id naming nothing fell through to `blogs[0]`
the same way, and a domain field carrying its own scheme composed
`https://https://…`.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ghlblog_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "ghl-blog-test-secret"
for _k in ("GHL_BLOG_LOCATION_ID", "SMART1_LOCATION_ID",
           "GHL_COMPANY_ID", "SUITE_COMPANY_ID"):
    os.environ.pop(_k, None)
os.environ["GHL_PRIVATE_TOKEN"] = "pit-a-real-looking-token"

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


from hub import ghl_blog as GB                                # noqa: E402


def env(**kw):
    for k in ("GHL_BLOG_LOCATION_ID", "SMART1_LOCATION_ID",
              "GHL_COMPANY_ID", "SUITE_COMPANY_ID"):
        os.environ.pop(k, None)
    for k, v in kw.items():
        os.environ[k] = v


def publish(**kw):
    """Guarded: a regression must name itself rather than ending the run,
    which is the shape every test file here now uses."""
    try:
        return GB.publish_llms_txt(kw.pop("client", "Acme Tyre"),
                                   kw.pop("text", "# Acme Tyre\nWe fit tyres."),
                                   **kw)
    except GB.BlogError as exc:
        return {"refused": str(exc)}
    except Exception as exc:                                  # noqa: BLE001
        return {"raised": f"{type(exc).__name__}: {exc}"}


# =====================================================================
section("companyId is not locationId")
# =====================================================================
# Two guards on purpose, because this codebase has made the mistake three
# times: the company spellings are not in the list that answers, AND a value
# matching the company id is refused whichever name it came in under. Either
# alone closes today's case; the second is what covers a company id arriving
# under a name nobody thought to exclude.
check("no company spelling can answer as a location",
      [n for n in GB.LOCATION_ENV if "COMPANY" in n], [])
check("and the company names are read under their own list",
      sorted(GB.COMPANY_ENV), ["GHL_COMPANY_ID", "SUITE_COMPANY_ID"])

env(GHL_COMPANY_ID="AGENCY-COMPANY-1")
check("a company id alone is not a location", GB._location(), "")
check("and the refusal says which id is wanted",
      "not the agency company id" in GB._location_problem(), True)

env(SUITE_COMPANY_ID="AGENCY-COMPANY-1")
check("nor under the other company spelling", GB._location(), "")

# On this deployment the two hold the same value, which is the whole reason
# the fallback was invisible: it resolved to something that works.
env(GHL_BLOG_LOCATION_ID="AGENCY-COMPANY-1", GHL_COMPANY_ID="AGENCY-COMPANY-1")
check("a location set to the company id is refused", GB._location(), "")
check("by name, so it is not read as 'nothing is set'",
      "same value as the agency company id" in GB._location_problem(), True)

env(GHL_BLOG_LOCATION_ID="SUBACCOUNT-9", GHL_COMPANY_ID="AGENCY-COMPANY-1")
check("a real sub-account is used", GB._location(), "SUBACCOUNT-9")
check("and nothing is reported wrong with it", GB._location_problem(), "")
check("Render's literal quotes are stripped, as everywhere else here",
      (env(GHL_BLOG_LOCATION_ID='"SUBACCOUNT-9"'), GB._location())[1],
      "SUBACCOUNT-9")

env(GHL_COMPANY_ID="AGENCY-COMPANY-1")
out = publish()
check("publishing with no location refuses rather than reaching the agency",
      "sub-account" in out.get("refused", ""), True)
check("and nothing was sent", "raised" in out, False)


# =====================================================================
section("The blog it publishes to is the one that was named")
# =====================================================================
env(GHL_BLOG_LOCATION_ID="SUBACCOUNT-9", GHL_COMPANY_ID="AGENCY-COMPANY-1")

BLOGS = [{"_id": "blogA", "name": "News", "domain": "news.acme-tyre.com"},
         {"_id": "blogB", "name": "Guides", "domain": "guides.acme-tyre.com"}]
SENT = {}


def api(method, path, **kw):
    SENT.update({"method": method, "path": path, "params": kw.get("params") or {},
                 "body": kw.get("json") or {}})
    if path == "/blogs/site/all":
        return {"data": BLOGS}
    if path == "/blogs/authors":
        return {"authors": [{"_id": "author-1"}]}
    if path == "/blogs/categories":
        return {"categories": [{"_id": "cat-1"}]}
    if path == "/blogs/posts/url-slug-exists":
        return {"exists": SENT.get("_taken", False)}
    return {"data": {"_id": "post-1", "urlSlug": SENT.get("_assigned",
                                                          "llm-text-acme-tyre")}}


GB._call = api

out = publish(blog_id="blogB")
check("a named blog is the one used", out.get("blog_id"), "blogB")
out = publish(blog_id="blogTYPO")
check("a blog id that names nothing is refused",
      out.get("refused", "").startswith("No blog with that id"), True)
check("and the refusal says what is there",
      "News" in out.get("refused", ""), True)
out = publish()
check("naming none still takes the first, as it always did",
      out.get("blog_id"), "blogA")


# =====================================================================
section("The address handed back is the one Suite assigned")
# =====================================================================
SENT["_assigned"] = "llm-text-acme-tyre-2"
out = publish()
check("the slug is read off the response", out.get("slug"),
      "llm-text-acme-tyre-2")
check("and the URL is built from it",
      out.get("url"), "https://news.acme-tyre.com/llm-text-acme-tyre-2")
check("what was asked for is kept beside it",
      out.get("requested_slug"), "llm-text-acme-tyre")
check("and the difference is said out loud",
      "rather than" in out.get("note", ""), True)

SENT["_assigned"] = "llm-text-acme-tyre"
out = publish()
check("an unsuffixed publish says nothing about it",
      "rather than" in out.get("note", ""), False)

# A domain carrying its own scheme composed https://https://…, which is a
# dead link presented as the published address.
BLOGS[0]["domain"] = "https://news.acme-tyre.com/"
out = publish()
check("a domain with a scheme is not doubled",
      out.get("url"), "https://news.acme-tyre.com/llm-text-acme-tyre")
BLOGS[0]["domain"] = ""
out = publish()
check("no domain is no URL", out.get("url"), "")
check("and it says why", "no custom domain" in out.get("note", ""), True)
BLOGS[0]["domain"] = "news.acme-tyre.com"


# =====================================================================
section("Not measured is not the same answer as no")
# =====================================================================
# `blogs/check-slug.readonly` failing is indistinguishable from a bad token,
# which is what this module's own docstring says -- and it was the failure
# that silently disabled the guard against publishing a second file about one
# client.

SENT["_taken"] = True
out = publish()
check("a post already at that address is refused",
      out.get("refused", "").startswith("A post already exists"), True)

SENT["_taken"] = False
out = publish()
check("a free address publishes", out.get("ok"), True)
check("and records that the check ran", out.get("slug_checked"), True)
check("saying nothing about it", "could not be made" in out.get("note", ""),
      False)

_ok_call = GB._call


def no_slug_scope(method, path, **kw):
    if path == "/blogs/posts/url-slug-exists":
        raise GB.BlogError("Smart 1 Suite rejected the request (401).")
    return _ok_call(method, path, **kw)


GB._call = no_slug_scope
check("the check itself answers 'we could not look'",
      GB.slug_taken("llm-text-acme-tyre", "SUBACCOUNT-9"), None)
out = publish()
check("the publish still goes ahead", out.get("ok"), True)
check("but does not claim the check passed", out.get("slug_checked"), False)
check("and says so, naming the scope that would close it",
      "blogs/check-slug.readonly" in out.get("note", ""), True)

# Updating a known post asks nothing about slugs: the post id says which one.
out = publish(post_id="post-1")
check("an update needs no slug check", out.get("slug_checked"), False)
check("and does not warn about a duplicate it cannot create",
      "second" in out.get("note", ""), False)
GB._call = _ok_call


# =====================================================================
section("check_access names the scope that fails")
# =====================================================================
acc = GB.check_access()
check("all four read scopes are tested", len(acc.get("checks") or {}), 4)
check("the slug check among them",
      "blogs/check-slug.readonly" in (acc.get("checks") or {}), True)
check("a healthy token reads healthy", acc.get("ok"), True)

GB._call = no_slug_scope
acc = GB.check_access()
check("a token missing only the slug scope is not reported healthy",
      acc.get("ok"), False)
slugchk = (acc.get("checks") or {}).get("blogs/check-slug.readonly") or {}
check("and it is named", slugchk.get("ok"), False)
check("with what it costs rather than a status code",
      "duplicate post" in str(slugchk.get("detail", "")), True)
check("the three that do work still read as working",
      [(acc.get("checks") or {}).get(k, {}).get("ok") for k in
       ("blogs/list.readonly", "blogs/author.readonly",
        "blogs/category.readonly")], [True, True, True])
GB._call = _ok_call

env(GHL_BLOG_LOCATION_ID="AGENCY-COMPANY-1", GHL_COMPANY_ID="AGENCY-COMPANY-1")
acc = GB.check_access()
check("a location that is really the company id is a reported problem",
      acc.get("ok"), False)
check("named, rather than 'set the variables'",
      "agency" in acc.get("problem", ""), True)
env(GHL_BLOG_LOCATION_ID="SUBACCOUNT-9", GHL_COMPANY_ID="AGENCY-COMPANY-1")


# =====================================================================
section("Nothing carries the token, and the work is attributable")
# =====================================================================
# BlogError's own docstring: "Message is safe to show a user -- never contains
# the token." A 401 body from HighLevel has carried token fragments, and this
# response reaches a browser.
_tok = os.environ["GHL_PRIVATE_TOKEN"]


def refuses(method, path, **kw):
    raise GB.BlogError("Smart 1 Suite rejected the request (401). The Private "
                       "Integration Token is probably missing a blogs scope.")


GB._call = refuses
env(GHL_BLOG_LOCATION_ID="SUBACCOUNT-9")
acc = GB.check_access()
blob = repr(acc) + repr(publish())
check("no refusal carries the token", _tok in blob, False)
check("nor any fragment of it", _tok[4:20] in blob, False)
GB._call = _ok_call

# The work log has to be able to name the module a row was filed under, or a
# client who had this published for them reads as one nobody worked for --
# the display_ads failure this codebase has now counted several times.
from hub import client_brand                                  # noqa: E402
check("`suite` is a name the client record can read",
      "suite" in client_brand.WORK_KINDS, True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
