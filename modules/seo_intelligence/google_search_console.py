"""Thin Search Console REST client used by the weekly SEO intelligence refresh."""
from __future__ import annotations

from urllib.parse import quote
import requests

TIMEOUT = 30
WEBMASTERS = "https://www.googleapis.com/webmasters/v3"
INSPECTION = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"


class SearchConsoleError(RuntimeError):
    pass


def _call(method, url, token, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    if method != "GET":
        headers.setdefault("Content-Type", "application/json")
    response = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
    try:
        from hub import quotas
        quotas.record_google(url, module="seo_intelligence", ok=response.ok)
    except Exception:
        pass
    if not response.ok:
        try:
            detail = (response.json().get("error") or {}).get("message")
        except Exception:
            detail = response.text[:500]
        raise SearchConsoleError(f"{response.status_code}: {detail or 'Search Console request failed'}")
    return response.json() if response.text else {}


def list_sites(token):
    return _call("GET", f"{WEBMASTERS}/sites", token).get("siteEntry", [])


def search_analytics(token, site_url, start_date, end_date, dimensions=("query", "page"), row_limit=25000, start_row=0):
    url = f"{WEBMASTERS}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
    body = {
        "startDate": str(start_date),
        "endDate": str(end_date),
        "dimensions": list(dimensions),
        "rowLimit": min(int(row_limit), 25000),
        "startRow": max(0, int(start_row)),
        "dataState": "final",
    }
    return _call("POST", url, token, json=body).get("rows", [])


def all_search_analytics(token, site_url, start_date, end_date, dimensions=("query", "page"), max_rows=100000):
    rows = []
    start = 0
    while start < max_rows:
        batch = search_analytics(token, site_url, start_date, end_date, dimensions, 25000, start)
        rows.extend(batch)
        if len(batch) < 25000:
            break
        start += len(batch)
    return rows[:max_rows]


def list_sitemaps(token, site_url):
    url = f"{WEBMASTERS}/sites/{quote(site_url, safe='')}/sitemaps"
    return _call("GET", url, token).get("sitemap", [])


def submit_sitemap(token, site_url, sitemap_url):
    url = f"{WEBMASTERS}/sites/{quote(site_url, safe='')}/sitemaps/{quote(sitemap_url, safe='')}"
    return _call("PUT", url, token)


def delete_sitemap(token, site_url, sitemap_url):
    url = f"{WEBMASTERS}/sites/{quote(site_url, safe='')}/sitemaps/{quote(sitemap_url, safe='')}"
    return _call("DELETE", url, token)


def inspect_url(token, site_url, inspection_url, language="en-US"):
    body = {"inspectionUrl": inspection_url, "siteUrl": site_url, "languageCode": language}
    return _call("POST", INSPECTION, token, json=body).get("inspectionResult", {})
