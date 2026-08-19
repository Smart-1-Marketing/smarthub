"""Insertion Order builder — ported from insertionordersmart.onrender.com.

Was an iframe inside Sales Builder, which meant it couldn't reach anything the
Hub knows: no client registry, no brand colours, no audit log, and no way to
prefill the client you were already looking at. Cross-origin also meant the Hub
couldn't tell when a submission succeeded.

The app itself is unchanged in substance — same two routes, same GoHighLevel
webhook, same Cloudinary-backed order counter. What's added is the Hub's
context: shared login, the client registry, and activity logging.
"""
