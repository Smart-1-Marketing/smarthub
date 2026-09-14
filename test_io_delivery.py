"""Request replay and concurrency tests; no Suite requests or real data."""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from flask import Flask, jsonify
from modules.io_builder.submission_attempts import protected_delivery

tmp = tempfile.TemporaryDirectory(prefix="io_delivery_", ignore_cleanup_errors=True)
os.environ["HUB_DATA_DIR"] = tmp.name
os.environ["DATABASE_URL"] = "sqlite:///" + tmp.name + "/mirror.db"
os.environ["AUDIT_LOG_PATH"] = tmp.name + "/audit.jsonl"
from hub import jsonstore  # warm imports before the timed concurrency assertions
app = Flask(__name__)
calls = []
entered, release = Event(), Event()


@app.post("/send")
@protected_delivery
def send():
    calls.append(1)
    entered.set()
    release.wait(30)
    return jsonify(ok=True, delivered_to_suite=True)


@app.post("/uncertain")
@protected_delivery
def uncertain():
    calls.append(1)
    return jsonify(ok=False, error="timeout"), 502


payload = {"orderNumber": "test-1", "submissionRequestId": "attempt-1"}


def post(value):
    with app.test_client() as client:
        return client.post("/send", json=value)


with ThreadPoolExecutor(2) as pool:
    first = pool.submit(post, payload)
    assert entered.wait(30)
    assert post(payload).status_code == 409
    assert post({**payload, "submissionRequestId": "another"}).status_code == 409
    release.set()
    assert first.result().status_code == 200
assert post(payload).json["delivered_to_suite"] is True
assert len(calls) == 1
assert post({**payload, "client": "changed"}).status_code == 409
assert post({**payload, "submissionRequestId": "revision-2"}).status_code == 200
assert len(calls) == 2
assert post(payload).status_code == 200
assert len(calls) == 2, "An old receipt must not overwrite a newer revision"


with app.test_client() as client:
    p = {"orderNumber": "test-2", "submissionRequestId": "attempt-1"}
    assert client.post("/uncertain", json=p).status_code == 502
    assert client.post("/uncertain", json=p).status_code == 409
    assert client.post("/uncertain", json={**p, "submissionRequestId": "new"}).status_code == 409
assert len(calls) == 3
print("PASS: concurrent delivery excluded, successful receipt replayed, revisions permitted, uncertain delivery never blindly resent")
