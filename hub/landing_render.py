"""Render a landing page as standalone, self-contained HTML.

One file. No build step, no framework, no external CSS — it has to be
pasteable into Smart 1 Sites, a GoHighLevel funnel, or a client's own CMS
without carrying a toolchain with it.

## Sections are omitted, not filled

If the copy writer had nothing for testimonials, there is no testimonials
section — rather than a heading over invented quotes. Every block here checks
for real content first. That is the difference between a page that's short and
a page that lies.

## The brand comes from Brandfetch

Their logo, their colours, their fonts. A landing page in someone else's
palette reads as an agency template with a name dropped in, and it converts
like one. Where there's no brand data the fallback is a neutral navy that
doesn't pretend to be theirs.
"""
from __future__ import annotations

import html as _html
import re


def esc(v) -> str:
    return _html.escape(str(v or ""), quote=True)


def _contrast(hex_color: str) -> str:
    """Black or white text, whichever is readable on this background.

    Brand palettes include pale yellows and near-blacks. Assuming white text
    is how a hero ends up unreadable on the client's own colour.
    """
    c = (hex_color or "").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return "#ffffff"
    # Perceived luminance, not average.
    return "#0d1117" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _hex_or(value, fallback: str) -> str:
    """A colour we are willing to paste into a stylesheet, or the fallback.

    Brandfetch is a third party and these values land unquoted inside a
    ``<style>`` block, so anything that is not literally a hex colour is
    dropped rather than escaped. A brand with an odd colour format loses that
    colour; it does not get to close the style element.
    """
    v = str(value or "").strip()
    return v if _HEX.match(v) else fallback


def _palette(brief: dict) -> dict:
    colors = [c for c in (brief.get("colors") or []) if isinstance(c, str)]
    primary = _hex_or(colors[0] if colors else "", "#0D2340")
    accent = _hex_or(colors[1] if len(colors) > 1 else "", "#3EC6F0")
    return {"primary": primary, "accent": accent,
            "on_primary": _contrast(primary),
            "on_accent": _contrast(accent)}


def _font_stack(brief: dict) -> str:
    """Their fonts, first, with a stack behind them.

    Same rule as the palette: a family name goes into a stylesheet unquoted,
    so anything but letters, digits, spaces and hyphens is dropped instead of
    escaped.
    """
    fonts = [re.sub(r"[^A-Za-z0-9 -]", "", f).strip()
             for f in (brief.get("fonts") or []) if isinstance(f, str)]
    fonts = [f for f in fonts if f][:2]
    if fonts:
        return ", ".join(f'"{f}"' for f in fonts) + \
               ", 'Segoe UI', system-ui, sans-serif"
    return "'Segoe UI', system-ui, -apple-system, sans-serif"


