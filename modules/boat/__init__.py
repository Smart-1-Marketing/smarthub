"""Boat dealer landing page, running inside the Hub.

First of the eleven Smart 1 Suite landing pages to move in. Ported rather
than rewritten: the AI prompts, the report structure and the PDF are the
product, and they work.

What changes by being here:

  * **Leads go to the Hub's lead panel** instead of straight out to a private
    GoHighLevel webhook. Stored first, forwarded second, visible in one place
    with the page that produced them.
  * **One deploy, one set of keys.** OPENAI_API_KEY, CLOUDINARY_URL and the
    rest are already configured for every other tool.
  * **The page stays public.** It's a landing page — AuthGuard's
    public_prefixes carries the exception, so the Hub login doesn't sit in
    front of a prospect.
"""
