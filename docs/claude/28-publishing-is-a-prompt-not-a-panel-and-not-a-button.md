## Publishing is a prompt, not a panel and not a button

Every blog post, JSON-LD block, FAQ accordion and alt tag we produce has to be
typed into a CMS by somebody. Smart 1 Sites (the Simvoly whitelabel) exposes
projects, plans and websites through its API — not page content — and a
client's WordPress is someone else's server with someone else's plugins on it.
So `hub/cms_publish.py` does not publish, and it no longer asks a rep to retype
thirty fields either. **It writes a prompt for Claude in Chrome.**

**Claude → Smart 1 Sites** and **Claude → WordPress** sit on the blog table,
the schema table, the FAQ table and the alt-text table. Tick what is going up,
the CMS opens in a new window, and the panel hands back one block of text: the
rules, how that CMS behaves, and the finished content. The rep signs in, pastes
it into the Claude side panel on that tab, and approves each action.

What that changes about what a good output is:

- **The prompt carries the content, not a description of it.** The browser
  agent cannot see this Hub, so "add the blog post" is useless — the whole body
  HTML, the slug, the categories and the author have to be in the pasted text.
- **It carries the rules that stop it improvising.** Approved copy is
  reproduced, not paraphrased. A missing field is reported, not guessed at. A
  category that does not exist is created with the exact name rather than filed
  under the nearest match. Nothing is published; everything stops as a draft
  for a human. An agent left to its own judgment on any of those produces
  something plausible that nobody approved.
- **It never carries a credential.** This Hub stores the site login and
  password under Client Setup, and interpolating them into a block of text
  destined for a chat window is the easiest possible mistake to make here. The
  human signs in first; the prompt says so and tells the agent not to ask.
  `test_alt_text.py` asserts no stored credential reaches any of the eight
  CMS × kind prompts.
- **The field-by-field list stays underneath it.** Claude in Chrome is not on
  every machine, and a rep fixing one field should not have to dig it out of a
  wall of prompt text.

Three things that follow, unchanged from when this was a paste panel:

- **Nothing is invented.** With no site URL on the client there is no WordPress
  admin to open, and the panel says which setting is missing. A guessed
  `https://<clientname>.com/wp-admin` opens a stranger's login page.
- **Smart 1 Sites opens through the Hub.** Sites Admin already holds every
  Simvoly project and already has the builder SSO, so the project page is the
  address that gets a rep into the right builder without a second password. The
  match is by **domain**, never by name, for the reason `hub/sites_match.py`
  gives at length — and two projects on one domain returns the search rather
  than picking one, because the wrong pick edits another client's website.
- **A field with no home says so.** Simvoly's blog has categories and no tag
  field; the prompt tells the agent to say so rather than put the tags
  somewhere else.
- **Where an FAQ block goes on the page is asked, not left to the agent.**
  "Somewhere sensible" is how an accordion lands above the hero on one page and
  in a sidebar on the next. The panel offers the positions (`PLACEMENTS`,
  default: the last section before the footer) and the answer is written into
  the prompt as an instruction. Changing it rebuilds the prompt rather than
  patching the text — a panel showing one position while the clipboard holds
  another is the worst version of this.

The window is opened in the click handler, before the fetch — a `window.open()`
inside a promise callback is a popup the browser blocks, and a blocked popup
looks exactly like a button that does nothing.
