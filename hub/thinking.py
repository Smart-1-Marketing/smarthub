"""The mark that says something is running, for the pages the script cannot reach.

`hub/static/hub-thinking.js` is the Hub's one implementation of this, and it is
on every staff page: `base.html` links it, `HubBar` injects it into the twenty
mounted modules, and the hub app's own injector puts it into the
blueprint-registered ones. Three code paths, and none of them reaches a page a
**client** opens.

That is deliberate rather than an oversight. Every client-facing surface in this
Hub is chrome-free by design — `CHROMELESS` in `hub/__init__.py`, and the
`PUBLIC_PREFIXES` each mounted module declares — because injecting the staff
sidebar, the help layer and a feedback tab into a document a client or a
prospect reads is the failure those lists exist to prevent. The script rides in
with that chrome, so switching the chrome off switches the mark off with it, and
nothing anywhere says so.

What that left behind
---------------------------------------------------------------------------
Seven pages a client stands in front of, each running a POST they are waiting
on, and not one of them drawing a mark:

  * the commercial review link — a client approves a finished TV cut, and the
    button greys out with no word and no glyph;
  * the Smart 1 Ads client estimate — a change request, and the same;
  * the four Social Content client pages — the swipe answered with no visible
    change at all, so a second tap on a phone is met by the double-tap guard
    doing exactly nothing, which reads as a broken button;
  * the proposal accept page, which at least says "Sending…".

And where they did say something they each said it in their own words —
"Sending…", "Saving…", "Uploading 3…" — written locally, four ways, which is
the drift `hub/storage.py` and `hub/images.py` exist to stop, wearing a
spinner and one audience further out.

Why this is a Jinja global and not a template
---------------------------------------------------------------------------
Those seven pages are spread across four Flask apps with four separate Jinja
environments, so a macro in one module's `templates/` is invisible to the other
three — the first trap this repo's CLAUDE.md names. A global registered by
`install_template_helpers()` (every mounted module, from `wsgi.py`) and by
`register_help()` (the hub app) reaches all of them, which is the same
arrangement `help_dot` already uses, and every call site is written
`{{ s1_wait_assets() if s1_wait_assets is defined else '' }}` for the same
reason: a module whose environment never got the registration loses the mark
rather than the page.

Why the markup is inlined rather than fetched
---------------------------------------------------------------------------
`modules/scans/templates/_scan_mark.html` settled this already for the three
pages a prospect meets on somebody else's website: a script fetched from the
Hub, on a page whose whole job is to load, is a new outbound dependency for a
decoration. The same argument holds for a proposal a client opens from an
email. So this emits its own `<style>` and its own small `<script>`, and the
page needs nothing from us but itself.

Why the name is `S1Wait` and not `S1Think`
---------------------------------------------------------------------------
This is the smaller sibling, not a second copy of the whole thing. It draws the
glyph and it swaps a button's label; it has no stage timer, no elapsed line and
no MutationObserver upgrade of existing spinners. Reusing `S1Think` would mean
one name meaning two different sets of promises depending on which page you are
reading, and somebody would eventually call `.stage()` on the half that has
none. The waits these pages run are single short POSTs, where an elapsed line
would be the noise `hub-thinking.js`'s own note warns about — a stopwatch on a
two-second read teaches people to expect a wait.

The four rules, unchanged from the script
---------------------------------------------------------------------------
  * **Nothing here may raise.** Every entry point is wrapped and `busy()`
    always returns a handle carrying a `.done()`, so a caller's `finally` is
    safe even when the button it named is not on the page. Call sites guard on
    `window.S1Wait` so a page that failed to run the block costs the mark and
    never the answer.
  * **It never claims what it does not know.** `.done()` puts the button back
    exactly as it was. It writes no "Done" and draws no tick: whether the call
    succeeded is the caller's answer, and a tick over a failed one is the
    confident wrong answer this codebase keeps undoing.
  * **`currentColor`, never a palette.** These seven pages have seven
    stylesheets and no shared one; inheriting the surrounding text color is the
    only way one glyph reads on a white card, a navy button and a dark client
    page without any of them being edited.
  * **`prefers-reduced-motion` drops the motion and keeps the mark.** That
    setting asks for less movement, not less information.

There are now three server-side drawings of these glyphs — this module,
`_scan_mark.html` and `modules/ad_builder/public/embed.html` — and that is on
purpose rather than by neglect: the first two are separate because a prospect
page that is self-contained today must not gain a runtime dependency on a
global to save a duplicate, and the third is served straight off the Node
renderer where no Jinja global exists at all. `test_thinking.py` holds all
three in step with `hub-thinking.js` rather than memory.
"""

from __future__ import annotations

try:                                     # pragma: no cover - Flask is present
    from markupsafe import Markup
except Exception:                        # noqa: BLE001 - never break an import
    def Markup(s):                       # type: ignore[misc]
        return s


