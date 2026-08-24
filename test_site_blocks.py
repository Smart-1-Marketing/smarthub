"""Website Blocks — the guarantees that make the output safe to paste.

Run with `python3 test_site_blocks.py`. No pytest, no new dependencies, and a
temporary data directory so nothing here touches /var/data or the real one.

The three things worth asserting are the three that would fail silently:

1. **Nothing escapes the block.** The output is pasted into an existing page.
   A bare `section {}` or `h2 {}` rule would restyle smart1marketing.com and
   the block itself would still look perfect in preview — which is exactly how
   nobody would notice until the site was already wrong.
2. **A link cannot carry script.** `javascript:` in a button href produces a
   working-looking button on the public site.
3. **Every block type actually renders.** A block that returns "" is an empty
   `<section>` on a live page: no error, no content, and a preview that looks
   like the page just has a gap in it.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="s1-site-blocks-")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.site_blocks import blocks as B    # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not ok:
        FAILURES.append(what + (f" — {detail}" if detail else ""))


def head(title: str) -> None:
    print(f"\n--- {title}")


# ---------------------------------------------------------------- 1. scoping
head("Every CSS rule is scoped to a block")

# Strip the media queries' own braces so the selector scan sees the rules
# inside them, then take everything left of each '{'.
css = B._CSS
# `[^\n]` matters: \s in the leading class would let a selector start on the
# previous line's newline, which quietly swallowed the @media rules.
selector_lists = re.findall(r"(?m)^[ \t]*([^{}@/\s][^{}\n]*?)[ \t]*\{", css)
check(len(selector_lists) > 40, "the stylesheet has rules to check",
      f"found {len(selector_lists)}")

unscoped = []
for sel_list in selector_lists:
    for sel in sel_list.split(","):
        sel = sel.strip()
        if not sel:
            continue
        # Every selector must start at a block: either the wrapper itself or
        # one of our own prefixed classes.
        if not (sel.startswith(".s1blk") or sel.startswith(".s1-")):
            unscoped.append(sel)
check(not unscoped, "no rule can match an element outside a block",
      "; ".join(unscoped[:6]))

check(":root" not in css and ":root" not in B.theme_vars("smart1"),
      "the palette is not defined on :root",
      "a :root block would rewrite variables the host site uses")
check(not re.search(r"(?m)^\s*(body|html)\s*\{", css),
      "no body or html rule")

# Scoping stops the block reaching the host page. Nothing stops the host page
# reaching the block, so the wrapper has to neutralise the declarations a site
# most often puts on a bare `section` — that is what the guard below is for.
wrapper = re.search(r"(?m)^\.s1blk\{(.*?)\}", css, re.S).group(1)
for decl in ("border:0", "margin:0", "text-align:left", "letter-spacing:normal"):
    check(decl in wrapper, f"the wrapper neutralises the host's {decl}",
          "a host rule on bare `section` would otherwise reach into the block")

check("font-family:inherit" in re.search(r"\.s1blk,\.s1blk \*\{([^}]*)\}",
                                         css).group(1),
      "every element in the block inherits the block's font",
      "a host rule on bare h2 beats inheritance — headings came out in the "
      "host's font while the body copy stayed Montserrat")

# Anything sized only by inheritance is reachable by a host rule on the bare
# element, so every paragraph and heading class must carry its own size.
for name in ("s1-lede", "s1-tm", "s1-disc", "s1-who", "s1-price-note",
             "s1-frame-note", "s1-role", "s1-by"):
    rule = re.search(r"\.%s\{([^}]*)\}" % name, css.replace("\n", ""))
    check(rule is not None and "font-size" in rule.group(1),
          f".{name} sets its own font-size")

sheet = B.stylesheet("smart1")
check(sheet.startswith("<style>") and sheet.rstrip().endswith("</style>"),
      "the stylesheet is one style element")
check(B.FONT_IMPORT in sheet, "Montserrat is imported with the block")

for key in B.THEMES:
    v = B.theme_vars(key)
    check(v.startswith(".s1blk{") and v.endswith("}"),
          f"theme {key} sets its variables on .s1blk")
    check(len(re.findall(r"--s1-[a-z0-9-]+:", v)) >= 17,
          f"theme {key} defines the whole palette")


# ------------------------------------------------------- 2. every block draws
head("Every block type renders, from its own sample")

for key in B.BLOCK_ORDER:
    check(key in B.BLOCKS, f"{key} is in BLOCKS")
    block = B.new_block(key)
    html = B.render_block(block, "smart1")
    check(f'class="s1blk s1blk-{key}"' in html, f"{key} renders its section")
    check(html.count("<section") == 1 and html.count("</section>") == 1,
          f"{key} renders exactly one section")
    check(len(B.render_block(block, "smart1", with_css=False)) > 120,
          f"{key} renders content, not an empty shell")

check(sorted(B.BLOCK_ORDER) == sorted(B.BLOCKS),
      "BLOCK_ORDER lists every block type exactly once",
      "a type missing from the order is invisible in the builder")

# Every field a block declares must be one the sample fills in, or the builder
# draws an input that the renderer then ignores.
for key, spec in B.BLOCKS.items():
    declared = {f["k"] for f in spec["fields"]}
    sample = set(spec["sample"])
    check(declared == sample, f"{key}'s sample covers exactly its fields",
          f"only in fields: {sorted(declared - sample)}; "
          f"only in sample: {sorted(sample - declared)}")


# -------------------------------------------------------------- 3. escaping
head("Copy is escaped and links cannot carry script")

nasty = B.new_block("hero")
nasty["title"] = 'Ohio <script>alert("x")</script> & Sons'
nasty["accent"] = ""
nasty["cta_href"] = "javascript:alert(1)"
nasty["cta2_href"] = "smart1marketing.com/free-consultation"
out = B.render_block(nasty, "smart1")
check("<script>" not in out, "a script tag in the copy is escaped")
check("&lt;script&gt;" in out, "…and printed as text")
check("&amp; Sons" in out, "an ampersand in the copy is escaped")
check("javascript:" not in out, "a javascript: link is refused")
check('href="#"' in out, "…and replaced with a dead anchor")
check("https://smart1marketing.com/free-consultation" in out,
      "a bare domain is given a scheme")

for good in ("#plan", "/land/hvac/", "https://x.com/a", "mailto:a@b.com",
             "tel:+16145360768"):
    check(B._href(good) != "#", f"{good} survives as a link")
for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x",
            "vbscript:x"):
    check(B._href(bad) == "#", f"{bad} is refused")

# The highlight has to match after escaping or a headline with an apostrophe
# would silently lose its accent phrase — and most house headlines have one.
apos = B.new_block("hero")
apos["title"] = "We don't play guessing games with your budget."
apos["accent"] = "don't play guessing games"
check('class="s1-hl"' in B.render_block(apos, "smart1"),
      "a highlight containing an apostrophe still matches")

missing = B.new_block("hero")
missing["accent"] = "a phrase that is not in the headline"
check('class="s1-hl"' not in B.render_block(missing, "smart1"),
      "a highlight that is not in the headline is dropped, not forced")


# ------------------------------------------------------- 4. absent is absent
head("An empty field prints nothing rather than an empty shell")

chip = B.new_block("channels")
for item in chip["items"]:
    item["chip"] = ""
# with_css=False throughout this section: the stylesheet names every class it
# styles, so searching the full output would find `.s1-chip` in the CSS and
# pass whatever the markup did.
check("s1-chip" not in B.render_block(chip, "smart1", with_css=False),
      "a blank rate chip is omitted, not printed empty",
      "an empty pill reads as a rate we could not find")

quiet = B.new_block("quote")
quiet["name"] = quiet["role"] = ""
out = B.render_block(quiet, "smart1", with_css=False)
check("s1-by" not in out and "s1-role" not in out,
      "an unattributed quote prints no empty byline")

bare = B.new_block("embed")
bare["src"] = ""
out = B.render_block(bare, "smart1", with_css=False)
check("<iframe" not in out, "an embed with no URL renders no iframe")
check("No tool URL" in out, "…and says so in the block",
      "a silently absent iframe looks like a page that failed to load")


# ---------------------------------------------------------- 5. page assembly
head("Page and block modes agree")

page = B.sample_page("heat")
blocks = page["blocks"]

joined = B.render_blocks(blocks, "heat")
check(joined.count("<style>") == 1,
      "all-blocks mode emits the stylesheet once")
check(joined.count("<section") == len(blocks),
      "all-blocks mode emits every section")

per_block = [B.render_block(b, "heat") for b in blocks]
check(all(p.count("<style>") == 1 for p in per_block),
      "single-block mode carries the stylesheet with it")
check(len({p.split("</style>")[0] for p in per_block}) == 1,
      "every block ships the identical stylesheet",
      "pasting several must not produce competing styles")
# The pixels have to be the same either way, so the section markup itself must
# be byte-identical between the two modes.
check(all(p.split("</style>\n", 1)[1] == B.render_block(b, "heat", with_css=False)
          for p, b in zip(per_block, blocks)),
      "the section markup is identical in both modes")

doc = B.render_page(dict(page, with_nav=True, with_footer=True,
                         title="Roofing", description="A description."))
check(doc.startswith("<!doctype html>"), "the document mode returns a document")
check(doc.count("<html") == 1 and doc.count("</html>") == 1,
      "…with one html element")
check("<title>Roofing</title>" in doc, "…carrying the page title")
check('name="description"' in doc, "…and its meta description")
# The elements, not the class names — the stylesheet names both classes in
# every document, so `"s1blk-nav" in doc` is true either way.
check("<header" in doc and "<footer" in doc,
      "…with the nav and footer when asked for")
plain = B.render_page(page)
check("<header" not in plain and "<footer" not in plain,
      "…and without them when not")

# A theme must actually reach the output, or the picker is decoration.
for key, t in B.THEMES.items():
    check(t["accent"] in B.render_block(blocks[0], key),
          f"theme {key} reaches the rendered block")


# ---------------------------------------------------- 6. the app, end to end
head("The module boots and answers")

from modules.site_blocks import app as A     # noqa: E402

A.app.config["TESTING"] = True
client = A.app.test_client()

r = client.get("/api/catalogue")
check(r.status_code == 200, "GET /api/catalogue")
cat = r.get_json()
check(len(cat["blocks"]) == len(B.BLOCKS), "…lists every block type")
check(len(cat["themes"]) == len(B.THEMES), "…and every theme")

r = client.get("/api/sample")
check(r.status_code == 200, "GET /api/sample")
sample = r.get_json()
check(len(sample["blocks"]) >= 5, "…returns a page with sections in it")

r = client.post("/api/render", json={"mode": "all", "theme": "ice",
                                     "blocks": sample["blocks"]})
check(r.status_code == 200, "POST /api/render")
body = r.get_json()
check(body["html"].count("<section") == len(sample["blocks"]),
      "…renders every section")
check(r.mimetype == "application/json",
      "…as JSON, never as text/html",
      "an HTML reply would collect the hub sidebar on the way out")

r = client.post("/api/render", json={"mode": "block", "index": 0,
                                     "blocks": sample["blocks"]})
check(r.status_code == 200 and r.get_json()["html"].count("<section") == 1,
      "…and one section in block mode")
r = client.post("/api/render", json={"mode": "block", "index": 99,
                                     "blocks": sample["blocks"]})
check(r.status_code == 400, "…refusing an index that is not there")

r = client.post("/api/download", json={"mode": "document", "title": "Roof Co",
                                       "blocks": sample["blocks"]})
check(r.status_code == 200, "POST /api/download")
check("attachment" in r.headers.get("Content-Disposition", ""),
      "…as an attachment",
      "an inline text/html reply is what the hub injects its sidebar into")
check("roof-co.html" in r.headers.get("Content-Disposition", ""),
      "…named from the page title")

# An unknown block type must be dropped rather than reaching the renderer.
r = client.post("/api/render", json={
    "mode": "all", "blocks": [{"kind": "not-a-block", "title": "x"},
                              {"kind": "cta", "title": "Real"}]})
check(r.get_json()["html"].count("<section") == 1,
      "an unknown block type is dropped on the way in")

# A field the block does not declare must not survive a save/reload round trip.
r = client.post("/api/pages", json={
    "title": "Round trip", "theme": "growth",
    "blocks": [dict(B.new_block("cta"), sneaky="kept?")]})
check(r.status_code == 200, "POST /api/pages saves")
pid = r.get_json()["id"]
r = client.get("/api/pages/" + pid)
check(r.status_code == 200, "GET /api/pages/<id> reads it back")
saved = r.get_json()
check("sneaky" not in saved["blocks"][0], "…dropping fields the block never declared")
check(saved["theme"] == "growth", "…keeping the theme")
check(saved["blocks"][0]["title"] == B.BLOCKS["cta"]["sample"]["title"],
      "…and the copy")

r = client.post("/api/pages", json={"title": "Empty", "blocks": []})
check(r.status_code == 400, "saving a page with no sections is refused")

r = client.get("/api/pages")
check(any(p["id"] == pid for p in r.get_json()["pages"]),
      "the saved page is in the listing")
check(client.delete("/api/pages/" + pid).status_code == 200, "DELETE /api/pages/<id>")
check(client.delete("/api/pages/" + pid).status_code == 404,
      "…and deleting it twice is a 404, not a silent success")

r = client.post("/api/write", json={"brief": "", "blocks": sample["blocks"]})
check(r.status_code == 400, "writing copy with no brief is refused")

r = client.get("/api/status")
check(r.status_code == 200 and "ai" in r.get_json(),
      "GET /api/status says whether the writer is available")

# The builder page itself. help_dot is a hub global that a module's own Jinja
# environment does not have, so this is the check that the `if ... is defined`
# guard in the template is still there.
r = client.get("/")
check(r.status_code == 200, "GET / renders the builder")
check(b"catalogue" in r.data, "…with the catalogue inlined for the JS")


# ---------------------------------------------------------------- 7. the tile
head("The tool is reachable from the tools page")

tools = open("hub/templates/tools.html", encoding="utf-8").read()
check("/tools/site-blocks/" in tools, "the tools page links to it",
      "a tool with no tile is invisible")

wsgi_src = open("wsgi.py", encoding="utf-8").read()
check('"/tools/site-blocks"' in wsgi_src, "wsgi.py mounts it")
check("modules.site_blocks.app" in wsgi_src, "…importing the module")
check("siteblk_fb" in wsgi_src, "…behind a fallback app if the import fails")


# ---------------------------------------------------------------------- done
print()
if FAILURES:
    print(f"FAILED — {len(FAILURES)} of {CHECKS} checks")
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print(f"OK — {CHECKS} checks passed")
