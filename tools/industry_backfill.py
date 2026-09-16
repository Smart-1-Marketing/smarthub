#!/usr/bin/env python3
"""One-off backfill: resolve and write hub/industry.py's canonical key for
every client this Hub can enumerate without a live Knack call.

Run once, by hand, from the repo root:

    python3 tools/industry_backfill.py

**No Knack in this deployment pass.** The client list this walks is
`hub.industry._client_universe()` -- every SEO store's own `client` field
plus every Image Picker gallery name -- rather than
`clients_registry.all_clients()`, which wraps a live Knack read
(`knack_data.websites()` / `.products()`). That is narrower than the full
book; the report below says how many clients it actually looked at.

Idempotent, and it refuses to touch a record already marked `manual` --
`hub.industry.write_industry()` already refuses that write, so running this
twice changes nothing the second time.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    from hub import industry

    names = industry._client_universe()  # noqa: SLF001
    print(f"{len(names)} clients found (SEO stores + Image Picker galleries; "
          f"no Knack read in this pass)\n")

    proposed: dict[str, int] = {}
    on_general: list[str] = []
    manual_skipped: list[str] = []
    disagree: list[dict] = []
    picker_changes: list[dict] = []
    written = 0

    for name in sorted(names, key=str.lower):
        stored = industry._stored_industry(name)  # noqa: SLF001
        if stored.get("source") == "manual":
            manual_skipped.append(name)
            continue
        cur_key = stored.get("key") or ""
        result = industry.resolve_industry(client=name)
        key = result.get("key") or "general"
        proposed[key] = proposed.get(key, 0) + 1
        if key == "general":
            on_general.append(name)
        if cur_key and cur_key != key:
            disagree.append({"client": name, "was": cur_key, "now": key})
        try:
            from modules.image_picker import taxonomy
            picker_key = taxonomy.picker_key_for(key)
            if picker_key:
                picker_changes.append({"client": name, "picker_key": picker_key})
        except Exception:                                 # noqa: BLE001
            pass
        if industry.write_industry(name, result):
            written += 1

    print("Proposed key -> client count")
    print("-" * 40)
    for key, count in sorted(proposed.items(), key=lambda kv: -kv[1]):
        label = (industry.industry(key) or {}).get("label", key)
        print(f"  {label:<28} {count}")

    print(f"\nClients resolving to general: {len(on_general)}")
    for n in on_general[:50]:
        print(f"  - {n}")

    print(f"\nManual picks skipped (never overwritten): {len(manual_skipped)}")

    print(f"\nStored key changing: {len(disagree)}")
    for row in disagree[:50]:
        print(f"  - {row['client']}: {row['was']} -> {row['now']}")

    print(f"\nImage Picker keys that would/did change: {len(picker_changes)}")
    for row in picker_changes[:50]:
        print(f"  - {row['client']}: {row['picker_key']}")

    print(f"\n{written} record(s) written.")


if __name__ == "__main__":
    main()
