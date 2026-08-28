"""One-off: derive the 1200x1200 layout in each template family from its 1080x1080.

Google's responsive display ads take a 1:1 square image asset, recommended at
1200x1200. Every family already carries a hand-authored 1080x1080 for Meta's
feed square, and 1200x1200 is that exact same shape 10/9 larger -- so the
geometry is a scale, not a design decision, and deriving it is the honest way
to author it. A new aspect ratio would not be derivable like this and would
have to be drawn by hand the way every other size in these files was.

Rounding is to whole pixels. `size` bands scale with the canvas because type
that held its 1080 size on a 1200 canvas would render 10% smaller relative to
everything around it. Everything that is not a length -- maxLines, lineHeight,
opacity, fit, align, colour names -- is carried across untouched.

Run once; the output is committed. Re-running is a no-op if the numbers match.
"""

import json
import glob

SCALE = 1200 / 1080

# Keys whose value is a length in canvas pixels.
LENGTHS = {"x", "y", "w", "h", "radius", "safe", "top", "bottom", "left", "right"}


def scale_value(key, value):
    if key == "size" and isinstance(value, list):
        return [round(v * SCALE) for v in value]
    if key in LENGTHS and isinstance(value, (int, float)):
        return round(value * SCALE)
    return value


def scale_node(node, key=None):
    if isinstance(node, dict):
        return {k: scale_node(v, k) for k, v in node.items()}
    if isinstance(node, list):
        if key == "size":
            return scale_value(key, node)
        return [scale_node(v) for v in node]
    return scale_value(key, node)


changed = 0
for f in sorted(glob.glob("src/templates/T*.json")):
    d = json.load(open(f))
    src = d["sizes"].get("1080x1080")
    if not src:
        print(f"{f}: no 1080x1080 to derive from — skipped")
        continue
    derived = scale_node(src)
    derived["canvas"] = {"w": 1200, "h": 1200}
    if d["sizes"].get("1200x1200") == derived:
        print(f"{f}: 1200x1200 already current")
        continue
    # Keep the key order of the file readable: insert next to the other square.
    sizes = {}
    for k, v in d["sizes"].items():
        sizes[k] = v
        if k == "1080x1080":
            sizes["1200x1200"] = derived
    if "1200x1200" not in sizes:
        sizes["1200x1200"] = derived
    d["sizes"] = sizes
    with open(f, "w") as fh:
        json.dump(d, fh, indent=2)
        fh.write("\n")
    changed += 1
    print(f"{f}: 1200x1200 derived from 1080x1080")

print(f"{changed} template(s) updated")
