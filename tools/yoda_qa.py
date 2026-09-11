"""Yoda's QA queue, from outside the Hub.

hub/qa_tasks.py has no bearer-token API and was never meant to need one --
every route reads the signed session cookie a browser sign-in leaves behind.
This is that sign-in, scripted: log in as the "Yoda" account with a plain
email/password POST to /login, keep the cookie in a requests.Session, and
read or write the same JSON the QA Tasks page itself calls.

    YODA_HUB_EMAIL=... YODA_HUB_PASSWORD=... python3 tools/yoda_qa.py list
    ... python3 tools/yoda_qa.py show 41
    ... python3 tools/yoda_qa.py reply 41 --body "..." [--file path]
    ... python3 tools/yoda_qa.py pickup --for todd@smart1marketing.com

Credentials are read from the environment and never printed, logged, or
written to disk -- this talks to the live Hub, not to this checkout.

`pickup` is the automatic half: it reads the whole-team board, finds every
open task still assigned to `--for` (Todd, by default), and claims each one
through `/api/qa-tasks/<id>/claim`. That route refuses the claim unless the
live Hub's `QA_TASK_DELEGATES` env var names this account as standing in for
that person -- reassignment is not "anyone can", the way raising a task is,
so the account this runs as has to be on that list before `pickup` can do
anything at all.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

import requests

BASE_URL = os.environ.get("HUB_BASE_URL", "https://smart1.agency").rstrip("/")


class YodaQaError(Exception):
    pass


def _session() -> requests.Session:
    email = os.environ.get("YODA_HUB_EMAIL", "")
    password = os.environ.get("YODA_HUB_PASSWORD", "")
    if not email or not password:
        raise YodaQaError(
            "Set YODA_HUB_EMAIL and YODA_HUB_PASSWORD before running this.")
    sess = requests.Session()
    resp = sess.post(f"{BASE_URL}/login",
                      data={"email": email, "password": password},
                      allow_redirects=True, timeout=30)
    resp.raise_for_status()
    check = sess.get(f"{BASE_URL}/api/qa-tasks", timeout=30)
    if check.status_code != 200 or check.json().get("email", "") == "":
        raise YodaQaError(
            "Signed in did not stick -- wrong password, or this account "
            "still has must_change_password set (log in through a browser "
            "once to clear it).")
    return sess


def cmd_list(args) -> int:
    sess = _session()
    data = sess.get(f"{BASE_URL}/api/qa-tasks", timeout=30).json()
    if not data.get("measured", True):
        print(f"Could not look: {data.get('error')}", file=sys.stderr)
        return 1
    todo = data.get("to_do", [])
    if not todo:
        print("Nothing assigned to Yoda right now.")
        return 0
    for t in todo:
        flag = " OVERDUE" if t["overdue"] else ""
        print(f"#{t['id']}  [{t['status_label']}]{flag}  {t['target_label']}"
              f"  -- from {t['created_by_name']}")
        print(f"      {t['instructions'][:200]}")
    return 0


def cmd_show(args) -> int:
    sess = _session()
    resp = sess.get(f"{BASE_URL}/api/qa-tasks/{args.task_id}", timeout=30)
    body = resp.json()
    if not body.get("ok"):
        print(body.get("error", "not found"), file=sys.stderr)
        return 1
    print(json.dumps(body["task"], indent=2))
    return 0


def cmd_reply(args) -> int:
    sess = _session()
    opener = open(args.file, "rb") if args.file else contextlib.nullcontext()
    with opener as fh:
        resp = sess.post(
            f"{BASE_URL}/api/qa-tasks/{args.task_id}/respond",
            data={"body": args.body}, files={"file": fh} if fh else None,
            timeout=30)
    body = resp.json()
    if not body.get("ok"):
        print(body.get("error", "could not post"), file=sys.stderr)
        return 1
    print(f"Posted on #{args.task_id}.")
    return 0


def cmd_pickup(args) -> int:
    sess = _session()
    data = sess.get(f"{BASE_URL}/api/qa-tasks/board", timeout=30).json()
    if not data.get("measured", True):
        print(f"Could not look: {data.get('error')}", file=sys.stderr)
        return 1
    principal = args.for_.strip().lower()
    theirs = [t for t in data.get("tasks", [])
              if t["assigned_to_email"].lower() == principal
              and t["status"] in ("open", "needs_more")]
    if not theirs:
        print(f"Nothing open assigned to {principal}.")
        return 0
    claimed = []
    for t in theirs:
        resp = sess.post(f"{BASE_URL}/api/qa-tasks/{t['id']}/claim", timeout=30)
        body = resp.json()
        if not body.get("ok"):
            print(f"#{t['id']}  {t['target_label']}  -- could not claim: "
                  f"{body.get('error')}", file=sys.stderr)
            continue
        claimed.append(t)
        print(f"#{t['id']}  {t['target_label']}  -- from {t['created_by_name']}")
        print(f"      {t['instructions'][:300]}")
    if not claimed:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Yoda's open tasks").set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="one task, full detail")
    p_show.add_argument("task_id", type=int)
    p_show.set_defaults(func=cmd_show)

    p_reply = sub.add_parser("reply", help="post an answer on a task")
    p_reply.add_argument("task_id", type=int)
    p_reply.add_argument("--body", required=True)
    p_reply.add_argument("--file", default=None)
    p_reply.set_defaults(func=cmd_reply)

    p_pickup = sub.add_parser(
        "pickup", help="claim every open task still assigned to --for")
    p_pickup.add_argument("--for", dest="for_",
                          default="todd@smart1marketing.com")
    p_pickup.set_defaults(func=cmd_pickup)

    args = parser.parse_args()
    try:
        return args.func(args)
    except YodaQaError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
