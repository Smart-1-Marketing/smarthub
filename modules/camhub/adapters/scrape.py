"""The scrape queue: a fact a conditions page needs that exists on a web
page and in no API.

At Buckeye Lake there is exactly one: the normal and winter pool elevations
(891.6 ft and 888.6 ft) in a table on ODNR's winter-drawdown page. Without
them the USGS reading is a meaningless number; with them it becomes "half a
foot above normal." That table changes once a year, so this is an annual
refresh with a value cached in between, never live scraping.

The rules that keep this out of trouble, each one enforced here:

- **robots.txt first.** A disallowed path is refused, not fetched.
- **Public, factual, low-volume.** One request a year to a state agency
  page. `fetch` is only ever called on the source's cadence.
- **Identify yourself** -- the same User-Agent as every other adapter.
- **A value outside a sane range holds the last good one.** `fetch` raises
  when the extraction finds nothing or the number moved further than
  `max_change` from what was last stored; the store then keeps the last good
  payload and raises the failure on the health screen.
- **A seeded value is a value.** A target whose URL the operator has not
  confirmed yet serves its `seed` -- the numbers the spec resolved -- with
  the seed date as its fetch time, and says so.

Never a substitute for an API that exists, and never a competitor's cam page.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.robotparser

from .http import SourceError, get_text, user_agent


def robots_allows(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(get_text(robots_url).splitlines())
    except SourceError:
        return True  # no robots file is not a refusal
    return rp.can_fetch(user_agent(), url)


def extract(html: str, rules: dict) -> dict:
    """Each rule is a regex with one capturing group, applied to the page
    with tags stripped. Every rule must match, or nothing is returned."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    out = {}
    for field, pattern in (rules or {}).items():
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            raise SourceError(f"scrape: '{field}' not found on the page")
        raw = m.group(1).strip()
        try:
            out[field] = float(raw.replace(",", ""))
        except ValueError:
            out[field] = raw
    return out


def probe(lat: float, lon: float, location_type: str = "inland_lake") -> list[dict]:
    """A scrape target is proposed by the builder from the location type,
    not discovered from a coordinate; the probe names the gap."""
    if location_type == "inland_lake":
        return [{"key": "pool_elevation", "adapter": "scrape", "label": "Normal pool elevation",
                 "found": False, "config": {"rules": {}, "url": ""},
                 "detail": "a managed lake's pool elevation is not in any API; "
                           "the operator confirms the agency page and the selector"}]
    return []


def fetch(config: dict) -> dict:
    url = str(config.get("url") or "").strip()
    rules = config.get("rules") or {}
    seed = config.get("seed") or {}
    if not url or not rules:
        if seed:
            return {**seed, "seeded": True, "seed_date": config.get("seed_date"),
                    "source": config.get("seed_source") or "seeded",
                    "note": "URL and selector not yet confirmed; serving the seeded values"}
        raise SourceError("scrape: no URL, no rules and no seed")
    if not robots_allows(url):
        raise SourceError(f"scrape: robots.txt disallows {url}")
    values = extract(get_text(url), rules)
    last = config.get("last_good") or seed
    max_change = config.get("max_change")
    if max_change and last:
        for field, value in values.items():
            prev = last.get(field)
            if isinstance(value, (int, float)) and isinstance(prev, (int, float)) \
                    and abs(value - prev) > float(max_change):
                raise SourceError(f"scrape: '{field}' moved from {prev} to {value}, "
                                  f"more than {max_change}; holding the last good value")
    return {**values, "seeded": False, "source": config.get("source_label") or url, "url": url}
