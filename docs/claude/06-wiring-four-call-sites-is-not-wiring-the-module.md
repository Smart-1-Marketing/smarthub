## Wiring four call sites is not wiring the module

`/api/integrity`'s silent-module check reads a **call** now rather than an
import, and the seven modules that had bound `hub.audit` and never used it
were fixed. That sweep wired a handful of call sites per module and stopped,
and nothing could see the remainder — because **the check one level up is
satisfied by one call site.** It asks whether a module logs *at all*, which is
the same shape as the check that read the string `for_module(` and counted the
binding. A module can be loudly attributable about a quarter of its work and
pass.

**Two modules were found doing exactly that, and in both the creating half of
a create/destroy pair was the half left out.**

`modules/sites_admin` recorded `delete_website` and not `add_site`, which
makes a client's website and can activate a paid plan — so the record showed
sites being deleted by somebody and appearing from nowhere. Nor
`personalization`, which writes brand colors onto their live pages, nor
`pricing`, which sets `client_price` **and** `internal_client_name` — the join
`hub/domain_links.py` writes and every domain-keyed report reads, so changing
it moves a website onto a different client's record. Nor `project_sso`, which
is not a change to the site but is the door the changes are made through.

`modules/google_finder` recorded `disconnect` and not `oauth_callback`, which
is the moment the Hub *gains* a refresh token for somebody else's Google
account — the grant every write in that module is made under. And it recorded
`gtm_deploy_event` while `gtm_deploy_pixel` went unrecorded: arbitrary code,
in a container we do not own, which is the very action this file already names
as one of the least attributable in the Hub. `api_gsc_bulk_add` writes
properties into their Search Console and was silent too.

**One walk, read by both.** `audit.write_route_attribution()` is the question
asked one level finer, and it lives beside the log rather than being copied
per module: two readings of one question drift the day either is edited, the
failure `_client_log_modules()` already had to undo. It reads the **AST**,
because both modules name `_audit` in comments explaining why it had gone
uncalled and a check matching text reports the fix as the defect; it resolves
a module's own `log()` wrapper, the shape `check_work_kinds()` had to learn;
and it is handed a silent route and required to name it.

**And a wrapper is resolved from its definition, not guessed from its name.**
The walk hard-coded `_audit` and `log`, which was two modules' spelling and not
a rule. `modules/seo_images` calls its wrapper `_log`, so a module recording
five of its seven writes read as recording **none** — a check inventing seven
findings, which is switched off faster than one that misses them. Worse, the
same blindness had already produced a **wrong exemption**: Google Finder's
`api_ga4_ask` logs through `_audit_mod.log(...)`, the walk could not see a
caller of that name, and it was duly declared as a route that records nothing.
An exemption covering a call site that never needed one. A wrapper is any
function in the file that itself reaches the shared logger, however spelled,
and the both-directions rule is what caught the stale entry.

**A route that writes without a write method is named rather than missed.**
Google redirects the browser to `oauth_callback`, so it is a `GET` by protocol
and a method-based walk cannot classify it — while what it does is store a
credential. It is asserted by name, because the one thing worse than a walk
that misses a route is a walk that misses it silently.

**`HOUSEKEEPING_ROUTES` is the other side**, per module, each entry with its
reason: the reads, the imports of our own tables, and GA4's `runReport`, which
is a POST that reads. Held in both directions, so an entry naming a route that
is gone — or one that has since started logging — fails.

**And every one of the new calls logs after the provider answered**, inside
the try, the shape `project_action`'s own comment already describes and
`approve_render` uses in the Commercial Builder: a change Simvoly or Google
refused is not written down as one that was made.

**What is deliberately not here is a repo-wide gate.** The same walk over
every module that logs finds about **229 silent write routes across 34
files**, and the great majority are genuinely housekeeping — autosaves,
drafts, previews, and POSTs that read. A check landing with 229 findings
nobody can act on is the one people learn to skip, which is the note
`help_audit.demo_targets()` already makes about the walkthrough backlog. The
modules that have been triaged declare their remainder and are held to it; the
rest is a list somebody works down, module by module.

**And the walk stopped one level short of its own stated rule.** Its comment
says a wrapper is *"a function in this file that itself reaches the shared
logger, however it is spelled"* — and the code counted a function calling
`audit.log(...)` by **attribute** and stopped there. A module that binds
`_cb_log = audit.for_module(...)` and then wraps *that* in a helper had every
route calling the helper reported silent. Four read that way, all four
recording their work perfectly well: the Commercial Builder's `submit_render`,
`send_for_review` and `client_decide`, and `image_audit.api_image_attach_many`,
which is the bulk attach that files orphaned images onto a client. That is a
check **inventing** findings rather than missing them — the failure the
paragraph above already names once about `_audit` and `log` being hard-coded —
and it is worse here than a gap, because the whole point of the walk is to let
a module be triaged: a triage built on that answer declares a **logging** route
as housekeeping, and the declaration is then held in both directions against a
lie. The set is closed transitively now, and it terminates because a pass that
adds nothing stops it.
