"""Real-tool adapters for the Proposal Execution Center.

`hub/proposal_execution.py` already has three adapters -- `brief` (an AI
working draft, text only), `radio_scripts` (real: it calls
`modules.radio_scripts.api.create_set`) and `launch_packet` (a human
handoff checklist). Every other task in the graph -- the Meta carousel, the
banners, the YouTube spot, paid-search ads, the social calendar, the SEO
workplan -- comes back as `brief`: JSON text describing what somebody
should go and build, in a tool this Hub already has.

Each module here is one adapter that actually reaches the real tool and
**creates work inside it**: a saved link, a draft plan, a project row. None
of them publishes, schedules client-facing content, changes a live
campaign, or starts spend -- that boundary is `execution_mode="approval"`
or `"handoff"`, enforced the same way every other task on this graph is.

A runner is a plain function `(run, task) -> dict`, the exact shape
`_brief_runner`/`_radio_runner` already use in `hub/proposal_execution.py`,
registered with `hub.proposal_execution.register_adapter` at import. This
package's `register_all()` is called once, from
`hub/proposal_execution_routes.py`, so importing this package is what
switches a task from a text brief to the real tool -- and nothing else has
to change at the call site the day a further adapter lands here.

Every adapter reads `run.inputs()` for what it needs and raises `ValueError`
naming the missing one in plain English when it cannot proceed -- the
`run_one()` in `hub/proposal_execution.py` catches that and requeues the
task rather than marking it failed. A run's own retry ceiling
(`MAX_ATTEMPTS`) still applies; nothing here tries to do the module's own
retrying a second time.
"""
from __future__ import annotations


def register_all() -> None:
    from . import utm  # noqa: F401 -- imported for its register_adapter() call
    from . import search_ads  # noqa: F401 -- imported for its register_adapter() call
    from . import social  # noqa: F401 -- imported for its register_adapter() call
    from . import seo  # noqa: F401 -- imported for its register_adapter() call
    from . import commercial  # noqa: F401 -- imported for its register_adapter() call
