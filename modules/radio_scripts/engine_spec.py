"""The word budgets, read by the prompt and by the check -- not restated in
either. A number typed twice is a number that drifts the day one of the two
copies is edited; both `prompts.py` and `engine.py` read this one.
"""
from __future__ import annotations

# length -> (low, high), counted by splitting on whitespace.
WORD_BUDGETS: dict[str, tuple[int, int]] = {
    "60": (140, 160),
    "30": (65, 75),
    "15": (35, 40),
}

LENGTHS = ("60", "30", "15")
