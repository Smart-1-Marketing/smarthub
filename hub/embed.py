"""One way to put a Hub landing tool on smart1marketing.com.

## Why this exists

Nine industry landing tools live in this Hub -- `/land/boat`, `/land/ski`,
`/land/stadium` and the rest. Each one asks a prospect a handful of questions,
builds a plan, and writes the answer into `hub/leads.py`, which creates the
contact in Smart 1 Suite over the Contacts API. That path works and is tested.

The marketing site has its own page per industry
(`smart1marketing.com/boat-dealer-marketing-gameplan`, ...). Those pages carry
whatever form the site builder wired up, which is not this one -- so a prospect
who fills the form on the marketing site does not appear in the Leads panel,
does not get a plan, and does not become a Suite contact. Nothing errors. That
is the whole failure: two forms for one campaign, and only one of them is
connected.

## The shape that was tried before, and why it is not this one

`modules/rv/public/smart1-multipart-embed.html` was a second copy of the RV
form, meant to be pasted into the Suite page and to POST back here. It carried
both of the traps this codebase keeps re-learning:

  * `const API_BASE = 'https://YOUR-RENDER-APP.onrender.com'` -- a placeholder
    that every "is it configured?" glance reads as a real setting.
  * Its request path was built by concatenating that base onto the endpoint,
    which is the one shape `tools/linkcheck.py` cannot see -- and the endpoint
    it concatenated omits the `/land/rv` mount the app actually answers on. So
    even with the host filled in correctly it was a 404.

And a copied form is a form that drifts: the day a field is added here, the
pasted one keeps collecting the old set and nothing says so.

So this module does not copy the form. It frames the real one. The mount
prefix is decided by the server, the fields are whatever the tool asks for
today, and the lead travels the same path a lead from `/land/rv` travels --
because it *is* a lead from `/land/rv`.

## What `install()` adds to a module

    /embed      the tool's own page, framable from the marketing site only
    /embed.js   a one-line loader that writes the iframe and keeps it sized

Two rules carried over from `modules/msa`, which established this pattern:

**The allowlist is an allowlist.** `frame-ancestors *` is right for the scans
widget, which is pasted onto clients' domains and cannot know them in advance.
These are framed only on ours. `X-Frame-Options` is dropped rather than set,
because it has no allowlist form and some browsers let it override CSP -- two
rules that can disagree is worse than one that decides.

**`/embed` has no trailing slash.** `modules/tourism` calls its API as
`fetch('api/partial-lead')`, a relative path that resolves against the
*directory* of the current URL. From `/land/tourism/embed` that is
`/land/tourism/api/partial-lead`, which is right; from `/land/tourism/embed/`
it is `/land/tourism/embed/api/partial-lead`, a 404 the prospect does not meet
until they have filled in the whole wizard and pressed submit. `/embed/`
redirects rather than serving.

## Two things the loader does that a bare iframe cannot

**Height.** These wizards start as a short form and end as a multi-page plan.
In a fixed-height frame that is a scrollbar inside a scrollbar, and on a phone
it is unusable. The framed page reports its own height on every change and the
loader grows the frame, so the host page scrolls once.

**Scroll.** Every one of these tools calls `window.scrollTo(0, 0)` or
`scrollIntoView()` when it moves between steps -- correct standalone, and a
silent no-op inside a frame tall enough to have no scrollbar of its own. The
prospect presses Continue at the bottom of a long frame and the next step
renders far above their viewport, so the page looks like it did nothing. The
reporter forwards those calls to the host, which scrolls to the same place on
the outer page. This is a translation of a scroll the tool already asked for,
not a guess about when to scroll.
"""
from __future__ import annotations

import os

from flask import Response, make_response, redirect, url_for

# Who may put a Hub landing tool in an iframe. Space-separated, CSP syntax.
#
# An ALLOWLIST, deliberately -- see the module docstring. Render stores quotes
# literally (SCANS_CALLBACK_TOKEN="abc" arrives including the quotes), so a
# value pasted with them still works here rather than producing a CSP nobody
# can read and an embed nobody can explain.
DEFAULT_FRAME_ANCESTORS = ("'self' https://smart1marketing.com "
                           "https://*.smart1marketing.com")


def frame_ancestors() -> str:
    """The CSP source list, read fresh so a test can set the environment.

    Only the double quotes Render adds are stripped. A CSP source list carries
    single quotes as syntax -- ``'self'`` means nothing without them -- so
    stripping those would turn a correct setting into one that allows no
    framer at all, and the embed would go blank with the variable looking set.
    """
    raw = (os.environ.get("HUB_FRAME_ANCESTORS") or "").strip().strip('"').strip()
    return raw or DEFAULT_FRAME_ANCESTORS


