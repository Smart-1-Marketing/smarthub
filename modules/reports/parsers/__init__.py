"""CSV parsers for platform exports that arrive by API and by email alike.

Each parser turns one platform's export into the ``AdPerfDaily`` dicts
``store.upsert_rows()`` takes, so a file downloaded by a scheduled pull and
the same file forwarded from somebody's inbox land as the same rows.
"""