KINDS = ("ai", "scan", "wait")

# The glyphs. Each is a 24x24 viewBox drawn in currentColor, and each is the
# same drawing hub-thinking.js makes for the same kind — a client waiting on a
# model and a rep waiting on the same model are waiting on one thing, and it
# must not look like two features.
#
#   ai    the four-point sparkle, turning, with two twinkles off-beat. The ✨
#         Client 360 already puts on its own AI control.
#   scan  the dish with a sweeping wedge and a contact that pings.
#   wait  the arc, which is what every hand-rolled spinner in this repo drew.
_GLYPHS = {
    "ai": (
        '<path class="s1w-star" fill="currentColor"'
        ' d="M12 3.2 13.6 9.1 19.4 10.7 13.6 12.3 12 18.2 10.4 12.3 4.6 10.7 10.4 9.1Z"/>'
        '<circle class="s1w-tw s1w-tw1" cx="19.4" cy="4.9" r="1.7" fill="currentColor"/>'
        '<circle class="s1w-tw s1w-tw2" cx="5.1" cy="19.2" r="1.3" fill="currentColor"/>'
    ),
    "scan": (
        '<circle cx="12" cy="12" r="9.2" fill="none" stroke="currentColor"'
        ' stroke-width="1.6" opacity=".3"/>'
        '<circle cx="12" cy="12" r="4.4" fill="none" stroke="currentColor"'
        ' stroke-width="1.4" opacity=".22"/>'
        '<path class="s1w-sweep" d="M12 12 L12 2.8 A9.2 9.2 0 0 1 20.5 8.6 Z"'
        ' fill="currentColor" opacity=".55"/>'
        '<circle class="s1w-ping" cx="16.3" cy="7.9" r="1.7" fill="currentColor"/>'
    ),
    "wait": (
        '<circle cx="12" cy="12" r="8.6" fill="none" stroke="currentColor"'
        ' stroke-width="2.4" opacity=".25"/>'
        '<circle class="s1w-arc" cx="12" cy="12" r="8.6" fill="none"'
        ' stroke="currentColor" stroke-width="2.4" stroke-linecap="round"'
        ' stroke-dasharray="16 38"/>'
    ),
}


_CSS = """
.s1w-mark{display:inline-block;width:1.05em;height:1.05em;vertical-align:-.18em;
  flex:0 0 auto;color:inherit;overflow:visible}
.s1w-btn{display:inline-flex !important;align-items:center;gap:.45em}
@keyframes s1w-star{0%{transform:rotate(0) scale(.9)}
  50%{transform:rotate(180deg) scale(1.05)}100%{transform:rotate(360deg) scale(.9)}}
@keyframes s1w-tw{0%,100%{opacity:.15;transform:scale(.5)}
  45%{opacity:1;transform:scale(1)}}
@keyframes s1w-sweep{to{transform:rotate(360deg)}}
@keyframes s1w-ping{0%{opacity:0;transform:scale(.4)}
  12%{opacity:1;transform:scale(1)}55%,100%{opacity:0;transform:scale(1.5)}}
@keyframes s1w-arc{to{transform:rotate(360deg)}}
.s1w-star{transform-origin:12px 12px;animation:s1w-star 2.6s ease-in-out infinite}
.s1w-tw{transform-box:fill-box;transform-origin:center;
  animation:s1w-tw 1.7s ease-in-out infinite}
.s1w-tw1{animation-delay:.25s}
.s1w-tw2{animation-delay:1.05s}
.s1w-sweep{transform-origin:12px 12px;animation:s1w-sweep 1.9s linear infinite}
.s1w-ping{transform-box:fill-box;transform-origin:center;
  animation:s1w-ping 1.9s ease-out infinite}
.s1w-arc{transform-origin:12px 12px;animation:s1w-arc .9s linear infinite}
/* Reduced motion asks for less movement, not less information. The glyph stays
   drawn and the word beside it still says what is running; only the movement
   goes. A wait that became invisible for these readers would be the feature
   failing in exactly the place it was needed. */
@media (prefers-reduced-motion:reduce){
  .s1w-star,.s1w-tw,.s1w-sweep,.s1w-ping,.s1w-arc{animation:none !important}
  .s1w-tw,.s1w-ping{opacity:.55}
  .s1w-star{transform:rotate(20deg)}
}
"""


