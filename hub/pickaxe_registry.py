"""Which Pickaxes the Hub calls live, and their form-field maps.

Only Pickaxes with a knowledge base the Hub cannot hold earn a live call —
everything else was absorbed into the Hub's own prompts (see
hub/prompts_harvested.py). Two qualify:

  * SEM Quote Help — Google Ads benchmarks and a 1,586-chunk responsive
    search ad report behind its keyword and CPC suggestions.
  * Audience Finder — the agency's audience catalog: "Audiences (5)"
    (11,915 chunks), "Topics Taxonomy" (21,503 chunks), "TARGET LISTS".

The field ids are the "userinput:<uuid>" keys from each Pickaxe's
promptframe. They are stable until somebody edits that Pickaxe's form in
Pickaxe Studio — when a call starts failing or a field stops landing, this
file is the one place to re-check (pull the promptframe and compare).

Usage:

    from hub import pickaxe
    from hub.pickaxe_registry import SEM_QUOTE_HELP, fill

    reply = pickaxe.ask(
        SEM_QUOTE_HELP["pickaxe_id"], module="ads", purpose="sem_quote",
        workspace_id=SEM_QUOTE_HELP["workspace_id"],
        inputs=fill(SEM_QUOTE_HELP, company=client.name, website=site,
                    does="HVAC sales and service", focus="AC replacement",
                    budget="$2,500/mo", geo="Carmel, IN + 10 miles"))
"""
from __future__ import annotations

# Workspace ids on the smartadops@gmail.com account. A workspace-scoped API
# key ignores these; a Personal API Key requires them.
WS_SMART_1_TEST = "50eb9802-678d-4be1-afe1-b615fba85dea"

SEM_QUOTE_HELP = {
    "pickaxe_id": "SEM_Quote_Help_41N14",
    "workspace_id": WS_SMART_1_TEST,
    # keyword -> Pickaxe field id. Keywords are what call sites use; the
    # UUIDs never appear outside this file.
    "fields": {
        "company": "userinput:01a99743-fcb7-4ada-b539-2213a85131a4",
        "website": "userinput:b2afdb6c-aa1f-40a1-bdda-638604205473",   # with https://
        "does":    "userinput:70a5bf9b-be8c-419f-bc27-8445b8c89e75",   # what the company does
        "focus":   "userinput:36f92fdd-b16b-4027-b2b7-866dd5f97f64",   # campaign focus
        "budget":  "userinput:73a85fc3-1a5e-48d9-be61-ae715d816d87",
        "geo":     "userinput:736e7b77-8a01-49b2-b72d-70ec04b88640",
    },
}

AUDIENCE_FINDER = {
    "pickaxe_id": "Audience_Finder_4PJVA",
    "workspace_id": WS_SMART_1_TEST,
    "fields": {
        "client": "userinput:8bd92363-ff1d-4c6e-a717-5b82b1f1f409",    # optional
        "target": "userinput:1b30197b-7be7-40b8-91e2-f381df940efe",    # required
    },
}


def fill(entry: dict, **values) -> dict:
    """Keyword arguments -> the inputs dict pickaxe.ask() sends.

    Unknown keywords raise rather than being dropped: a typo'd field name
    that silently vanishes produces a completion that is quietly missing an
    input, which is far harder to notice than a KeyError.
    """
    fields = entry["fields"]
    unknown = set(values) - set(fields)
    if unknown:
        raise KeyError(f"Unknown field(s) for {entry['pickaxe_id']}: {sorted(unknown)}")
    return {fields[k]: str(v) for k, v in values.items() if str(v or "").strip()}
