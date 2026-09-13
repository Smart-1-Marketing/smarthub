"""Static contract tests for the Smart 1 Thinking personality layer.

Run directly with:
    python3 test_thinking_personality.py

These checks intentionally need no browser or third-party test framework. The
behavior lives in the globally injected hub-thinking.js, so this file protects
the architectural promises that make current and future tools inherit it.
"""
from pathlib import Path
import re

ROOT = Path(__file__).parent
SCRIPT = (ROOT / "hub" / "static" / "hub-thinking.js").read_text(encoding="utf-8")

passed = failed = 0


def ok(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")


print("\nSmart 1 Thinking personality\n----------------------------")

for context in ("generic", "ai", "api", "database", "analytics", "creative",
                "weather", "search", "report", "deployment"):
    ok(f"knows {context} context", f'"{context}"' in SCRIPT)

ok("uses the Smart 1 Thinking headline", '"Smart 1 Thinking…"' in SCRIPT)
ok("has four elapsed-time personality tiers",
   all(s in SCRIPT for s in ("elapsed < 3500", "elapsed < 8000", "elapsed < 15000")))
ok("rotates messages on a randomized 3–5 second window",
   "MESSAGE_MIN = 3200" in SCRIPT and "MESSAGE_JITTER = 1600" in SCRIPT)
ok("does not intentionally repeat the previous message",
   "candidate !== previous" in SCRIPT)
ok("starts informative before becoming playful",
   "Smart 1 is working on it." in SCRIPT and "Artificial intelligence. Natural impatience." in SCRIPT)

print("\nAutomatic inheritance\n---------------------")
ok("wraps fetch once", "window.fetch = wrappedFetch" in SCRIPT
   and "__s1ThinkingWrapped" in SCRIPT)
ok("wraps XMLHttpRequest once", "XMLHttpRequest.prototype.__s1ThinkingWrapped" in SCRIPT
   and "proto.send = function" in SCRIPT)
ok("waits before showing the global card", "NETWORK_DELAY = 700" in SCRIPT)
ok("allows a request to opt out", "o.s1Thinking === false" in SCRIPT)
ok("allows a request to name its context", "o.s1Context" in SCRIPT)
ok("future code can explicitly track an arbitrary promise", "track: trackPromise" in SCRIPT)
ok("future code can explicitly start a wait", "start: startGlobal" in SCRIPT)

print("\nContext inference\n-----------------")
for needle, context in (("weather|forecast", "weather"),
                        ("image|creative|video", "creative"),
                        ("analytics|ga4|looker", "analytics"),
                        ("report|reporting|audit", "report"),
                        ("deploy|publish|release", "deployment"),
                        ("search|lookup|find", "search"),
                        ("database|\\bdb\\b|knack", "database"),
                        ("openai|\\bai\\b|gpt", "ai")):
    ok(f"infers {context}", needle in SCRIPT and f'return "{context}"' in SCRIPT)
ok("falls back to API for generic API routes", 'return "api"' in SCRIPT)
ok("falls back safely to generic", 'return "generic"' in SCRIPT)

print("\nAccessibility and non-interference\n----------------------------------")
ok("global status is announced politely",
   'box.setAttribute("role", "status")' in SCRIPT
   and 'box.setAttribute("aria-live", "polite")' in SCRIPT)
ok("status updates are atomic", 'box.setAttribute("aria-atomic", "true")' in SCRIPT)
ok("respects reduced motion", "prefers-reduced-motion:reduce" in SCRIPT)
ok("fast static assets are ignored",
   re.search(r"js\|css\|map\|png", SCRIPT) is not None)
ok("network failure still clears the wait",
   SCRIPT.count("h.done(); throw") >= 2)
ok("the loader never writes a success claim",
   "Smart 1 Thinking…" in SCRIPT and "Success!" not in SCRIPT)

print(f"\n{passed} passed, {failed} failed")
if failed:
    raise SystemExit(1)
