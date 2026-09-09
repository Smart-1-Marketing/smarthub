"""Bulk check upload and one-time import of checks already reconciled in chat.

Installed after the main reconciliation module loads so the accounting core stays
small. This adds multi-file intake plus a safe one-time import queue. It never
posts a QuickBooks payment; posting still requires the existing explicit
invoice-allocation approval flow.
"""
from __future__ import annotations

import secrets
from pathlib import Path

from flask import jsonify, request
from werkzeug.utils import secure_filename


# Checks captured in the September 2026 reconciliation conversation. These are
# intake records only. Four rows were already verified paid in QuickBooks and
# are imported locked as external_paid so they cannot be posted again.
KNOWN_CHECKS = [
    ("2026-05-15", "Page Customs, LLC", 30.00, False),
    ("2026-05-19", "Cars & Carts Automotive", 1198.00, True),
    ("2026-06-10", "Trasin Corporation", 24.50, False),
    ("2026-06-16", "New Freedom Resources", 2598.00, True),
    ("2026-06-17", "Mattingly Cold Storage", 304.50, False),
    ("2026-07-06", "Massey's Pizza", 79.00, False),
    ("2026-07-06", "B.C. Excavating Ltd.", 82.04, False),
    ("2026-07-07", "New Freedom Resources", 798.00, True),
    ("2026-07-08", "2060 Digital, LLC", 30.00, False),
    ("2026-07-08", "Page Customs, LLC", 30.00, False),
    ("2026-07-08", "Urban One", 19.00, False),
    ("2026-07-08", "Amedore Group, Inc.", 900.00, False),
    ("2026-07-09", "Cars & Carts Automotive", 2000.00, True),
    ("2026-07-10", "Dent Wizard International", 1030.00, False),
    ("2026-07-14", "Trasin Corporation", 24.50, False),
    ("2026-07-15", "Keene Publishing Corp.", 79.00, False),
    ("2026-07-15", "Monogram Homes", 2370.00, False),
    ("2026-07-17", "Michelle's Academy / Eastgate", 500.00, False),
    ("2026-07-20", "American Air", 1180.00, False),
    ("2026-07-24", "SummitMedia, LLC", 4552.52, False),
    ("2026-07-30", "J Fred Schmidt Packing / Schmidt's", 13632.00, False),
    ("2026-07-30", "N2 Advertising", 24196.65, False),
    ("2026-08-05", "Top Marketing Group", 632.00, False),
    ("2026-08-06", "Van Wagner Sports & Entertainment", 7850.00, False),
    ("2026-08-10", "N2 Advertising", 1105.00, False),
    ("2026-08-11", "Cars & Carts Automotive", 2000.00, False),
    ("2026-08-13", "Hern Marine Distributors", 8000.00, False),
    ("2026-08-17", "Michelle's Academy / Eastgate", 500.00, False),
    ("2026-08-19", "Keene Publishing Corp.", 79.00, False),
    ("2026-08-27", "Gazette News Group, Inc.", 408.00, False),
    ("2026-08-28", "Dent Wizard International", 1030.00, False),
    ("2026-09-01", "Consumer Support Services, Inc.", 129.00, False),
    ("2026-09-02", "J Fred Schmidt Packing / Schmidt's", 13232.00, False),
]

# Payer aliases the user already confirmed. Resolve these against live QBO
# customer names at import time rather than hard-coding customer IDs.
CONFIRMED_ALIAS_NAMES = {
    "Page Customs, LLC": ["Page Customs LLC"],
    "Cars & Carts Automotive": ["CARS & CARTS AUTOMOTIVE INC."],
    "Trasin Corporation": ["Trasin Asphalt and Concrete"],
    "New Freedom Resources": ["New Freedom Resources, LLC"],
    "Mattingly Cold Storage": ["Mattingly Cold Storage"],
    "Massey's Pizza": ["Masseys Pizza"],
    "B.C. Excavating Ltd.": ["BC Excavating"],
    "2060 Digital, LLC": ["Hubbard Interactive 2060 Digital"],
    "Amedore Group, Inc.": ["Amedore Group , INC"],
    "Dent Wizard International": ["Dent Wizard International dba Magic by Dent Wizard"],
    "Keene Publishing Corp.": ["Keene Publishing Corporation"],
    "Monogram Homes": ["Monogram Homes"],
    "Michelle's Academy / Eastgate": ["Michelle's Academy dba Eastgate ELA"],
    "American Air": ["American Air Heating, Cooling, Electric, & Plumbing"],
    "J Fred Schmidt Packing / Schmidt's": ["J Fred Schmidt Packing Co"],
    "N2 Advertising": ["N2 Advertising"],
    "Top Marketing Group": ["TOP Marketing Group Lexington", "Top Marketing Group Louisville"],
    "Hern Marine Distributors": ["Hern Marine Distributors, LTD"],
}


