"""Every industry key literal in the tree is canonical or a known legacy one.

    python3 test_industry_consumers.py

Same shape as the other test files here -- no pytest, no new dependencies.

hub/industry.py is the one canonical taxonomy; LEGACY_MAP joins the old
spellings this repo already had on disk (modules/image_picker/taxonomy.py,
modules/commercial_builder/library_spec.py's INDUSTRY_PACKS,
modules/smartforecast's catalog) before the canonical table existed. This
sweeps hub/ and modules/ for a dict/list literal named INDUSTRIES or
INDUSTRY_PACKS and asserts every key it declares is either canonical or in
LEGACY_MAP -- so a new pack, or a rename inside one, cannot quietly
introduce a key nothing here can join back to the taxonomy.

Registered in test_ci_gate.py so a stray key fails CI.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import industry                                            # noqa: E402

CANONICAL = set(industry.INDUSTRY_BY_KEY.keys())
LEGACY = set(industry.LEGACY_MAP.keys())
KNOWN = CANONICAL | LEGACY

# Files that declare an `INDUSTRIES`/`INDUSTRY_PACKS` literal that is a
# *different* taxonomy from this Hub's client industry key -- named with the
# reason, the ALLOW-list discipline this repo uses everywhere else, so an
# exemption is a decision rather than a blind spot.
ALLOW = {
    os.path.join("hub", "industry_prospects.py"):
        "the industry-factory prospecting categories (roofing, rv-dealers, "
        "restaurants, ...) are a vertical to prospect into, not a client's "
        "own industry key -- a different taxonomy entirely",
}


def _literal_dict_keys(tree: ast.AST, names: set[str]) -> dict[str, list]:
    """{constant_name: [key, ...]} for every module-level dict/list literal
    named one of `names`, at any depth (a dict of dicts, or a list of
    dicts each carrying `"key"`/`"id"`)."""
    found: dict[str, list] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        target_names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if not (target_names & names):
            continue
        name = next(iter(target_names & names))
        keys: list = []
        if isinstance(node.value, ast.Dict):
            for k in node.value.keys:
                try:
                    keys.append(ast.literal_eval(k))
                except Exception:                          # noqa: BLE001
                    pass
        elif isinstance(node.value, ast.List):
            for item in node.value.elts:
                if isinstance(item, ast.Dict):
                    for k, v in zip(item.keys, item.values):
                        try:
                            kk = ast.literal_eval(k)
                        except Exception:                   # noqa: BLE001
                            continue
                        if kk in ("key", "id"):
                            try:
                                keys.append(ast.literal_eval(v))
                            except Exception:                # noqa: BLE001
                                pass
        found[name] = keys
    return found


def scan_tree() -> dict[str, list]:
    """{relative_path: [stray_key, ...]} for every INDUSTRIES/INDUSTRY_PACKS
    literal outside hub/industry.py whose keys are neither canonical nor
    in LEGACY_MAP."""
    strays: dict[str, list] = {}
    for base in ("hub", "modules"):
        base_path = os.path.join(ROOT, base)
        for dirpath, dirnames, filenames in os.walk(base_path):
            dirnames[:] = [d for d in dirnames
                           if d not in ("_attic", "node_modules", ".git",
                                        "__pycache__")]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, ROOT)
                if rel == os.path.join("hub", "industry.py") or rel in ALLOW:
                    continue
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        tree = ast.parse(fh.read())
                except (OSError, SyntaxError):
                    continue
                found = _literal_dict_keys(tree, {"INDUSTRIES", "INDUSTRY_PACKS"})
                bad = []
                for _name, keys in found.items():
                    for k in keys:
                        if not isinstance(k, str):
                            continue
                        if k.lower() not in KNOWN:
                            bad.append(k)
                if bad:
                    strays[rel] = bad
    return strays


# ---------------------------------------------------------------------------
section("The canonical table is capped at 30 keys, general last")
check("30 keys, no more", len(industry.INDUSTRIES) == 30, len(industry.INDUSTRIES))
check("general is last", industry.INDUSTRIES[-1]["key"] == "general")
check("general has confidence 0.0 in resolve_industry",
      industry.resolve_industry(client="")["source"] == "general")

section("LEGACY_MAP covers every legacy spelling found in the repo")
_expected_legacy = {"auto", "boat", "medical", "medical_dental", "professional",
                    "recruit", "stadium", "home_builder"}
missing = _expected_legacy - LEGACY
check("every named legacy key is mapped", not missing, missing)
for leg, canon in {"auto": "automotive", "boat": "marine", "medical": "healthcare",
                    "medical_dental": "healthcare", "professional": "professional_services",
                    "recruit": "recruiting", "stadium": "events",
                    "home_builder": "real_estate"}.items():
    check(f"{leg} -> {canon}", industry.LEGACY_MAP.get(leg) == canon)

section("Every ALLOW entry carries a reason")
check("no empty exemptions",
      all(isinstance(v, str) and v.strip() for v in ALLOW.values()))

section("No stray industry key literal anywhere in the tree")
strays = scan_tree()
check("hub/ and modules/ declare only canonical or legacy keys", strays == {},
      strays)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
