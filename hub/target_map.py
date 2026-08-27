"""The target areas, drawn — the map that goes on the proposal.

## Why this exists

A proposal's geography was a table of sentences: "Carmel, IN + 10-mile
radius", "Fishers, IN + 10-mile radius", "Indianapolis DMA". Every word of it
true, and it is the one part of the document a client cannot check against
what they know — the person reading it lives there, and a list of three
sentences does not show them that the three rings overlap, that the whole buy
sits on one side of the city, or that the area they care about is in it. A map
answers all three in the time it takes to look at it.

## The rules it works to

  * **Nothing is invented.** An area is plotted only where a real lookup
    returned a real coordinate for what the rep actually typed. There is no
    "did you mean", no nearest-city fallback, and where a state was named and
    no place in that state matched, the answer is *not found* rather than the
    same city name in another state — a Carmel, Indiana campaign drawn on
    Carmel, California is a wrong answer that looks exactly like a right one.
    The rule `modules/ads_builder/logo.py` works to.

  * **A radius is drawn; a DMA, a state and a national buy are not.**
    This module has no DMA or state boundary data and will not pretend to:
    a hand-drawn blob labelled "Indianapolis DMA" is a claim about coverage
    that nobody can check. Those areas are *named* under the map as covered
    but not drawn, so the reader can see the map is not the whole campaign.
    Absent data reads as absent, never as a shape.

  * **An area that could not be plotted says why.** "We have not looked",
    "there is no such place", "the tile server did not answer" and "this kind
    of area is never drawn" are four different situations, and only two of
    them are somebody's to fix. `render()` returns them per area.

  * **The rep sees the reasons; the client sees the map.** A caveat about a
    geocoder is an internal note. The builder prints what was left out; the
    client document draws the map or omits it entirely, because a proposal
    carrying an empty box headed "Coverage map" is worse than one carrying no
    map at all.

  * **The image is the same image everywhere.** One PNG serves the preview,
    the PDF and the Word export, so the three cannot disagree about where the
    campaign runs — the same reason `_estimate_doc.html` is included twice in
    Smart 1 Ads rather than existing as two templates.

  * **Attribution is printed onto the image, not beside it.** The tiles are
    somebody else's work under a licence that asks for credit, and a credit
    in the HTML of one of three renderers is a credit that travels with none
    of the others.

Nothing here raises. A map is an illustration: a failure costs the document
its picture and must never cost it the section it sits in.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from hub import jsonstore
from hub import target_areas as areas_mod
from hub.config import settings

log = logging.getLogger(__name__)

TILE = 256
MAX_TILES = 40              # one map, not a tile scrape
MAX_ZOOM = 14
MIN_ZOOM = 3
TILE_TIMEOUT = 6
# A wall-clock budget for the whole map, not a timeout per tile.
#
# The PDF route builds this inside a request, and thirty-five tiles against a
# tile server that has gone slow is six rounds of six seconds -- half a
# minute of a rep waiting for a proposal, for a picture. Past the budget a
# tile is not requested at all, which trips the "mostly missing" rule below
# and refuses the map rather than delivering it late. The same shape as the
# scheduler's indexing budget: a count limit alone lets one slow batch hold
# up everything behind it.
MAP_BUDGET = 10.0
GEOCODE_TIMEOUT = 8
MILES_TO_M = 1609.344

# Brand ink, matching the proposal PDF's palette so the map does not read as
# a screenshot from somewhere else.
NAVY = (20, 40, 75)
BLUE = (31, 99, 174)
GOLD = (229, 163, 35)
WHITE = (255, 255, 255)
MUTED = (83, 101, 122)

_MAX_CACHED_PLACES = 4000


def _cache_path() -> str:
    """Where the geocode cache lives.

    Through `jsonstore.data_dir` and computed on use, not a relative literal
    computed at import. A bare "target_map/geocode.json" resolves against
    whatever the process working directory happens to be -- which on this
    deployment is inside the container and wiped on every deploy, and in a
    checkout is the repo root, where it turns up as an untracked directory.
    The same trap `os.environ.get("HUB_DATA_DIR", "data")` set for Page Image
    Optimizer and Tickets.
    """
    return os.path.join(jsonstore.data_dir("target_map"), "geocode.json")

# The composed image, keyed by exactly what was asked for. The preview, the
# PDF and the Word export all ask for the same map within seconds of each
# other, and each one uncached is a dozen tile requests at somebody else's
# expense. Deliberately in memory and small: it is rebuildable, so it must not
# be written to the data disk, and two gunicorn workers each holding a few
# megabytes of PNG is the whole cost.
_RENDER_CACHE: dict[str, tuple[bytes, dict]] = {}
_RENDER_ORDER: list[str] = []
_RENDER_MAX = 16
_LOCK = threading.Lock()

_STATE_CODE_TO_NAME = {code: name.title()
                       for name, code in areas_mod.STATE_NAMES.items()}


def available() -> tuple[bool, str]:
    """Whether a map can be drawn at all, and why not when it cannot."""
    if not settings.map_enabled:
        return False, "Maps are switched off for this deployment (PROPOSAL_MAP)."
    if not settings.map_tile_url:
        return False, "No map tile source is configured (MAP_TILE_URL)."
    try:
        from PIL import Image        # noqa: F401
    except Exception:                # noqa: BLE001  — Pillow is a dependency;
        return False, "Pillow is not available, so no map can be drawn."
    return True, ""


# ---------------------------------------------------------------------------
# Where a place is
# ---------------------------------------------------------------------------
def _cache() -> dict:
    data = jsonstore.read_json(_cache_path(), default={})
    return data if isinstance(data, dict) else {}


def _cache_put(key: str, value: dict) -> None:
    data = _cache()
    if len(data) >= _MAX_CACHED_PLACES:
        # Oldest first. A coordinate does not go stale, so this is a bound on
        # the file rather than an expiry.
        for stale in sorted(data, key=lambda k: data[k].get("at", 0))[:200]:
            data.pop(stale, None)
    data[key] = value
    jsonstore.write_json(_cache_path(), data)


def _split_place(query: str) -> tuple[str, str]:
    """"Carmel, IN" as ("Carmel", "Indiana"). The state may be empty."""
    raw = str(query or "").strip()
    if "," not in raw:
        return raw, ""
    head, tail = raw.rsplit(",", 1)
    tail = tail.strip().strip(".")
    code = tail.upper()
    if code in _STATE_CODE_TO_NAME:
        return head.strip(), _STATE_CODE_TO_NAME[code]
    if tail.lower() in areas_mod.STATE_NAMES:
        return head.strip(), tail.title()
    return raw, ""


def _zip_lookup(zipcode: str) -> dict | None:
    """A five-digit ZIP, through the free postal-code service.

    Deliberately first: a ZIP identifies exactly one place and needs no
    disambiguation, which is the whole difficulty with a city name.
    """
    try:
        r = requests.get(f"https://api.zippopotam.us/us/{zipcode}",
                         timeout=GEOCODE_TIMEOUT,
                         headers={"User-Agent": settings.map_user_agent})
        if r.status_code != 200:
            return None
        place = (r.json().get("places") or [None])[0]
        if not place:
            return None
        return {"lat": float(place["latitude"]), "lon": float(place["longitude"]),
                "label": f"{place.get('place name', zipcode)}, "
                         f"{place.get('state abbreviation', '')}".strip(", "),
                "source": "postal code"}
    except Exception as exc:                        # noqa: BLE001
        log.debug("zip lookup failed for %s: %s", zipcode, exc)
        return None


def _city_lookup(name: str, state: str) -> dict | None:
    """A city, through Open-Meteo's geocoder — free, no key, US-filtered.

    A named state must match. Open-Meteo answers "Carmel" with Carmel-by-the-
    Sea, California first and Carmel, Indiana fourth; taking the first result
    would put an Indiana campaign on the Pacific coast, and the map is the one
    part of the proposal a client checks by recognising it.
    """
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 20, "language": "en",
                                 "format": "json", "countryCode": "US"},
                         timeout=GEOCODE_TIMEOUT,
                         headers={"User-Agent": settings.map_user_agent})
        if r.status_code != 200:
            return None
        results = [x for x in (r.json().get("results") or [])
                   if str(x.get("country_code") or "").upper() == "US"]
    except Exception as exc:                        # noqa: BLE001
        log.debug("city lookup failed for %s: %s", name, exc)
        return None
    if not results:
        return None
    if state:
        wanted = state.strip().lower()
        for row in results:
            if str(row.get("admin1") or "").strip().lower() == wanted:
                return {"lat": float(row["latitude"]), "lon": float(row["longitude"]),
                        "label": f"{row.get('name')}, {row.get('admin1')}",
                        "source": "place name"}
        return None                 # named a state, found nothing in it
    row = results[0]
    return {"lat": float(row["latitude"]), "lon": float(row["longitude"]),
            "label": f"{row.get('name')}, {row.get('admin1') or ''}".strip(", "),
            "source": "place name"}


def geocode(query: str) -> dict | None:
    """Where one origin is, or None. Cached; never raises."""
    raw = str(query or "").strip()
    if not raw:
        return None
    key = raw.lower()
    hit = _cache().get(key)
    if isinstance(hit, dict) and "lat" in hit:
        return hit

    zips = areas_mod.zip_list(raw)
    found = None
    if zips and not re.sub(r"[\d\s,;-]", "", raw):
        found = _zip_lookup(zips[0])
    if not found:
        name, state = _split_place(raw)
        if name:
            found = _city_lookup(name, state)
    if not found and zips:
        found = _zip_lookup(zips[0])
    if not found:
        return None
    found["at"] = int(time.time())
    found["query"] = raw
    try:
        _cache_put(key, found)
    except Exception as exc:                        # noqa: BLE001
        log.debug("geocode cache write failed: %s", exc)
    return found


# ---------------------------------------------------------------------------
# What can be drawn
# ---------------------------------------------------------------------------
# Why an area is not on the map. Each is a different situation and only two of
# them are anybody's to act on, which is the whole reason this is a sentence
# rather than a count.
NOT_DRAWN = {
    areas_mod.DMA: "a DMA — covered by the campaign, and its boundary is not "
                   "drawn here",
    areas_mod.STATEWIDE: "statewide — covered by the campaign, and the state "
                         "outline is not drawn here",
    areas_mod.NATIONAL: "national — the whole country, so there is nothing to "
                        "draw a ring around",
}


def locate(areas) -> dict:
    """Every area, sorted into what can be drawn and what cannot.

        {"points": [{"label", "lat", "lon", "radius", "zips"}],
         "not_plotted": [{"label", "reason", "kind"}]}
    """
    points, missed = [], []
    for area in areas_mod.normalize(areas):
        kind = areas_mod.canonical_type(area.get("type"))
        label = areas_mod.label(area) or areas_mod.describe(area)
        if kind in NOT_DRAWN:
            missed.append({"label": label, "reason": NOT_DRAWN[kind], "kind": kind})
            continue
        origin = str(area.get("origin") or "").strip()
        zips = areas_mod.zip_list(area.get("zips"))
        query = origin or (zips[0] if zips else "")
        if not query:
            missed.append({"label": label, "kind": kind,
                           "reason": "no city or ZIP Code on the area to place it by"})
            continue
        place = geocode(query)
        if not place:
            missed.append({"label": label, "kind": kind,
                           "reason": f"we could not find “{query}” — check the "
                                     "spelling, or add the state"})
            continue
        radius = float(area.get("radius") or 0) if kind == areas_mod.RADIUS else 0.0
        points.append({"label": label, "lat": place["lat"], "lon": place["lon"],
                       "radius": radius, "found": place.get("label") or query,
                       "kind": kind, "zips": len(zips)})
    return {"points": points, "not_plotted": missed}


# ---------------------------------------------------------------------------
# Drawing it
# ---------------------------------------------------------------------------
def _project(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Web-Mercator pixel coordinates at a zoom level."""
    lat = max(min(lat, 85.05), -85.05)
    n = TILE * (2 ** zoom)
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def _metres_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)


