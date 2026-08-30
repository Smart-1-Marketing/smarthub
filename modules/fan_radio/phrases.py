"""Fan Radio — the football language guard.

The whole point of this tool is to sound like football without borrowing
anybody's trademark. A local advertiser who says a club nickname, a league
mark or a fan slogan in a paid spot is not "supporting the team" — they are
running an unlicensed endorsement, and the letter comes from the league, not
the station.

So two lists, and one scanner:

* ``BLOCKED`` — marks that must never survive into a delivered script:
  league names, club nicknames, bowl and playoff marks, broadcast package
  names, and the fan slogans clubs actually register (Who Dey, Terrible
  Towel, 12th Man, Bills Mafia…). A hit here fails QC outright.
* ``ADVISORY`` — phrases that are widely used as trademark *workarounds* but
  still draw attention ("the big game", "the championship"). Allowed, but
  surfaced so a human decides.
* ``SAFE_PHRASES`` — generic football language nobody owns, grouped by
  daypart. The writer is fed these; the QC pass checks at least one landed,
  because a "football spot" with no football in it is the other failure mode.

The client's own team is *never* named for them. When a project names a team
for context (so the writer knows the market and the schedule), that name and
its mascot are added to the block list for that project — the context steers
the writing, it does not enter the copy.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------- blocked
# Leagues, packages, post-season and governing bodies.
_LEAGUE_MARKS = [
    "nfl", "national football league", "super bowl", "superbowl",
    "pro bowl", "monday night football", "sunday night football",
    "thursday night football", "sunday ticket", "redzone channel",
    "ncaa", "march madness", "final four", "college football playoff",
    "rose bowl", "orange bowl", "sugar bowl", "cotton bowl", "fiesta bowl",
    "peach bowl", "citrus bowl", "heisman", "bcs", "gatorade",
    "friday night lights", "hard knocks", "combine invite",
    "afc championship", "nfc championship", "lombardi trophy",
    "sec network", "big ten", "big 12", "pac-12", "acc network",
]

# The 32 club nicknames. Cities are fine on their own — "Cincinnati" is a
# place — so only the nicknames and the unmistakable city+nickname pairs.
_CLUB_NICKNAMES = [
    "cardinals", "falcons", "ravens", "bills", "panthers", "bears",
    "bengals", "browns", "cowboys", "broncos", "lions", "packers",
    "texans", "colts", "jaguars", "chiefs", "raiders", "chargers",
    "rams", "dolphins", "vikings", "patriots", "saints", "giants",
    "jets", "eagles", "steelers", "49ers", "niners", "seahawks",
    "buccaneers", "bucs", "titans", "commanders",
]

# College programmes a local spot is most likely to reach for.
_COLLEGE_NICKNAMES = [
    "buckeyes", "wolverines", "crimson tide", "bulldogs", "gators",
    "longhorns", "sooners", "fighting irish", "nittany lions", "volunteers",
    "tigers", "aggies", "hurricanes", "seminoles", "razorbacks", "huskers",
    "spartans", "hawkeyes", "badgers", "trojans", "ducks", "wildcats",
    "cyclones", "jayhawks", "mountaineers", "hokies", "tar heels", "rebels",
]

# Fan slogans and traditions that are registered marks or close enough.
_FAN_MARKS = [
    "who dey", "terrible towel", "cheesehead", "dawg pound", "12th man",
    "twelfth man", "skol", "bills mafia", "raider nation", "dolphins fins up",
    "fins up", "go pack go", "roll tide", "war eagle", "hook em", "hook 'em",
    "boomer sooner", "the horseshoe", "the swamp", "death valley",
    "touchdown jesus", "lambeau leap", "black hole", "cheese head",
    "sea of red", "big blue nation", "bleed green", "legion of boom",
]

BLOCKED: list[str] = sorted(set(_LEAGUE_MARKS + _CLUB_NICKNAMES
                                + _COLLEGE_NICKNAMES + _FAN_MARKS))

# --------------------------------------------------------------- advisory
# Not owned outright, but they are the phrases everybody reaches for when
# they mean the mark they can't say. Flagged, never auto-removed.
ADVISORY: dict[str, str] = {
    "the big game": "Widely used, but it reads as a stand-in for a league "
                    "mark. 'Sunday' or 'kickoff' is cleaner.",
    "big game": "Fine in context ('a big game this weekend'); avoid it as a "
                "proper noun standing in for a championship.",
    "the championship": "Safe generically. Don't pair it with a city or a "
                        "date that identifies one specific event.",
    "the playoffs": "Generic and fine. Don't attach a league name to it.",
    "official": "'Official sponsor/partner of' implies a license. Only use "
                "it if the client actually holds one.",
    "sponsor of": "Same — an unlicensed sponsorship claim is the one line "
                  "that reliably draws a letter.",
    "home team": "Fine. Just never follow it with the nickname.",
    "our boys": "Fine, but check it doesn't read as a club reference in "
                "your market.",
}

# --------------------------------------------------------- safe language
# Generic football that nobody owns. The writer picks from these; the QC
# pass requires at least one to have landed.
SAFE_PHRASES: dict[str, list[str]] = {
    "pregame": [
        "pregame prep", "before kickoff", "stock up before Sunday",
        "get the tailgate ready", "the countdown to kickoff",
        "beat the pregame rush", "set your lineup", "game plan",
        "first-string", "starting lineup", "the week of prep",
        "warm-ups", "coin toss is at noon", "get ahead of gameday",
        "tailgate season", "before the whistle blows",
    ],
    "gameday": [
        "gameday", "kickoff", "first down", "red zone", "fourth and one",
        "the two-minute drill", "halftime", "in the huddle", "snap count",
        "moving the chains", "goal line", "under the lights",
        "the home crowd", "hometown crowd", "on the road", "overtime",
        "no huddle", "third and long", "the hurry-up", "sideline to sideline",
        "hail mary", "extra point", "blitz", "pigskin", "gridiron",
    ],
    "postgame": [
        "postgame", "after the final whistle", "Monday morning quarterback",
        "armchair quarterback", "the film room", "breaking down the tape",
        "the recap", "back to the drawing board", "next week's game plan",
        "win or lose", "however it ended", "the highlight reel",
        "the box score", "the bye week", "on to the next one",
        "regroup and reload", "post-game analysis", "the long ride home",
    ],
}

# Post-game copy that quietly assumes a result. A spot booked to air after
# the final whistle is written and voiced days earlier — it cannot know.
OUTCOME_ASSUMING = [
    "big win", "after that win", "great win", "the victory", "we won",
    "celebrate the win", "another win", "undefeated", "perfect season",
    "tough loss", "after that loss", "we lost", "heartbreaker",
    "blowout", "we're rolling", "on a winning streak",
]


def _pattern(term: str) -> re.Pattern:
    """Word-boundary match that tolerates punctuation inside a phrase."""
    parts = [re.escape(p) for p in term.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\s\-']+".join(parts)
                      + r"(?![A-Za-z0-9])", re.I)


_BLOCK_RE = [(t, _pattern(t)) for t in BLOCKED]
_ADVISE_RE = [(t, _pattern(t), why) for t, why in ADVISORY.items()]
_OUTCOME_RE = [(t, _pattern(t)) for t in OUTCOME_ASSUMING]


def extra_blocked(*names: str) -> list[str]:
    """Turn a project's own team/mascot context into block terms.

    'Cincinnati Bengals' contributes 'cincinnati bengals' and 'bengals';
    the city alone is left alone because a city name is not a mark and the
    spot legitimately needs to say where it is.
    """
    out: list[str] = []
    for name in names:
        clean = re.sub(r"[^A-Za-z0-9 ']", " ", str(name or "")).strip()
        if not clean:
            continue
        words = clean.split()
        if len(words) > 1:
            out.append(clean.lower())
            out.append(words[-1].lower())
            if len(words) > 2:
                # 'Kansas State Wildcats' also blocks 'Kansas State' — a
                # school name is a mark. A two-word name is city + nickname,
                # and the city on its own is just a place.
                out.append(" ".join(words[:-1]).lower())
        elif len(words) == 1 and len(words[0]) > 2:
            out.append(words[0].lower())
    return sorted({w for w in out if len(w) > 2})


def scan(text: str, also_block: list[str] | None = None,
         daypart: str = "", outcome: str = "neutral") -> dict:
    """Check one script. Returns findings, never rewrites.

    ``blocked`` is a hard fail: the script cannot be delivered as written.
    ``advisory`` and ``outcome`` are surfaced for a human call.
    ``football`` reports whether any safe football language actually landed.
    """
    text = str(text or "")
    blocked, seen = [], set()

    for term, rx in _BLOCK_RE:
        m = rx.search(text)
        if m and term not in seen:
            seen.add(term)
            blocked.append({"term": m.group(0), "match": term,
                            "why": "Registered mark — a league, club, bowl "
                                   "or fan trademark."})

    for term in (also_block or []):
        rx = _pattern(term)
        m = rx.search(text)
        if m and term not in seen:
            seen.add(term)
            blocked.append({"term": m.group(0), "match": term,
                            "why": "This project's own team name. Context "
                                   "for the writer, not copy for the spot."})

    advisory = []
    for term, rx, why in _ADVISE_RE:
        m = rx.search(text)
        if m:
            advisory.append({"term": m.group(0), "why": why})

    outcome_hits = []
    if daypart == "postgame" and outcome == "neutral":
        for term, rx in _OUTCOME_RE:
            m = rx.search(text)
            if m:
                outcome_hits.append({
                    "term": m.group(0),
                    "why": "This airs after the final whistle but is voiced "
                           "days earlier — it can't know the result."})

    bank = SAFE_PHRASES.get(daypart, [])
    landed = [p for p in bank if _pattern(p).search(text)]
    if not landed:                       # any daypart's language still counts
        for phrases in SAFE_PHRASES.values():
            landed += [p for p in phrases if _pattern(p).search(text)]

    return {
        "blocked": blocked,
        "advisory": advisory,
        "outcome": outcome_hits,
        "football": sorted(set(landed))[:6],
        "clean": not blocked,
    }


def suggest(daypart: str, n: int = 8) -> list[str]:
    """Safe phrases to offer a writer (human or model) for a daypart."""
    return SAFE_PHRASES.get(daypart, SAFE_PHRASES["gameday"])[:n]
