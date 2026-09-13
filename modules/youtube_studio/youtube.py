"""Bounded YouTube API calls, explicit ownership and useful provider errors."""
import os
import re
import time
from urllib.parse import urlsplit
import requests
from . import store

ROOT = "https://www.googleapis.com/youtube/v3/"
SCOPES = ("https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/yt-analytics.readonly")


def record_google_request(method, url, **kwargs):
    """Meter each attempt without storing query credentials or upload sessions."""
    from hub.quotas import record_google
    host = urlsplit(url).hostname
    safe_url = ROOT if host == "www.googleapis.com" else "https://" + str(host) + "/"
    ok = False
    try:
        response = getattr(requests, method.lower())(url, **kwargs)
        ok = response.ok
        return response
    finally:
        record_google(safe_url, module="youtube_studio", ok=ok)


def oauth_config():
    from modules.google_access import config
    return config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET, config.PUBLIC_BASE_URL + "/connect/callback"


def status():
    cid, secret, redirect = oauth_config()
    missing = []
    if not cid or not secret:
        missing.append("Google OAuth client")
    try:
        store.cipher()
    except ValueError:
        missing.append("Token encryption")
    if not redirect.startswith("https://"):
        missing.append("Public HTTPS address")
    return {"connect_ready": not missing, "missing": missing,
            "public_lookup_ready": bool(os.environ.get("YOUTUBE_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
            "redirect_uri": redirect}


def checked(response):
    if response.ok:
        return response.json() if response.content else {}
    # Never echo request URLs, bearer tokens or arbitrary provider response text.
    try:
        reason = response.json().get("error", {}).get("errors", [{}])[0].get("reason", "")
    except (ValueError, AttributeError, IndexError):
        reason = ""
    messages = {
        "quotaExceeded": "YouTube's daily API allowance has been reached. Try again after it resets.",
        "accessNotConfigured": "Enable YouTube Data API v3 for this Google project.",
        "insufficientPermissions": "Reconnect the channel and allow the requested YouTube permissions.",
        "uploadLimitExceeded": "This channel has reached its YouTube upload limit.",
        "forbidden": "YouTube refused this action. Check channel ownership and granted permissions.",
    }
    raise ValueError(messages.get(reason, f"YouTube could not complete this request (HTTP {response.status_code})."))


def api(resource, params=None, token=None, method="GET", body=None):
    params = dict(params or {})
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    else:
        params["key"] = os.environ.get("YOUTUBE_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        if not params["key"]:
            raise ValueError("Public lookup needs YOUTUBE_API_KEY. You can still save a channel link and connect its owner.")
    return checked(record_google_request(method, ROOT + resource, params=params,
                                   json=body, headers=headers, timeout=30))


def parse_channel(value):
    raw = str(value or "").strip()
    if re.fullmatch(r"UC[\w-]{22}", raw):
        return "id", raw
    if raw.startswith("@") and re.fullmatch(r"@[\w.\-\u0080-\uffff]{3,30}", raw):
        return "forHandle", raw
    parsed = urlsplit(raw if "://" in raw else "https://" + raw)
    if parsed.scheme != "https" or parsed.hostname not in ("youtube.com", "www.youtube.com", "m.youtube.com") or parsed.username or parsed.port:
        raise ValueError("Paste a YouTube channel URL, @handle or channel ID.")
    parts = parsed.path.strip("/").split("/")
    if parts[0].startswith("@"):
        return parse_channel(parts[0])
    if len(parts) == 2 and parts[0] == "channel":
        return parse_channel(parts[1])
    if len(parts) == 2 and parts[0] == "user" and re.fullmatch(r"[\w.-]{1,100}", parts[1]):
        return "forUsername", parts[1]
    raise ValueError("Use the channel's @handle or /channel/ URL, rather than a video or custom /c/ link.")


def shape(item):
    sn = item.get("snippet", {})
    return {"id": item["id"], "title": sn.get("title", item["id"]),
        "description": sn.get("description", ""),
        "thumbnail": sn.get("thumbnails", {}).get("default", {}).get("url", ""),
        "url": "https://www.youtube.com/channel/" + item["id"],
        "statistics": item.get("statistics", {}), "refreshed_at": time.time(),
        "uploads": item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")}


def resolve(value, token=None):
    key, val = parse_channel(value)
    items = api("channels", {"part": "snippet,statistics,contentDetails", key: val}, token).get("items", [])
    if not items:
        raise ValueError("No channel was found for that address.")
    return shape(items[0])


def access_token(name, channel_id):
    channel = store.read().get("clients", {}).get(store.client_key(name), {}).get("channels", {}).get(channel_id)
    if not channel or not channel.get("refresh_token"):
        raise ValueError("Connect this channel's owner before managing it.")
    cid, secret, _ = oauth_config()
    response = record_google_request("POST", "https://oauth2.googleapis.com/token", data={"client_id": cid,
        "client_secret": secret, "refresh_token": store.decrypt(channel["refresh_token"]),
        "grant_type": "refresh_token"}, timeout=20)
    if not response.ok:
        store.update(lambda data: store.client(data, name)["channels"][channel_id].update(
            connection_error="Reconnect required"))
        raise ValueError("This YouTube connection needs reconnecting. Create a new access link.")
    token = response.json().get("access_token")
    if not token:
        raise ValueError("Google did not return access to this channel.")
    owned = api("channels", {"part": "id", "mine": "true"}, token).get("items", [])
    if channel_id not in [x["id"] for x in owned]:
        raise ValueError("The connected Google account no longer owns this channel. Reconnect the correct owner.")
    return token


def videos(channel, token=None):
    playlist = channel.get("uploads")
    if not playlist:
        return []
    rows = api("playlistItems", {"part": "contentDetails", "playlistId": playlist, "maxResults": 25}, token)
    ids = [r.get("contentDetails", {}).get("videoId") for r in rows.get("items", [])]
    if not ids:
        return []
    result = api("videos", {"part": "snippet,statistics,contentDetails,status", "id": ",".join(ids)}, token)
    return [{"id": v["id"], "title": v["snippet"].get("title", ""),
        "description": v["snippet"].get("description", ""), "published_at": v["snippet"].get("publishedAt"),
        "thumbnail": v["snippet"].get("thumbnails", {}).get("medium", {}).get("url", ""),
        "statistics": v.get("statistics", {}), "status": v.get("status", {}),
        "url": "https://www.youtube.com/watch?v=" + v["id"]} for v in result.get("items", [])]


def review(channel):
    findings = []
    if len(channel.get("description", "").strip()) < 80:
        findings.append({"title": "Describe the business", "detail": "The channel description is short. Explain the services, audience and next step.", "priority": "High"})
    rows = channel.get("videos", [])
    for video in rows:
        if not re.search(r"https?://", video.get("description", "")):
            findings.append({"title": "Add a useful next step", "detail": video["title"] + ": no website link found in the description.", "video_id": video["id"], "priority": "Medium"})
        if len(video.get("title", "")) < 15:
            findings.append({"title": "Make the title more specific", "detail": video["title"], "video_id": video["id"], "priority": "Medium"})
    return {"checked_at": time.time(), "sample_size": len(rows), "findings": findings,
        "note": "Metadata review of up to 25 recent videos. This does not measure rankings, thumbnail quality or viewer retention."}