def _fetch_tile(session, zoom: int, x: int, y: int, deadline: float = 0.0):
    from PIL import Image
    if deadline and time.monotonic() > deadline:
        return None
    url = (settings.map_tile_url
           .replace("{z}", str(zoom)).replace("{x}", str(x)).replace("{y}", str(y)))
    try:
        r = session.get(url, timeout=TILE_TIMEOUT)
        if r.status_code != 200 or not r.content:
            return None
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as exc:                        # noqa: BLE001
        log.debug("tile %s/%s/%s failed: %s", zoom, x, y, exc)
        return None


def _choose_zoom(points, width: int, height: int) -> int:
    """The closest zoom at which every ring still fits inside the canvas."""
    for zoom in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
        xs, ys = [], []
        for p in points:
            px, py = _project(p["lat"], p["lon"], zoom)
            pad = 0.0
            if p["radius"] > 0:
                pad = (p["radius"] * MILES_TO_M) / _metres_per_pixel(p["lat"], zoom)
            xs += [px - pad, px + pad]
            ys += [py - pad, py + pad]
        span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
        if span_x <= width * 0.86 and span_y <= height * 0.86:
            return zoom
    # Every zoom was too tight for the campaign's spread: this is a national
    # footprint drawn from city rings, and the widest view is the honest one.
    return MIN_ZOOM


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)      # Pillow >= 10.1, scalable
    except Exception:                                 # noqa: BLE001
        try:
            return ImageFont.load_default()
        except Exception:                             # noqa: BLE001
            return None