# The whole of the browser half. Deliberately small: a glyph, a button that
# keeps its width while it works, and a handle whose .done() puts the button
# back. Nothing here reaches the network and nothing here may raise.
_JS = """
(function(){
  if (window.S1Wait) { return; }
  var NS = "http://www.w3.org/2000/svg";
  var G = __GLYPHS__;
  function mark(kind){
    var k = G[kind] ? kind : "wait";
    var s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("focusable", "false");
    s.setAttribute("class", "s1w-mark s1w-" + k);
    s.innerHTML = G[k];
    return s;
  }
  /* A button that keeps its width while it works. The shape these seven pages
     each wrote out longhand is `btn.disabled = true` and nothing else, which
     loses nothing because there was nothing, and says nothing either. */
  function busy(button, opts){
    var o = opts || {};
    var btn = typeof button === "string"
      ? (document.getElementById(button) || document.querySelector(button))
      : button;
    /* Returned even when there is nothing to draw on, so a caller's
       finally { h.done(); } is safe and never has to null-check. */
    var handle = { stage: function(){ return handle; },
                   done: function(){ return handle; }, el: null };
    if (!btn) { return handle; }
    var was = btn.innerHTML, wasDisabled = btn.disabled, label;
    try {
      btn.disabled = true;
      btn.classList.add("s1w-btn");
      btn.innerHTML = "";
      btn.appendChild(mark(o.kind || "wait"));
      label = document.createElement("span");
      label.textContent = o.label || "Working\\u2026";
      btn.appendChild(label);
      handle.el = btn;
    } catch (e) { return handle; }
    handle.stage = function(text){
      try { if (label && text) { label.textContent = text; } } catch (e) {}
      return handle;
    };
    /* Stops the animation and puts the button back exactly as it was. It does
       not write "Done" and it does not draw a tick: whether the call succeeded
       is the caller's answer. */
    handle.done = function(){
      try {
        btn.innerHTML = was;
        btn.disabled = wasDisabled;
        btn.classList.remove("s1w-btn");
      } catch (e) {}
      return handle;
    };
    return handle;
  }
  /* The other half of the same idea, for a wait reported on a status line
     rather than in the button — three of these pages already had the line and
     wrote a bare word into it. Same handle, so a call site reads the same
     either way. */
  function note(target, opts){
    var o = opts || {};
    var host = typeof target === "string"
      ? (document.getElementById(target) || document.querySelector(target))
      : target;
    var handle = { stage: function(){ return handle; },
                   done: function(){ return handle; }, el: null };
    if (!host) { return handle; }
    var was = host.innerHTML, label;
    try {
      host.innerHTML = "";
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      host.appendChild(mark(o.kind || "wait"));
      label = document.createElement("span");
      label.style.marginLeft = ".45em";
      label.textContent = o.label || "Working\u2026";
      host.appendChild(label);
      handle.el = host;
    } catch (e) { return handle; }
    handle.stage = function(text){
      try { if (label && text) { label.textContent = text; } } catch (e) {}
      return handle;
    };
    handle.done = function(){
      try { host.innerHTML = was; } catch (e) {}
      return handle;
    };
    return handle;
  }
  window.S1Wait = { KINDS: __KINDS__, mark: mark, busy: busy, note: note };
})();
"""


def mark_svg(kind: str = "wait", size: str = "1.05em", label: str = "Working") -> str:
    """One glyph as markup, for a page that wants a static mark in a string.

    `label` is what a screen reader is told, for which a spinning circle on its
    own says nothing at all.
    """
    k = kind if kind in _GLYPHS else "wait"
    return (
        f'<svg class="s1w-mark s1w-{k}" width="{size}" height="{size}"'
        f' viewBox="0 0 24 24" role="img" aria-label="{label}">{_GLYPHS[k]}</svg>'
    )


def css() -> str:
    """The stylesheet body, without the <style> wrapper."""
    return _CSS.strip()


def js() -> str:
    """The script body, without the <script> wrapper."""
    glyphs = "{" + ",".join(
        '"%s":%s' % (k, _json_string(v)) for k, v in sorted(_GLYPHS.items())
    ) + "}"
    kinds = "[" + ",".join('"%s"' % k for k in KINDS) + "]"
    return _JS.strip().replace("__GLYPHS__", glyphs).replace("__KINDS__", kinds)


def _json_string(s: str) -> str:
    import json
    # </script> inside a string literal would close the block the script is in.
    return json.dumps(s).replace("</", "<\\/")


def assets() -> "Markup":
    """The whole inline block: one <style> and one <script>.

    Emitted once per page, anywhere in it. Call sites are written
    `{{ s1_wait_assets() if s1_wait_assets is defined else '' }}`, so a module
    whose Jinja environment never received the registration loses the mark and
    never the page — the guard every helper in `hub/help_routes.py` uses.
    """
    return Markup("<style>%s</style>\n<script>%s</script>" % (css(), js()))


def install(app) -> None:
    """Expose the two globals on one app's Jinja environment.

    Called from `install_template_helpers()` for every mounted module and from
    `register_help()` for the hub app, because a module's environment is its own
    and a global registered on the hub app is invisible inside a mount.
    """
    app.jinja_env.globals.setdefault("s1_wait_assets", assets)
    app.jinja_env.globals.setdefault("s1_wait_mark", lambda *a, **k: Markup(mark_svg(*a, **k)))
