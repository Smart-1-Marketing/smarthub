"""Smart 1 hvac landing page, running inside the Hub.

Ported rather than rewritten — the AI prompts, report structure and PDF are
the product and they work. What changes: leads go to the Hub's lead panel
instead of a private webhook, the OpenAI SDK is replaced by a plain HTTP call
so no new dependency is needed, and the page stays public via AuthGuard's
public_prefixes so a prospect never meets the Hub login.
"""
