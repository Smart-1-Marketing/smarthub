"""Deterministic sample data.

Runs when Knack is not configured, so the whole tool can be clicked through
before a single credential is set. Every audit condition is represented at
least once on purpose, which is also how the reports get tested.
"""
import random
from datetime import datetime, timedelta

TICKET_FIELDS = [
    ("field_101", "Ticket Number", "auto_increment"),
    ("field_102", "Client", "connection"),
    ("field_103", "Status", "multiple_choice"),
    ("field_104", "Date Opened", "date_time"),
    ("field_105", "Date Closed", "date_time"),
    ("field_106", "Last Updated", "date_time"),
    ("field_107", "Request Type", "multiple_choice"),
    ("field_108", "Priority", "multiple_choice"),
    ("field_109", "Assigned To", "user"),
    ("field_110", "Summary", "short_text"),
    ("field_111", "Time Spent", "number"),
    ("field_112", "Source", "multiple_choice"),
]

CLIENT_FIELDS = [
    ("field_201", "Client Name", "short_text"),
    ("field_202", "Monthly Price", "currency"),
    ("field_203", "Products", "multiple_choice"),
    ("field_204", "Status", "multiple_choice"),
]

CLIENTS = [
    # name, monthly, products, profile
    ("Buckeye Heating & Air", 1500, ["SEO", "Google Ads", "Suite"], "heavy"),
    ("Vaughn Family Law", 2500, ["SEO", "Suite", "Sites"], "normal"),
    ("Olentangy Powersports", 900, ["Google Ads", "Sites"], "heavy"),
    ("Riverbend Marina", 3500, ["SEO", "Google Ads", "Suite", "Sites"], "normal"),
    ("Hocking Hills Cabins", 750, ["Sites"], "quiet"),
    ("Delaware County Dental", 1200, ["SEO", "Suite"], "aging"),
    ("Grandview Bistro", 500, ["Sites"], "heavy"),
    ("Scioto Roofing Co", 2000, ["SEO", "Google Ads"], "normal"),
    ("Westerville Auto Group", 5000, ["SEO", "Google Ads", "Suite", "Sites"], "normal"),
    ("Pickaway Feed & Supply", 750, ["Sites"], "quiet"),
]

TYPES = ["Content change", "New page", "Broken link", "Form not working",
         "Hosting / DNS", "Image update", "Tracking / analytics", "Speed complaint"]
ASSIGNEES = ["Todd", "Megan", "Chris", "Unassigned"]
SOURCES = ["Web form", "Email", "Phone", "Suite chat"]


def demo_fields(object_key):
    rows = CLIENT_FIELDS if "client" in object_key else TICKET_FIELDS
    return [{"key": k, "name": n, "type": t} for k, n, t in rows]


def demo_fieldmap():
    return {
        "ticket_number": "field_101", "client": "field_102", "status": "field_103",
        "opened": "field_104", "closed": "field_105", "last_update": "field_106",
        "type": "field_107", "priority": "field_108", "assignee": "field_109",
        "summary": "field_110", "time_spent": "field_111", "source": "field_112",
    }


def demo_client_fieldmap():
    return {"name": "field_201", "monthly_price": "field_202",
            "products": "field_203", "status": "field_204"}


def _iso(dt):
    return dt.strftime("%m/%d/%Y %I:%M %p")


def demo_records(object_key):
    if "client" in object_key:
        return [
            {
                "id": f"cl{i:03d}",
                "field_201": name,
                "field_202": monthly,
                "field_202_raw": monthly,
                "field_203": ", ".join(products),
                "field_204": "Active",
            }
            for i, (name, monthly, products, _) in enumerate(CLIENTS)
        ]

    rng = random.Random(20260817)
    now = datetime.now()
    records = []
    n = 0
    for i, (name, monthly, _products, profile) in enumerate(CLIENTS):
        if profile == "quiet":
            count = 0
        elif profile == "heavy":
            count = rng.randint(18, 26)
        else:
            count = rng.randint(4, 9)

        for _ in range(count):
            n += 1
            age = rng.randint(0, 120)
            opened = now - timedelta(days=age, hours=rng.randint(0, 20))

            if profile == "aging" and rng.random() < 0.5:
                closed = None
                status = "In Progress"
                opened = now - timedelta(days=rng.randint(9, 40))
            elif rng.random() < 0.22:
                closed = None
                status = rng.choice(["Open", "In Progress", "Waiting on Client"])
            else:
                closed = opened + timedelta(days=rng.randint(0, 9),
                                            hours=rng.randint(1, 20))
                if closed > now:
                    closed = None
                    status = "In Progress"
                else:
                    status = rng.choice(["Closed", "Completed", "Resolved"])

            last = closed or (opened + timedelta(days=rng.randint(0, 12)))
            if last > now:
                last = now

            rec = {
                "id": f"tk{n:04d}",
                "field_101": 1000 + n,
                "field_102": name,
                "field_102_raw": [{"id": f"cl{i:03d}", "identifier": name}],
                "field_103": status,
                "field_104": _iso(opened),
                "field_104_raw": {"iso_timestamp": opened.isoformat()},
                "field_105": _iso(closed) if closed else "",
                "field_105_raw": {"iso_timestamp": closed.isoformat()} if closed else None,
                "field_106": _iso(last),
                "field_106_raw": {"iso_timestamp": last.isoformat()},
                "field_107": rng.choice(TYPES),
                "field_108": rng.choice(["Low", "Normal", "High", "Urgent"]),
                "field_109": rng.choice(ASSIGNEES),
                "field_110": "Client request logged from the website form.",
                "field_111": round(rng.choice([0.25, 0.5, 0.75, 1, 1.5, 2, 3]), 2),
                "field_112": rng.choice(SOURCES),
            }
            records.append(rec)

    # A repeat-issue cluster: same client, same type, inside the window.
    for k in range(4):
        n += 1
        opened = datetime.now() - timedelta(days=3 + k * 4)
        records.append({
            "id": f"tk{n:04d}",
            "field_101": 1000 + n,
            "field_102": "Olentangy Powersports",
            "field_102_raw": [{"id": "cl002", "identifier": "Olentangy Powersports"}],
            "field_103": "Closed",
            "field_104": _iso(opened),
            "field_104_raw": {"iso_timestamp": opened.isoformat()},
            "field_105": _iso(opened + timedelta(days=1)),
            "field_105_raw": {"iso_timestamp": (opened + timedelta(days=1)).isoformat()},
            "field_106": _iso(opened + timedelta(days=1)),
            "field_106_raw": {"iso_timestamp": (opened + timedelta(days=1)).isoformat()},
            "field_107": "Form not working",
            "field_108": "High",
            "field_109": "Megan",
            "field_110": "Contact form submissions not arriving again.",
            "field_111": 1.25,
            "field_112": "Email",
        })

    # Two tickets whose client name matches nothing on file.
    for label in ("Hilliard Pet Resort", "smart1 test account"):
        n += 1
        opened = datetime.now() - timedelta(days=11)
        records.append({
            "id": f"tk{n:04d}",
            "field_101": 1000 + n,
            "field_102": label,
            "field_102_raw": [{"id": "", "identifier": label}],
            "field_103": "Open",
            "field_104": _iso(opened),
            "field_104_raw": {"iso_timestamp": opened.isoformat()},
            "field_105": "",
            "field_106": _iso(opened),
            "field_106_raw": {"iso_timestamp": opened.isoformat()},
            "field_107": "New page",
            "field_108": "Normal",
            "field_109": "Unassigned",
            "field_110": "Requested a new landing page.",
            "field_111": 0,
            "field_112": "Web form",
        })

    return records