def framable(resp):
    """Let the allowlisted hosts frame this response, and nobody else."""
    resp.headers["Content-Security-Policy"] = "frame-ancestors " + frame_ancestors()
    # X-Frame-Options has no allowlist form: any value it could carry would
    # either forbid the embed outright or be honoured inconsistently, and some
    # browsers let it override CSP. Dropping it leaves one rule in charge.
    resp.headers.pop("X-Frame-Options", None)
    return resp


# --------------------------------------------------------------------------
# Injected into the framed page
# --------------------------------------------------------------------------
#
# tools/jscheck.py reads .js files and the inline blocks in templates, so it
# never sees this one or the loader below -- they are Python strings. Both are
# handed to `node --check` by test_landing_embeds.py instead, because a syntax
# error here does not raise: the script simply does not run, and every gameplan
# frame stays at its starting height with the plan cut off.

REPORTER_JS = r"""
<script>
/* Smart 1 embed reporter -- injected by hub/embed.py, only on /embed.
   Reports this document's height to the host page, and forwards the scrolls
   the tool asks for. Everything is wrapped: a page that fails to load because
   its resize helper threw is worse than a page with a fixed height. */
(function () {
  if (window.top === window.self) return;   /* not framed: nothing to report */

  var last = 0;

  function send(type, data) {
    try {
      var msg = {type: type};
      for (var k in data) { if (data.hasOwnProperty(k)) msg[k] = data[k]; }
      window.parent.postMessage(msg, '*');
    } catch (e) {}
  }

  function height() {
    var d = document.documentElement, b = document.body;
    if (!d || !b) return 0;
    return Math.max(b.scrollHeight, b.offsetHeight,
                    d.scrollHeight, d.offsetHeight) + 8;
  }

  function report() {
    var h = height();
    /* A one-pixel wobble on every animation frame would post hundreds of
       messages a second and make the host page shiver. */
    if (!h || Math.abs(h - last) < 4) return;
    last = h;
    send('s1embed:height', {height: h});
  }

  /* Height changes for reasons no single event covers: fonts arriving, an
     accordion opening, a report rendering from a fetch. ResizeObserver sees
     all of them; the interval is the floor for browsers that lack it and for
     changes inside a subtree it is not watching. */
  try {
    if (window.ResizeObserver) {
      new ResizeObserver(report).observe(document.documentElement);
    }
  } catch (e) {}
  window.addEventListener('load', report);
  window.addEventListener('resize', report);
  document.addEventListener('DOMContentLoaded', report);
  setInterval(report, 500);
  report();

  /* --- forwarding the tool's own scrolls -------------------------------
     The frame is grown to fit its content, so it has no scrollbar and every
     scroll call inside it does nothing. Each landing tool scrolls when it
     changes step, which is exactly when the prospect needs to be moved. So
     the call is translated into a host-page scroll to the same place rather
     than being lost. */
  function offsetOf(el) {
    var y = 0;
    try {
      while (el && el.offsetParent) { y += el.offsetTop; el = el.offsetParent; }
    } catch (e) {}
    return y;
  }

  try {
    var nativeScrollTo = window.scrollTo;
    window.scrollTo = function () {
      var top = 0;
      if (arguments.length === 1 && arguments[0] && typeof arguments[0] === 'object') {
        top = arguments[0].top || 0;
      } else if (arguments.length > 1) {
        top = arguments[1] || 0;
      }
      send('s1embed:scroll', {top: top});
      try { return nativeScrollTo.apply(window, arguments); } catch (e) {}
    };
  } catch (e) {}

  try {
    var nativeIntoView = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function () {
      send('s1embed:scroll', {top: offsetOf(this)});
      try { return nativeIntoView.apply(this, arguments); } catch (e) {}
    };
  } catch (e) {}
})();
</script>
"""


def with_reporter(html: bytes) -> bytes:
    """Put the reporter in, the way HubBar puts the sidebar in.

    Before the LAST ``</body>``, not the first. The IO Builder's printable
    documents are JavaScript template literals that each carry their own
    ``</body>``, and injecting at the first one dropped the Hub sidebar inside
    a string and rendered the whole tool blank. None of these pages does that
    today, and none of them has to keep not doing it.

    A page with no ``</body>`` is returned untouched: a fragment is not a page,
    and appending script to one is how a partial response becomes a broken one.
    """
    marker = b"</body>"
    if marker not in html:
        return html
    cut = html.rfind(marker)
    return html[:cut] + REPORTER_JS.encode("utf-8") + html[cut:]


# --------------------------------------------------------------------------
# The loader the marketing site pastes
# --------------------------------------------------------------------------

