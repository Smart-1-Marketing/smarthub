## Opportunistic migration — read this before editing any module

`hub/storage.py` (Cloudinary), `hub/images.py` (resize/convert),
`hub/jsonstore.py` (persisted JSON) and `hub/config.py` (settings) are the
shared implementations. **They are used by almost none of the modules.** Instead, 15 modules configure Cloudinary
themselves, 6 have their own resize code, and 55 files read environment
variables directly.

This has already caused real bugs twice. The "cap the longest edge before
converting" rule had to be found and fixed in several places separately. And
when the Pexels key was named `PEXELS_API` rather than `PEXELS_API_KEY`, the
fix went into `hub/config.py` — and the tool was still broken, because Image
Creator never called `config.py`. It had to be fixed a second time.

**The rule: when you are already editing a module for another reason, move
that module's Cloudinary, image and settings code onto the shared versions
while you are in there.** Not as a separate project — a big-bang rewrite of 22
working modules is risk with no feature at the end of it. But never leave a
module you have just touched still doing its own thing.

What that means in practice:

    cloudinary.config(...) + cloudinary.uploader.upload(...)
        -> from hub.storage import put;  put(data, kind="seo_images", ...)

    Image.open(...).save(..., "WEBP")
        -> from hub.images import optimise;  optimise(data, max_edge=1600)

    os.environ.get("PEXELS_API_KEY")
        -> from hub.config import settings;  settings.pexels_key
           (config already accepts every spelling in use)

    open(path, "w") + json.dump(...)   /   open(path) + json.load(...)
        -> from hub import jsonstore
           jsonstore.write_json(path, data)   # atomic, and mirrored
           jsonstore.read_json(path, default=[])
           jsonstore.delete_json(path)        # never bare os.remove

    base = "/var/data" if os.path.isdir("/var/data") else .../"data"
        -> jsonstore.data_dir("my_module")

If a shared function does not do what the module needs, extend the shared one
rather than keeping the local copy. That is the whole point — the next fix
should land once.
