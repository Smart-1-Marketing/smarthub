"""Four image sources, equal citizens, for one trigger's chosen ad.

Never limit the stock sources: `hub/stock_search.py` already fans a query
out to Pexels, Pixabay, Unsplash and our own Cloudinary library in parallel
and merges the results, so this module reads that rather than picking a
favorite provider or adding a fourth copy of the fan-out — the same reader
`modules/stock_photos` uses. Upload, the client's own gallery, and a
generated image round the tab bar out to four; none of the four is a
fallback for another.

**Every chosen image is copied into Cloudinary at selection time.** A stock
provider rotates and deletes; an ad whose image 404s three months in is the
failure this exists to prevent. `select()` is where that copy happens, and
the license payload travels with it, because "we can't find the license for
this creative" is not a position to be in.
"""
from __future__ import annotations

from hub import stock_search

THIRD_PARTY = set(stock_search.THIRD_PARTY)
MAX_QUERY = 200


def search(query: str, per_page: int = 12) -> dict:
    """Stock + our library, fanned out in parallel. Never raises."""
    q = str(query or "").strip()[:MAX_QUERY]
    if not q:
        return {"results": [], "sources": {}, "error": "Type a search term."}
    found = stock_search.search([q], sources=list(stock_search.SOURCES),
                                per_page=per_page, limit=per_page * len(stock_search.SOURCES))
    return {"results": found.get("results", []), "sources": found.get("sources", {}),
            "cached": bool(found.get("cached"))}


def gallery(client: str, limit: int = 30) -> dict:
    """The client's own prior uploads and shots, one tab of the four."""
    try:
        from hub.client_context import gallery_images
    except Exception as exc:                              # noqa: BLE001
        return {"results": [], "error": f"The gallery is unavailable: {exc}"}
    images, note = gallery_images(client, limit=limit)
    results = [{"id": f"gallery:{im.get('public_id', '')}", "provider": "gallery",
               "thumbnail": im.get("url", ""), "preview_url": im.get("url", ""),
               "full_url": im.get("url", ""), "width": im.get("width"),
               "height": im.get("height"), "already_ours": True,
               "public_id": im.get("public_id", "")} for im in images]
    return {"results": results, "note": note}


def generate(prompt: str, client: str = "") -> dict:
    """A Cloudinary-hosted image drawn from the ad's own headline. A draft
    like any other generated image in this Hub -- kept only when selected."""
    try:
        from hub import ai as hub_ai
        from hub import storage
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"Image generation is unavailable: {exc}"}
    try:
        data = hub_ai.image(prompt, module="weather_setup", purpose="ad_image",
                            size="1024x1024")
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False,
                "error": f"We could not generate an image right now ({exc})."}
    try:
        asset = storage.put("weather_setup", "generated.png", data, client=client)
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The image could not be stored: {exc}"}
    return {"ok": True, "results": [{
        "id": f"generated:{asset.public_id}", "provider": "generated",
        "thumbnail": asset.url, "preview_url": asset.url, "full_url": asset.url,
        "already_ours": True, "public_id": asset.public_id,
    }]}


def select(*, client: str, trigger_id: str, image_id: str, provider: str,
          url: str, public_id: str = "", width=None, height=None,
          source_url: str = "") -> dict:
    """Copy a chosen image into Cloudinary (unless it already is one) and
    return the record `store.py` files onto the pick. Never raises.

    A stock provider's own CDN URL is never stored as the final asset: it
    rotates and deletes, and this is what stops an approved ad silently
    losing its picture months later.
    """
    if provider in ("gallery", "generated") or public_id:
        return {"ok": True, "asset": {
            "source": "gallery" if provider == "gallery" else "generated",
            "provider": provider, "provider_id": image_id, "provider_url": url,
            "cloudinary_public_id": public_id, "url": url, "license_json": {},
            "credit_required": False, "width": width, "height": height,
        }}
    if not url:
        return {"ok": False, "error": "That image has no address to copy."}
    try:
        from hub import storage
        asset = storage.put_remote(
            "weather_setup", url, client=client,
            filename=f"{provider}-{trigger_id}.jpg")
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The image could not be stored: {exc}"}
    credit = provider in THIRD_PARTY
    return {"ok": True, "asset": {
        "source": "stock" if provider != "upload" else "upload",
        "provider": provider, "provider_id": image_id, "provider_url": url,
        "cloudinary_public_id": asset.public_id, "url": asset.url,
        "width": width, "height": height, "credit_required": credit,
        "license_json": ({"provider": provider, "source_url": source_url or url,
                          "note": f"{provider.title()} — free for commercial "
                                  "use, credit not required by license terms "
                                  "but the source is kept on file."}
                         if credit else {}),
    }}


def upload(*, client: str, trigger_id: str, data: bytes, filename: str) -> dict:
    """A file the client sent straight from the wizard."""
    if not data:
        return {"ok": False, "error": "No file was received."}
    try:
        from hub import storage
        asset = storage.put("weather_setup", filename or f"upload-{trigger_id}.jpg",
                            data, client=client)
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The upload could not be stored: {exc}"}
    return {"ok": True, "asset": {
        "source": "upload", "provider": "client", "provider_id": "",
        "provider_url": "", "cloudinary_public_id": asset.public_id,
        "url": asset.url,
        "width": None, "height": None, "credit_required": False, "license_json": {},
    }}