def loader_js(title: str, default_height: int) -> str:
    """The body of ``/embed.js``.

    The Hub's URL appears exactly once on the marketing site -- in this
    script's own ``src`` -- and the frame URL, the origin check and everything
    else are derived from it. Moving the Hub to another host is then a
    one-word edit per page rather than a hunt through pasted markup, which is
    the failure ``smart1-multipart-embed.html`` shipped with.
    """
    safe_title = title.replace("\\", "\\\\").replace("'", "\\'")
    # Raw string: the regexes carry \/ and \. which Python does not recognise
    # as escapes. It warns today and errors in a future release, at which
    # point the loader stops matching and every embed shows a frozen frame.
    return r"""(function(){
  var s = document.currentScript;
  if (!s || !s.src) return;
  var base = s.src.replace(/\/embed\.js(\?.*)?$/, '');
  var origin = base.replace(/^(https?:\/\/[^\/]+).*$/, '$1');

  var frame = document.createElement('iframe');
  frame.src = base + '/embed';
  frame.title = '__TITLE__';
  frame.loading = 'lazy';
  frame.setAttribute('scrolling', 'no');
  frame.setAttribute('allow', 'clipboard-write');
  frame.style.cssText = 'display:block;width:100%;border:0;' +
                        'height:' + (s.getAttribute('data-height') || '__HEIGHT__') + 'px;';
  s.parentNode.insertBefore(frame, s);

  function frameTop() {
    var y = 0, el = frame;
    while (el) { y += el.offsetTop || 0; el = el.offsetParent; }
    return y;
  }

  window.addEventListener('message', function(e){
    /* Both checks, not either. The origin says the message came from the Hub;
       the source says it came from THIS frame rather than another Hub embed
       further down the same page. */
    if (e.origin !== origin) return;
    if (e.source !== frame.contentWindow) return;
    var d = e.data || {};
    if (d.type === 's1embed:height' && d.height) {
      frame.style.height = d.height + 'px';
    } else if (d.type === 's1embed:scroll') {
      /* The tool asked to scroll and could not, because the frame it lives in
         has no scrollbar of its own. Same destination, outer page. */
      var target = frameTop() + (d.top || 0) - 20;
      try { window.scrollTo({top: target, behavior: 'smooth'}); }
      catch (err) { window.scrollTo(0, target); }
    }
  });
})();""".replace("__TITLE__", safe_title).replace("__HEIGHT__", str(int(default_height)))


# --------------------------------------------------------------------------

def install(app, title: str, view=None, default_height: int = 1400) -> None:
    """Give a landing module ``/embed`` and ``/embed.js``.

    ``view`` is the page to frame, and defaults to whatever the module has
    registered on ``/`` -- which is the tool itself in every landing module
    but HVAC, where ``/`` is a brochure page and the wizard is ``/plan``.
    Resolving it here rather than at nine call sites means a module that
    renames its index view does not have to remember to come back.

    Call this at the BOTTOM of the module, after the route it frames exists.
    """
    if view is None:
        for rule in app.url_map.iter_rules():
            if rule.rule == "/" and "GET" in (rule.methods or ()):
                view = app.view_functions[rule.endpoint]
                break
    if view is None:                      # nothing to frame: add nothing
        raise RuntimeError("hub.embed.install: no view to frame")

    def _embed():
        resp = make_response(view())
        # send_from_directory hands back a passthrough response, and reading
        # its body without clearing this raises rather than returning bytes.
        resp.direct_passthrough = False
        # set_data recomputes Content-Length; the pop is here so that a
        # response which arrived with one set some other way cannot survive
        # with the pre-injection length. A short Content-Length truncates the
        # page at exactly the point the browser stops reading, which is
        # mid-script -- so the tool half-loads rather than failing outright.
        resp.set_data(with_reporter(resp.get_data()))
        resp.headers.pop("Content-Length", None)
        resp.headers["Cache-Control"] = "no-store"
        return framable(resp)

    def _embed_slash():
        """See the module docstring: the trailing slash breaks tourism's
        relative API path, and does it only at the moment of submission."""
        return redirect(url_for("s1_embed"), code=301)

    def _embed_js():
        # charset stated explicitly. A classic script with no charset is
        # decoded as the HOST page's encoding, and the frame title carries an
        # em dash -- so on a page that is not UTF-8 the accessible name of
        # every embed on the marketing site turns to mojibake.
        return framable(Response(
            loader_js(title, default_height),
            content_type="application/javascript; charset=utf-8",
            headers={"Cache-Control": "public, max-age=3600"}))

    app.add_url_rule("/embed", "s1_embed", _embed, methods=["GET"])
    app.add_url_rule("/embed/", "s1_embed_slash", _embed_slash, methods=["GET"])
    app.add_url_rule("/embed.js", "s1_embed_js", _embed_js, methods=["GET"])