def render_page(brief: dict, copy: dict, direction: dict) -> str:
    import json as _json
    json_client = _json.dumps(str(brief.get("client") or ""))
    p = _palette(brief)
    font = _font_stack(brief)
    client = esc(brief.get("client"))
    phone = esc(brief.get("phone"))
    cta = esc(copy.get("cta") or "Get started")
    area = esc(brief.get("geo") or f"{brief.get('city','')} {brief.get('state','')}".strip())

    hero_bg = (f"background:{p['primary']};color:{p['on_primary']}"
               if direction["hero"] != "light"
               else f"background:#f7fafc;color:#0d1117;"
                    f"border-bottom:1px solid #e2e8f0")

    def section(inner: str) -> str:
        return f'<section class="sec">{inner}</section>' if inner else ""

    # --- benefits -----------------------------------------------------
    benefits = ""
    items = [b for b in (copy.get("benefits") or [])
             if isinstance(b, dict) and b.get("title")]
    if items:
        cards = "".join(
            f'<div class="card"><h3>{esc(b.get("title"))}</h3>'
            f'<p>{esc(b.get("text"))}</p></div>' for b in items)
        benefits = section(f'<div class="grid">{cards}</div>')

    # --- how it works -------------------------------------------------
    how = ""
    steps = [s for s in (copy.get("how_it_works") or [])
             if isinstance(s, dict) and s.get("step")]
    if steps:
        rows = "".join(
            f'<div class="step"><span class="n">{i}</span>'
            f'<div><h3>{esc(s.get("step"))}</h3><p>{esc(s.get("text"))}</p></div></div>'
            for i, s in enumerate(steps, 1))
        how = section(f'<h2>How it works</h2><div class="steps">{rows}</div>')

    # --- why us -------------------------------------------------------
    why = ""
    reasons = [r for r in (copy.get("why_us") or []) if isinstance(r, str) and r.strip()]
    if reasons:
        lis = "".join(f"<li>{esc(r)}</li>" for r in reasons)
        why = section(f'<h2>Why {client}</h2><ul class="ticks">{lis}</ul>')

    # --- faqs ---------------------------------------------------------
    faqs = ""
    qs = [f for f in (copy.get("faqs") or [])
          if isinstance(f, dict) and f.get("q")]
    if qs:
        blocks = "".join(
            f'<details><summary>{esc(f.get("q"))}</summary>'
            f'<p>{esc(f.get("a"))}</p></details>' for f in qs)
        faqs = section(f'<h2>Questions</h2><div class="faqs">{blocks}</div>')

    logo = (f'<img src="{esc(brief.get("logo"))}" alt="{client}" class="logo">'
            if brief.get("logo") else f'<b class="wordmark">{client}</b>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(copy.get('headline'))} | {client}</title>
<meta name="description" content="{esc(copy.get('subhead'))}">
<style>
  :root{{--primary:{p['primary']};--accent:{p['accent']};
        --on-accent:{p['on_accent']};--radius:{direction['radius']};
        --ink:#1b2733;--muted:#5b6b7c;--line:#e2e8f0}}
  *{{box-sizing:border-box}}
  body{{margin:0;font:16px/1.65 {font};color:var(--ink);background:#fff}}
  .wrap{{max-width:1040px;margin:0 auto;padding:0 22px}}
  header{{padding:16px 0;border-bottom:1px solid var(--line)}}
  .hdr{{display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
  .logo{{height:38px;width:auto;display:block}}
  .wordmark{{font-size:18px;color:var(--primary)}}
  .tel{{margin-left:auto;font-weight:700;color:var(--primary);
        text-decoration:none;font-size:17px}}
  .hero{{{hero_bg};padding:{direction['hero_pad']}}}
  .hero h1{{font-size:clamp(30px,5vw,50px);line-height:1.12;margin:0 0 14px;
            font-weight:{direction['weight']};max-width:17ch}}
  .hero p{{font-size:clamp(16px,2.1vw,20px);margin:0 0 26px;max-width:52ch;
           opacity:.92}}
  .btn{{display:inline-block;background:var(--accent);color:var(--on-accent);
        padding:15px 32px;border-radius:var(--radius);font-weight:700;
        text-decoration:none;font-size:17px;border:0;cursor:pointer}}
  .btn:hover{{filter:brightness(.94)}}
  .sec{{padding:52px 0;border-top:1px solid var(--line)}}
  .sec:first-of-type{{border-top:0}}
  h2{{font-size:clamp(22px,3vw,30px);margin:0 0 26px;color:var(--primary)}}
  h3{{font-size:17px;margin:0 0 6px}}
  p{{margin:0 0 12px}}
  .grid{{display:grid;grid-template-columns:1fr;gap:20px}}
  @media(min-width:760px){{.grid{{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}}}}
  .card{{border:1px solid var(--line);border-radius:var(--radius);padding:22px}}
  .card p{{color:var(--muted);margin:0}}
  .steps{{display:grid;gap:20px}}
  .step{{display:flex;gap:16px;align-items:flex-start}}
  .n{{flex:none;width:34px;height:34px;border-radius:50%;background:var(--accent);
      color:var(--on-accent);display:grid;place-items:center;font-weight:800}}
  .step p{{color:var(--muted);margin:0}}
  .ticks{{list-style:none;padding:0;margin:0;display:grid;gap:11px}}
  .ticks li{{padding-left:28px;position:relative}}
  .ticks li:before{{content:"";position:absolute;left:0;top:8px;width:14px;
      height:8px;border-left:3px solid var(--accent);
      border-bottom:3px solid var(--accent);transform:rotate(-45deg)}}
  details{{border:1px solid var(--line);border-radius:var(--radius);
           padding:15px 18px;margin-bottom:10px}}
  summary{{cursor:pointer;font-weight:600}}
  details p{{margin:10px 0 0;color:var(--muted)}}
  .final{{background:var(--primary);color:{p['on_primary']};
          padding:56px 0;text-align:center}}
  .final h2{{color:inherit;margin:0 0 12px}}
  .final p{{opacity:.9;max-width:46ch;margin:0 auto 26px}}
  footer{{padding:26px 0;font-size:13px;color:var(--muted);text-align:center}}
  form{{display:grid;gap:12px;max-width:440px;margin:0 auto;text-align:left}}
  input,textarea{{padding:13px 15px;border:1px solid #cfd7df;
                  border-radius:var(--radius);font:16px inherit;width:100%}}
</style>
</head>
<body>

<header><div class="wrap hdr">
  {logo}
  {f'<a class="tel" href="tel:{phone}">{phone}</a>' if phone else ''}
</div></header>

<div class="hero"><div class="wrap">
  <h1>{esc(copy.get('headline'))}</h1>
  <p>{esc(copy.get('subhead'))}</p>
  <a class="btn" href="#enquire">{cta}</a>
</div></div>

<div class="wrap">
  {benefits}
  {how}
  {why}
  {faqs}
</div>

<div class="final" id="enquire"><div class="wrap">
  <h2>{cta}</h2>
  <p>Tell us what you need{f' in {area}' if area else ''} and we'll come
     straight back to you.</p>
  <!-- Posts to the Hub's lead panel: stored first, forwarded to Smart 1
       Suite second, so an outage delays a lead rather than losing it. -->
  <form onsubmit="return sendLead(event)">
    <input name="name" placeholder="Your name" required>
    <input name="phone" placeholder="Phone" required>
    <input name="email" type="email" placeholder="Email">
    <textarea name="detail" rows="3" placeholder="What do you need?"></textarea>
    <button class="btn" type="submit">{cta}</button>
    <p id="leadMsg" style="font-size:14px;margin:0"></p>
  </form>
</div></div>

<footer>&copy; <span id="yr"></span> {client}</footer>

<script>
document.getElementById('yr').textContent = new Date().getFullYear();
function sendLead(ev){{
  ev.preventDefault();
  var f = ev.target, msg = document.getElementById('leadMsg');
  var data = {{}};
  new FormData(f).forEach(function(v,k){{ data[k]=v; }});
  if(!data.email && !data.phone){{ msg.textContent='A phone or email is needed.'; return false; }}
  msg.textContent = 'Sending…';
  fetch('{{LEAD_ENDPOINT}}', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      source:'landing', page:{json_client}, client:{json_client},
      fields:data
    }})
  }}).then(function(r){{ return r.json(); }})
    .then(function(){{ f.innerHTML =
      '<p style="font-size:18px;font-weight:600">Thanks — we have your details '+
      'and someone will be in touch shortly.</p>'; }})
    .catch(function(){{ msg.textContent =
      'That did not send. Please call us instead.'; }});
  return false;
}}
</script>
</body>
</html>"""


def with_endpoint(html: str, endpoint: str) -> str:
    """Point the form at wherever the page will actually live.

    Left as a token until export so a page pasted into Smart 1 Sites, a GHL
    funnel or the client's own CMS still reaches the Hub's lead panel — a
    relative URL would post to whatever domain it was pasted onto and quietly
    404.
    """
    return html.replace("{LEAD_ENDPOINT}", endpoint)