def install_bulk(module) -> None:
    app = module.app

    def make_record(file, *, source="upload"):
        raw = file.read()
        if not raw:
            raise ValueError(f"{file.filename or 'Uploaded file'} is empty.")
        mime = file.mimetype or "application/octet-stream"
        ext = Path(secure_filename(file.filename or "check")).suffix.lower()[:8] or ".bin"
        cid = "chk_" + secrets.token_hex(8)
        path = module.UPLOAD_DIR / f"{cid}{ext}"
        module._ensure_dirs()
        path.write_bytes(raw)
        extracted = module._extract_check(raw, mime)
        amount_raw = extracted.get("amount")
        try:
            amount = round(float(str(amount_raw).replace("$", "").replace(",", "")), 2) if amount_raw not in (None, "") else None
        except Exception:
            amount = None
        return {
            "id": cid,
            "created_at": module._now(),
            "payer": str(extracted.get("payer") or "").strip(),
            "date": str(extracted.get("date") or "").strip(),
            "amount": amount,
            "check_number": str(extracted.get("check_number") or "").strip(),
            "ocr_confidence": extracted.get("confidence"),
            "ocr_error": extracted.get("ocr_error"),
            "file": path.name,
            "source": source,
            "status": "new",
            "customer_matches": [],
            "selected_customers": [],
            "suggestion": {},
            "payments": [],
        }

    def upload_many():
        files = [f for f in request.files.getlist("files") if f and f.filename]
        if not files:
            return module._api_error(ValueError("Choose at least one check image."))
        if len(files) > 25:
            return module._api_error(ValueError("Upload up to 25 checks at a time."))
        created, errors = [], []
        for f in files:
            try:
                rec = make_record(f)
                module._mutate(lambda state, r=rec: state["checks"].append(r))
                module._audit("check_uploaded", check_id=rec["id"], payer=rec["payer"], amount=rec["amount"], date=rec["date"], filename=f.filename)
                created.append(rec)
            except Exception as exc:
                errors.append({"filename": f.filename, "error": str(exc)[:500]})
        if not created:
            return jsonify({"ok": False, "error": "No checks could be read.", "errors": errors}), 400
        return jsonify({"ok": True, "checks": created, "count": len(created), "errors": errors})

    def import_known():
        state = module._read_state()
        existing = {
            (str(x.get("date") or ""), module._normalize_name(str(x.get("payer") or "")), round(float(x.get("amount") or 0), 2))
            for x in state["checks"]
        }
        customers = []
        customer_error = None
        try:
            customers = module._all_customers()
        except Exception as exc:
            customer_error = str(exc)[:500]
        by_norm = {}
        for c in customers:
            for candidate in (c.get("DisplayName"), c.get("CompanyName"), c.get("FullyQualifiedName")):
                key = module._normalize_name(str(candidate or ""))
                if key and key not in by_norm:
                    by_norm[key] = c

        alias_updates = {}
        for payer, wanted_names in CONFIRMED_ALIAS_NAMES.items():
            chosen = []
            for wanted in wanted_names:
                c = by_norm.get(module._normalize_name(wanted))
                if c and str(c.get("Id") or ""):
                    chosen.append({"id": str(c["Id"]), "name": str(c.get("DisplayName") or wanted)})
            if chosen:
                alias_updates[module._normalize_name(payer)] = {
                    "payer": payer, "customers": chosen, "confirmed_at": module._now(), "source": "chat_import"
                }

        created = []
        for date, payer, amount, already_paid in KNOWN_CHECKS:
            key = (date, module._normalize_name(payer), round(amount, 2))
            if key in existing:
                continue
            alias = alias_updates.get(module._normalize_name(payer))
            selected = list((alias or {}).get("customers") or [])
            rec = {
                "id": "chk_" + secrets.token_hex(8),
                "created_at": module._now(),
                "payer": payer,
                "date": date,
                "amount": amount,
                "check_number": "",
                "ocr_confidence": "imported",
                "file": "",
                "source": "chat_import",
                "status": "external_paid" if already_paid else ("matched" if selected else "new"),
                "customer_matches": [],
                "selected_customers": selected,
                "suggestion": {},
                "payments": [],
                "external_paid": already_paid,
            }
            created.append(rec)
            existing.add(key)

        def apply(s):
            s["checks"].extend(created)
            s["aliases"].update(alias_updates)
        module._mutate(apply)
        module._audit("chat_checks_imported", count=len(created), aliases=len(alias_updates))
        return jsonify({"ok": True, "count": len(created), "aliases": len(alias_updates), "customer_lookup_error": customer_error})

    # Register only once in case the module is reloaded during tests.
    if "checkrec_upload_many" not in app.view_functions:
        app.add_url_rule("/api/upload-many", "checkrec_upload_many", upload_many, methods=["POST"])
    if "checkrec_import_known" not in app.view_functions:
        app.add_url_rule("/api/import-known", "checkrec_import_known", import_known, methods=["POST"])

    page = getattr(module, "_PAGE", "")
    if not page:
        return

    # Turn the existing uploader into a multi-file intake without disturbing the
    # accounting review/posting UI below it.
    page = page.replace(
        'type="file" name="file" accept="image/*,.pdf" required',
        'type="file" name="files" accept="image/*,.pdf" multiple required',
    )
    page = page.replace("<label>Check image</label>", "<label>Check images (one or many)</label>")
    page = page.replace("Upload & read check", "Upload & read check(s)")
    page = page.replace("api('api/upload',{method:'POST',body:f})", "api('api/upload-many',{method:'POST',body:f})")
    page = page.replace(
        "setTimeout(()=>document.getElementById(r.check.id)?.scrollIntoView({behavior:'smooth'}),50)",
        "$('#uploadMsg').innerHTML=`<div class=\"success\">Added ${r.count} check${r.count===1?'':'s'}${r.errors?.length?' · '+r.errors.length+' file(s) need review':''}.</div>`;let first=r.checks&&r.checks[0];if(first)setTimeout(()=>document.getElementById(first.id)?.scrollIntoView({behavior:'smooth'}),50)",
    )

    # Add one-click import for the checks already supplied in this conversation.
    marker = '<div class="toolbar" style="margin:16px 0"><button class="btn" onclick="loadChecks()">Refresh</button>'
    replacement = '<div class="card"><div class="titleline"><div><b>Checks already provided in ChatGPT</b><div class="muted">Import the previously captured check dates, payers and amounts into this queue. Existing duplicates are skipped and confirmed payer aliases are reused.</div></div><button class="btn" id="importKnown" onclick="importKnownChecks()">Import previous checks</button></div><div id="importKnownMsg"></div></div>' + marker
    page = page.replace(marker, replacement)

    # Lock rows that were already verified paid before SmartHub was introduced.
    page = page.replace("let posted=c.status==='posted';", "let posted=c.status==='posted'||c.status==='external_paid';")
    page = page.replace("f==='posted'?c.status==='posted':c.status!=='posted'", "f==='posted'?(c.status==='posted'||c.status==='external_paid'):(c.status!=='posted'&&c.status!=='external_paid')")
    page = page.replace("data.checks.filter(x=>x.status==='posted').length", "data.checks.filter(x=>x.status==='posted'||x.status==='external_paid').length")
    page = page.replace("<b>Posted</b><br>${pay||''}", "<b>${c.status==='external_paid'?'Already paid in QuickBooks':'Posted'}</b><br>${pay||''}")

    inject = "async function importKnownChecks(){let b=$('#importKnown');let m=$('#importKnownMsg');b.disabled=true;m.textContent='Importing…';try{let r=await api('api/import-known',{method:'POST',body:'{}'});m.innerHTML=`<div class=\"success\">Imported ${r.count} previous check${r.count===1?'':'s'} and ${r.aliases} confirmed client match${r.aliases===1?'':'es'}. Duplicates were skipped.</div>`;await loadChecks()}catch(e){m.innerHTML=`<div class=\"error\">${esc(e.message)}</div>`}finally{b.disabled=false}};"
    page = page.replace("qboStatus();loadChecks();", inject + "qboStatus();loadChecks();")
    module._PAGE = page
