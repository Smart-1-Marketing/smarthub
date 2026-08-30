"""One-off: derive Google's responsive display assets from their Meta twins.

Google composes a responsive display ad from image assets rather than taking a
finished banner, and asks for three shapes: 1.91:1 landscape, 1:1 square and
4:5 portrait, recommended at 1200x628, 1200x1200 and 1200x1500.

Two of those are shapes every family already draws for Meta, exactly 10/9
smaller -- 1080x1080 is the square and 1080x1350 is the portrait. So the
geometry is a scale rather than a design decision, and deriving it is the
honest way to author it. (1200x628 needed nothing at all: Meta's link ad is
already that size.) A shape with no twin would not be derivable like this and
would have to be drawn by hand the way the rest of these files were.

Rounding is to whole pixels. `size` bands scale with the canvas, because type
that held its 1080 size on a 1200 canvas would render 10% smaller relative to
everything around it. Everything that is not a length -- maxLines, lineHeight,
opacity, fit, align, colour names, and the Meta safe zone, which belongs to
Meta's interface and not to a Google asset -- is carried across untouched,
except that the safe zone is dropped for exactly that reason.

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


# Which Google asset comes from which Meta shape. Both pairs are the identical
# aspect ratio 10/9 apart; nothing else in these files is.
DERIVATIONS = [
    ("1080x1080", "1200x1200", (1200, 1200)),
    ("1080x1350", "1200x1500", (1200, 1500)),
]

changed = 0
for f in sorted(glob.glob("src/templates/T*.json")):
    d = json.load(open(f))
    wrote = []
    for src_key, target_key, (w, h) in DERIVATIONS:
        src = d["sizes"].get(src_key)
        if not src:
            print(f"{f}: no {src_key} to derive {target_key} from — skipped")
            continue
        derived = scale_node(src)
        derived["canvas"] = {"w": w, "h": h}
        # Meta reserves the top and bottom of a story for its own interface.
        # A Google responsive asset is composed into a unit Google draws, so
        # carrying Meta's exclusion zone across would reserve space against a
        # platform that is not showing it.
        derived.pop("safeZone", None)
        if d["sizes"].get(target_key) == derived:
            continue
        # Keep the file readable: each derived shape sits beside the one it
        # came from rather than at the end.
        sizes = {}
        for k, v in d["sizes"].items():
            sizes[k] = v
            if k == src_key:
                sizes[target_key] = derived
        if target_key not in sizes:
            sizes[target_key] = derived
        d["sizes"] = sizes
        wrote.append(f"{target_key} from {src_key}")
    if wrote:
        with open(f, "w") as fh:
            json.dump(d, fh, indent=2)
            fh.write("\n")
        changed += 1
        print(f"{f}: " + ", ".join(wrote))
    else:
        print(f"{f}: already current")

print(f"{changed} template(s) updated")
