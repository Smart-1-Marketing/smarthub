"""Offline IO completion contract: real JS -> Flask -> real PDFs -> fake Suite.

Only storage/delivery providers are replaced. All files and records are isolated.
"""
import os
import subprocess
import tempfile
import threading
from unittest.mock import patch

tmp = tempfile.TemporaryDirectory(prefix="io_completion_", ignore_cleanup_errors=True)
os.environ.update(HUB_DATA_DIR=tmp.name, DATABASE_URL="sqlite:///" + tmp.name + "/mirror.db",
                  AUDIT_LOG_PATH=tmp.name + "/audit.jsonl", SECRET_KEY="fixture-only")

import cloudinary
import modules.io_builder.app as io
from hub import io_records, suite_opportunity
from werkzeug.serving import make_server

cloudinary.config(cloud_name="fixture", api_key="fixture", api_secret="fixture")
stats = {"uploads": 0, "pushes": 0, "opportunity_ids": []}


def upload(file, **kwargs):
    stats["uploads"] += 1
    assert file.read().startswith(b"%PDF"), "Must generate real PDF bytes"
    if stats["uploads"] == 2:
        raise RuntimeError("Fixture: interrupted internal PDF storage")
    return {"secure_url": f"https://example.com/fixture-{stats['uploads']}.pdf",
            "public_id": f"fixture-{stats['uploads']}"}


def push(**kwargs):
    stats["pushes"] += 1
    stats["opportunity_ids"].append(kwargs["opportunity_id"])
    return {"ok": True, "opportunity_id": "fixture-opportunity", "contact": {"id": "fixture-contact"}}


@io.app.get("/_test/results")
def results():
    row = io_records.get("QA-COMPLETION")
    return {**stats, "budget": row["monthly"], "orders": len(io_records._rows())}


with patch("cloudinary.uploader.upload", side_effect=upload), \
     patch.object(suite_opportunity, "configured", return_value=True), \
     patch.object(suite_opportunity, "push_proposal", side_effect=push), \
     patch("modules.image_picker.filing.file_asset"), \
     patch("hub.io_clients.register_from_io"):
    server = make_server("127.0.0.1", 0, io.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = dict(os.environ, IO_TEST_URL=f"http://127.0.0.1:{server.server_port}")
        subprocess.run(["node", "test_io_completion.js"], env=env, check=True, timeout=90)
    finally:
        server.shutdown()
        thread.join(timeout=5)

print("PASS: isolated IO completion workflow")
