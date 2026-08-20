"""Smart 1 ski resort landing page, running inside the Hub.

Leads go to the Hub lead panel, the OpenAI SDK is replaced by a plain HTTP
call, and the page stays public. Also fixes the 502 the QA audit found: a
finished report was returned inside an error response the front end never
rendered, so the operator waited 30 seconds and saw a raw webhook error
instead of their plan.
"""