# The default font Pillow ships covers Latin-1 and not much past it, and a
# glyph it does not have draws as an empty box. Every label here comes out of
# `target_areas.label()`, which legitimately contains an em dash -- so the
# typography is folded to its ASCII equivalent for this one surface rather
# than being left to render as tofu on a document a client reads. Nothing
# else in the Hub does this: the PDF and the browser have real fonts.
_ASCII_FOLD = {"\u2014": "-", "\u2013": "-", "\u2019": "'", "\u2018": "'",
               "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " "}


def _plain(message: str) -> str:
    out = str(message or "")
    for bad, good in _ASCII_FOLD.items():
        out = out.replace(bad, good)
    return out


def _text(draw, xy, message, font, fill, halo=WHITE):
    """Label with a white halo, so it reads over any tile underneath it."""
    x, y = xy
    if halo:
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            draw.text((x + dx, y + dy), message, font=font, fill=halo)
    draw.text((x, y), message, font=font, fill=fill)


def _signature(areas, width: int, height: int) -> str:
    rows = [[areas_mod.canonical_type(a.get("type")), a.get("origin", ""),
             a.get("radius", 0), a.get("dma", ""), a.get("state", ""),
             areas_mod.label(a)] for a in areas_mod.normalize(areas)]
    blob = json.dumps([rows, width, height, settings.map_tile_url], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()


def render(areas, width: int = 1100, height: int = 620) -> tuple[bytes | None, dict]:
    """The campaign's map as PNG bytes, and what it does and does not show.

    Returns ``(None, meta)`` rather than raising or returning a blank canvas:
    a caller drawing an empty grey box headed "Coverage map" on a client
    document is the failure this shape exists to stop. ``meta["reason"]`` says
    which of the ways it went, in words a rep can act on.
    """
    meta = {"plotted": [], "not_plotted": [], "reason": "", "measured": False,
            "attribution": settings.map_tile_attribution, "tiles_missing": 0}
    ok, why = available()
    if not ok:
        meta["reason"] = why
        return None, meta

    key = _signature(areas, width, height)
    with _LOCK:
        cached = _RENDER_CACHE.get(key)
    if cached:
        return cached[0], dict(cached[1])

    placed = locate(areas)
    meta["not_plotted"] = placed["not_plotted"]
    points = placed["points"]
    if not points:
        meta["reason"] = ("Nothing on this campaign can be put on a map yet — "
                          "a target area needs a city or a ZIP Code to be "
                          "placed by.")
        return None, meta

    try:
        png = _compose(points, width, height, meta)
    except Exception as exc:                        # noqa: BLE001
        log.warning("target map failed: %s", exc)
        meta["reason"] = f"The map could not be drawn ({exc})."
        return None, meta
    if png is None:
        return None, meta

    meta["plotted"] = [p["label"] for p in points]
    meta["measured"] = True
    with _LOCK:
        _RENDER_CACHE[key] = (png, dict(meta))
        _RENDER_ORDER.append(key)
        while len(_RENDER_ORDER) > _RENDER_MAX:
            _RENDER_CACHE.pop(_RENDER_ORDER.pop(0), None)
    return png, meta


def _compose(points, width, height, meta) -> bytes | None:
    from PIL import Image, ImageDraw

    zoom = _choose_zoom(points, width, height)
    xs = [_project(p["lat"], p["lon"], zoom)[0] for p in points]
    ys = [_project(p["lat"], p["lon"], zoom)[1] for p in points]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    left, top = cx - width / 2, cy - height / 2

    x0, y0 = int(math.floor(left / TILE)), int(math.floor(top / TILE))
    x1 = int(math.floor((left + width) / TILE))
    y1 = int(math.floor((top + height) / TILE))
    span = 2 ** zoom

    wanted = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
              if 0 <= y < span]
    if len(wanted) > MAX_TILES:
        # Never quietly draw part of the map. A canvas this size is a caller
        # asking for more tiles than one proposal may take off a shared tile
        # server, and half a map with no seam visible is the confidently wrong
        # answer -- it looks like the campaign stops at the edge of the tiles.
        meta["reason"] = ("That map size would need more tiles than one "
                          "proposal may request. Ask for a smaller image.")
        return None

    canvas = Image.new("RGB", (width, height), (233, 238, 244))
    session = requests.Session()
    session.headers.update({"User-Agent": settings.map_user_agent})
    deadline = time.monotonic() + MAP_BUDGET
    with ThreadPoolExecutor(max_workers=6) as pool:
        tiles = list(pool.map(
            lambda t: _fetch_tile(session, zoom, t[0] % span, t[1], deadline),
            wanted))
    missing = 0
    for (tx, ty), img in zip(wanted, tiles):
        if img is None:
            missing += 1
            continue
        canvas.paste(img, (int(tx * TILE - left), int(ty * TILE - top)))
    meta["tiles_missing"] = missing
    # A map mostly made of blank squares is not a map. Better to say the tile
    # server did not answer than to print a grid of grey boxes onto a document
    # a client reads.
    if wanted and missing > len(wanted) / 2:
        meta["reason"] = ("The map service did not answer — no map this time. "
                          "The target areas are unaffected.")
        return None

    # Each ring gets its own layer, composited in turn.
    #
    # ImageDraw *replaces* pixels rather than blending them, so drawing a
    # second translucent ring straight onto the same overlay paints out
    # whatever the first one put there -- which is how the first two rooftops
    # on a three-rooftop campaign came to show one pin between them, with the
    # map otherwise perfect. Compositing per ring is also what makes an
    # overlap read as an overlap, which on a multi-location buy is the single
    # most useful thing the picture says.
    base = canvas.convert("RGBA")
    placed = []
    for p in points:
        px, py = _project(p["lat"], p["lon"], zoom)
        px, py = px - left, py - top
        placed.append((px, py, p))
        if p["radius"] <= 0:
            continue
        ring = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        r = (p["radius"] * MILES_TO_M) / _metres_per_pixel(p["lat"], zoom)
        ImageDraw.Draw(ring).ellipse([px - r, py - r, px + r, py + r],
                                     fill=BLUE + (42,), outline=BLUE + (215,),
                                     width=3)
        base = Image.alpha_composite(base, ring)

    pins = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(pins)
    label_font = _font(15)
    for ix, (px, py, _p) in enumerate(placed, 1):
        draw.ellipse([px - 10, py - 10, px + 10, py + 10],
                     fill=GOLD + (255,), outline=NAVY + (255,), width=2)
        if label_font:
            mark = str(ix)
            box = draw.textbbox((0, 0), mark, font=label_font)
            draw.text((px - (box[2] - box[0]) / 2, py - (box[3] - box[1]) / 2 - 1),
                      mark, font=label_font, fill=NAVY + (255,))
    canvas = Image.alpha_composite(base, pins).convert("RGB")
    canvas = _crop_to_campaign(canvas, points, zoom, left, top)

    canvas = _legend(canvas, points, meta)
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _crop_to_campaign(canvas, points, zoom, left, top):
    """Trim the tiles down to what the campaign actually covers.

    Zoom is a whole number, so the closest one that fits every ring inside the
    canvas is often a long way inside it -- two showrooms nine miles apart
    came out as a pair of circles in the middle of an otherwise empty county.
    Cropping is what an extra half-step of zoom would have done, without
    resampling the tiles into mush.
    """
    xs, ys = [], []
    for p in points:
        px, py = _project(p["lat"], p["lon"], zoom)
        px, py = px - left, py - top
        pad = 0.0
        if p["radius"] > 0:
            pad = (p["radius"] * MILES_TO_M) / _metres_per_pixel(p["lat"], zoom)
        xs += [px - pad, px + pad]
        ys += [py - pad, py + pad]
    margin = max(46, 0.1 * max(max(xs) - min(xs), max(ys) - min(ys)))
    box = [min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin]
    # Never crop below something that still reads as a map, and never outside
    # the tiles actually fetched -- past the edge is white, which reads as the
    # world ending rather than as a crop.
    if box[2] - box[0] < 460 or box[3] - box[1] < 300:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        half_w, half_h = max(230, (box[2] - box[0]) / 2), max(150, (box[3] - box[1]) / 2)
        box = [cx - half_w, cy - half_h, cx + half_w, cy + half_h]
    box = [max(0, int(box[0])), max(0, int(box[1])),
           min(canvas.width, int(box[2])), min(canvas.height, int(box[3]))]
    if box[2] - box[0] < 200 or box[3] - box[1] < 140:
        return canvas
    return canvas.crop(tuple(box))


def _legend(canvas, points, meta):
    """The numbered key and the tile credit, drawn onto the image itself.

    Onto the image because three renderers show this picture and a caption
    written in one of them travels with none of the others -- and the credit
    is a licence condition rather than a nicety.
    """
    from PIL import Image, ImageDraw

    font = _font(15)
    small = _font(12)
    rows = points[:12]
    row_h = 22
    strip = 14 + row_h * len(rows) + 22
    out = Image.new("RGB", (canvas.width, canvas.height + strip), WHITE)
    out.paste(canvas, (0, 0))
    draw = ImageDraw.Draw(out)
    y = canvas.height + 10
    for ix, p in enumerate(rows, 1):
        draw.ellipse([16, y + 2, 32, y + 18], fill=GOLD, outline=NAVY, width=2)
        if font:
            box = draw.textbbox((0, 0), str(ix), font=font)
            draw.text((24 - (box[2] - box[0]) / 2, y + 10 - (box[3] - box[1]) / 2 - 1),
                      str(ix), font=font, fill=NAVY)
            ring = (f"{int(p['radius'])}-mile radius" if p["radius"] > 0
                    else "target area")
            draw.text((42, y + 3), _plain(f"{p['label']}  -  {ring}"),
                      font=font, fill=NAVY)
        y += row_h
    if len(points) > len(rows) and font:
        draw.text((42, y + 2), f"+ {len(points) - len(rows)} more target areas",
                  font=font, fill=MUTED)
        y += row_h
    if small:
        draw.text((16, out.height - 18), _plain(meta.get("attribution") or ""),
                  font=small, fill=MUTED)
    return out
