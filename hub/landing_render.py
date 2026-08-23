"""Render a landing page as standalone, self-contained HTML.

One file. No build step, no framework, no external CSS -- it has to be
pasteable into Smart 1 Sites, a GoHighLevel funnel, or a client's own CMS
without carrying a toolchain with it.

## Built to convert, not just to exist

A page of grey boxes and body copy does not convert whatever the words say,
so the layout follows what actually moves a local-services conversion rate:

  - **Something to do, above the fold, on the first screen.** The primary
    action and the phone number are both in the hero and both in a header
    that sticks.
  - **One action, repeated.** Every call to action on the page says the same
    thing and goes to the same form. A page offering three different next
    steps converts on none of them.
  - **Pictures carrying the sections**, from `hub/landing_images.py` -- their
    own site's photography first, stock second.
  - **A bar pinned to the bottom on phones**, where most of this traffic is
    and where the form is otherwise three thumb-scrolls away.
  - **The form asks for as little as will do.** Name, then phone or email --
    the validation requires one of the two, not both.

## Sections are omitted, not filled

If the copy writer had nothing for a section, there is no section -- rather
than a heading over invented content. Every block here checks for real
content first. That is the difference between a page that is short and a page
that lies, and it applies to the pictures too: a row of benefit cards gets
photographs for all of them or for none, because three-with and one-without
reads as a page that failed to load.

## The brand comes from Brandfetch

Their logo, their colours, their fonts. A landing page in someone else's
palette reads as an agency template with a name dropped in, and it converts
like one. Where there is no brand data the fallback is a neutral navy that
does not pretend to be theirs.

Colours and font names arrive from a third party and land unquoted inside a
`<style>` block, so both are validated rather than escaped: anything that is
not literally a hex colour, or a plain family name, is dropped for the
fallback. It loses a brand colour; it does not get to close the style element.
"""
from __future__ import annotations

import html as _html
import json as _json
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
    """A colour we are willing to paste into a stylesheet, or the fallback."""
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


def _img_url(img) -> str:
    """A picture URL we are willing to put in a src, or nothing.

    These come from a stock API or off somebody's website, so the scheme is
    checked rather than assumed: a javascript: or data: url in a src is the
    same hole as one in an href.
    """
    u = str((img or {}).get("url") or "").strip()
    return u if u.startswith("https://") else ""


def render_page(brief: dict, copy: dict, direction: dict,
                images: dict | None = None) -> str:
    images = images or {}
    p = _palette(brief)
    font = _font_stack(brief)
    client = esc(brief.get("client"))
    json_client = _json.dumps(str(brief.get("client") or ""))
    phone = esc(brief.get("phone"))
    cta = esc(copy.get("cta") or "Get started")
    area = esc(brief.get("geo") or
               f"{brief.get('city','')} {brief.get('state','')}".strip())

    hero_img = _img_url(images.get("hero"))
    band_img = _img_url(images.get("band"))
    cards_img = [u for u in (_img_url(c) for c in (images.get("cards") or [])) if u]

    def section(inner: str, cls: str = "") -> str:
        return f'<section class="sec {cls}">{inner}</section>' if inner else ""

    # --- benefits ------------------------------------------------------
    benefits = ""
    items = [b for b in (copy.get("benefits") or [])
             if isinstance(b, dict) and b.get("title")]
    if items:
        # All-or-nothing: pictures for every card, or for none of them.
        pics = cards_img if len(cards_img) >= len(items) else []
        cards = ""
        for i, b in enumerate(items):
            pic = (f'<div class="card-pic" style="background-image:url({pics[i]})"></div>'
                   if pics else "")
            cards += (f'<div class="card">{pic}<div class="card-b">'
                      f'<h3>{esc(b.get("title"))}</h3>'
                      f'<p>{esc(b.get("text"))}</p></div></div>')
        benefits = section(f'<div class="grid">{cards}</div>')

    # --- how it works --------------------------------------------------
    how = ""
    steps = [s for s in (copy.get("how_it_works") or [])
             if isinstance(s, dict) and s.get("step")]
    if steps:
        rows = "".join(
            f'<div class="step"><span class="n">{i}</span>'
            f'<div><h3>{esc(s.get("step"))}</h3><p>{esc(s.get("text"))}</p></div></div>'
            for i, s in enumerate(steps, 1))
        how = section(f'<h2>How it works</h2><div class="steps">{rows}</div>')

    # --- why us --------------------------------------------------------
    why = ""
    reasons = [r for r in (copy.get("why_us") or []) if isinstance(r, str) and r.strip()]
    if reasons:
        lis = "".join(f"<li>{esc(r)}</li>" for r in reasons)
        why = section(f'<h2>Why {client}</h2><ul class="ticks">{lis}</ul>')

    # --- faqs ----------------------------------------------------------
    faqs = ""
    qs = [f for f in (copy.get("faqs") or [])
          if isinstance(f, dict) and f.get("q")]
    if qs:
        blocks = "".join(
            f'<details><summary>{esc(f.get("q"))}</summary>'
            f'<p>{esc(f.get("a"))}</p></details>' for f in qs)
        faqs = section(f'<h2>Questions</h2><div class="faqs">{blocks}</div>')

    # --- a wide band between the content and the form -------------------
    band = ""
    if band_img:
        band = (f'<div class="band" style="background-image:url({band_img})">'
                f'<div class="band-in"><h2>{cta}</h2>'
                f'<a class="btn" href="#enquire">{cta}</a></div></div>')

    logo = (f'<img src="{esc(brief.get("logo"))}" alt="{client}" class="logo">'
            if str(brief.get("logo") or "").startswith("https://")
            else f'<b class="wordmark">{client}</b>')

    tel = f'<a class="tel" href="tel:{phone}">{phone}</a>' if phone else ""

    # The trust row states only what is on the record: where they work, and
    # that a person answers. No badges, no counts, nothing unearned.
    trust = []
    if area:
        trust.append(f"Serving {esc(area)}")
    if phone:
        trust.append("Speak to a person")
    trust_row = ('<ul class="trust">' +
                 "".join(f"<li>{t}</li>" for t in trust) +
                 "</ul>") if trust else ""

    hero_style = (f'background-image:linear-gradient(rgba(8,14,24,.62),'
                  f'rgba(8,14,24,.72)),url({hero_img});background-size:cover;'
                  f'background-position:center' if hero_img else
                  f'background:linear-gradient(150deg,{p["primary"]},'
                  f'{p["accent"]})')
    hero_ink = "#ffffff" if hero_img else p["on_primary"]

    credits = ""
    if images.get("credits"):
        credits = ('<p class="credit">Photography: ' +
                   esc(", ".join(images["credits"])) + "</p>")

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
        --ink:#1b2733;--muted:#5b6b7c;--line:#e4e9ef}}
  *{{box-sizing:border-box}}
  body{{margin:0;font:16px/1.65 {font};color:var(--ink);background:#fff;
        -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1080px;margin:0 auto;padding:0 22px}}
  a{{color:inherit}}

  /* Header sticks so the phone and the action are never scrolled past. */
  header{{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.97);
          backdrop-filter:saturate(1.4) blur(6px);
          border-bottom:1px solid var(--line)}}
  .hdr{{display:flex;align-items:center;gap:14px;padding:11px 0}}
  .logo{{height:36px;width:auto;display:block}}
  .wordmark{{font-size:18px;color:var(--primary)}}
  .hdr-r{{margin-left:auto;display:flex;align-items:center;gap:12px}}
  .tel{{font-weight:700;color:var(--primary);text-decoration:none;font-size:16px}}

  .btn{{display:inline-block;background:var(--accent);color:var(--on-accent);
        padding:15px 30px;border-radius:var(--radius);font-weight:700;
        text-decoration:none;font-size:17px;border:0;cursor:pointer;
        box-shadow:0 6px 18px rgba(15,23,42,.16);transition:transform .12s ease}}
  .btn:hover{{transform:translateY(-1px)}}
  .btn.sm{{padding:10px 18px;font-size:15px;box-shadow:none}}

  .hero{{{hero_style};color:{hero_ink};padding:{direction['hero_pad']}}}
  .hero h1{{font-size:clamp(32px,5.4vw,54px);line-height:1.1;margin:0 0 16px;
            font-weight:{direction['weight']};max-width:18ch;
            text-shadow:{'0 2px 18px rgba(0,0,0,.34)' if hero_img else 'none'}}}
  .hero p.sub{{font-size:clamp(17px,2.2vw,21px);margin:0 0 28px;max-width:54ch;
               opacity:.95}}
  .hero-cta{{display:flex;flex-wrap:wrap;gap:14px;align-items:center}}
  .hero-cta .tel{{color:inherit;opacity:.95}}
  .trust{{list-style:none;display:flex;flex-wrap:wrap;gap:10px 22px;
          padding:0;margin:26px 0 0;font-size:14px;opacity:.92}}
  .trust li{{position:relative;padding-left:20px}}
  .trust li:before{{content:"";position:absolute;left:0;top:6px;width:11px;
      height:6px;border-left:2px solid currentColor;
      border-bottom:2px solid currentColor;transform:rotate(-45deg)}}

  .sec{{padding:56px 0;border-top:1px solid var(--line)}}
  .sec:first-of-type{{border-top:0}}
  h2{{font-size:clamp(23px,3vw,32px);margin:0 0 28px;color:var(--primary)}}
  h3{{font-size:17px;margin:0 0 6px}}
  p{{margin:0 0 12px}}

  .grid{{display:grid;grid-template-columns:1fr;gap:22px}}
  @media(min-width:760px){{.grid{{grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}}}}
  .card{{border:1px solid var(--line);border-radius:var(--radius);
         overflow:hidden;background:#fff;
         box-shadow:0 1px 2px rgba(15,23,42,.04)}}
  .card-pic{{height:168px;background-size:cover;background-position:center}}
  .card-b{{padding:20px 22px}}
  .card p{{color:var(--muted);margin:0}}

  .steps{{display:grid;gap:22px}}
  .step{{display:flex;gap:16px;align-items:flex-start}}
  .n{{flex:none;width:36px;height:36px;border-radius:50%;background:var(--accent);
      color:var(--on-accent);display:grid;place-items:center;font-weight:800}}
  .step p{{color:var(--muted);margin:0}}

  .ticks{{list-style:none;padding:0;margin:0;display:grid;gap:12px}}
  @media(min-width:760px){{.ticks{{grid-template-columns:1fr 1fr;gap:12px 30px}}}}
  .ticks li{{padding-left:28px;position:relative}}
  .ticks li:before{{content:"";position:absolute;left:0;top:8px;width:14px;
      height:8px;border-left:3px solid var(--accent);
      border-bottom:3px solid var(--accent);transform:rotate(-45deg)}}

  .band{{background-size:cover;background-position:center;position:relative;
         padding:88px 22px;text-align:center;color:#fff}}
  .band:before{{content:"";position:absolute;inset:0;
                background:rgba(8,14,24,.58)}}
  .band-in{{position:relative}}
  .band h2{{color:#fff;margin:0 0 20px}}

  details{{border:1px solid var(--line);border-radius:var(--radius);
           padding:15px 18px;margin-bottom:10px}}
  summary{{cursor:pointer;font-weight:600}}
  details p{{margin:10px 0 0;color:var(--muted)}}

  .final{{background:var(--primary);color:{p['on_primary']};
          padding:64px 0 76px;text-align:center}}
  .final h2{{color:inherit;margin:0 0 12px}}
  .final p.lead{{opacity:.92;max-width:48ch;margin:0 auto 28px}}
  form{{display:grid;gap:12px;max-width:440px;margin:0 auto;text-align:left}}
  input,textarea{{padding:14px 15px;border:1px solid #cfd7df;
                  border-radius:var(--radius);font:16px inherit;width:100%}}
  input:focus,textarea:focus{{outline:2px solid var(--accent);outline-offset:1px}}
  .fine{{font-size:12.5px;opacity:.72;margin:14px 0 0}}
  footer{{padding:26px 0 92px;font-size:13px;color:var(--muted);text-align:center}}
  .credit{{font-size:11.5px;color:var(--muted);opacity:.8;margin:8px 0 0}}

  /* Phones are most of this traffic, and the form is otherwise several
     scrolls away from wherever the visitor stopped reading. */
  .dock{{position:fixed;left:0;right:0;bottom:0;z-index:30;display:none;
         gap:10px;padding:10px 14px;background:rgba(255,255,255,.97);
         border-top:1px solid var(--line);
         box-shadow:0 -6px 20px rgba(15,23,42,.10)}}
  .dock a{{flex:1;text-align:center}}
  @media(max-width:759px){{
    .dock{{display:flex}}
    .hdr-r .btn{{display:none}}
  }}
  @media(prefers-reduced-motion:reduce){{.btn{{transition:none}}}}
</style>
</head>
<body>

<header><div class="wrap hdr">
  {logo}
  <div class="hdr-r">{tel}<a class="btn sm" href="#enquire">{cta}</a></div>
</div></header>

<div class="hero"><div class="wrap">
  <h1>{esc(copy.get('headline'))}</h1>
  <p class="sub">{esc(copy.get('subhead'))}</p>
  <div class="hero-cta">
    <a class="btn" href="#enquire">{cta}</a>
    {f'<a class="tel" href="tel:{phone}">or call {phone}</a>' if phone else ''}
  </div>
  {trust_row}
</div></div>

<div class="wrap">
  {benefits}
  {how}
  {why}
</div>

{band}

<div class="wrap">
  {faqs}
</div>

<div class="final" id="enquire"><div class="wrap">
  <h2>{cta}</h2>
  <p class="lead">Tell us what you need{f' in {area}' if area else ''} and we'll come
     straight back to you.</p>
  <!-- Posts to the Hub's lead panel: stored first, forwarded to Smart 1
       Suite second, so an outage delays a lead rather than losing it. -->
  <form onsubmit="return sendLead(event)">
    <input name="name" placeholder="Your name" autocomplete="name" required>
    <input name="phone" placeholder="Phone" autocomplete="tel" inputmode="tel">
    <input name="email" type="email" placeholder="Email" autocomplete="email">
    <textarea name="detail" rows="3" placeholder="What do you need?"></textarea>
    <button class="btn" type="submit">{cta}</button>
    <p id="leadMsg" style="font-size:14px;margin:0"></p>
  </form>
  <p class="fine">A phone number or an email is enough — whichever you prefer.</p>
</div></div>

<footer>
  &copy; <span id="yr"></span> {client}
  {credits}
</footer>

<div class="dock">
  {f'<a class="btn sm" href="tel:{phone}" style="background:#fff;color:var(--primary);border:1px solid var(--line)">Call</a>' if phone else ''}
  <a class="btn sm" href="#enquire">{cta}</a>
</div>

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
    funnel or the client's own CMS still reaches the Hub's lead panel -- a
    relative URL would post to whatever domain it was pasted onto and quietly
    404.
    """
    return html.replace("{LEAD_ENDPOINT}", endpoint)
