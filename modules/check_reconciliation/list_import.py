"""CSV list import for Check Reconciliation.

Lets the owner maintain a spreadsheet outside SmartHub, save/export it as CSV,
and import the rows into the reconciliation queue. Import never posts a QBO
Payment. Duplicate date/payer/amount rows are skipped and every imported row
still uses the existing explicit approval flow before any accounting write.
"""
from __future__ import annotations

import csv
import io
import secrets
from typing import Any

from flask import jsonify, request

REQUIRED = {"Check Date", "Payer", "Check Amount"}
OPTIONAL = {
    "Check Number", "QBO Customer", "Invoice Numbers", "Apply Amounts",
    "Status", "Notes",
}


def _money(value: Any):
    if value in (None, ""):
        return None
    return round(float(str(value).replace("$", "").replace(",", "").strip()), 2)


def _parts(value: str) -> list[str]:
    return [x.strip() for x in str(value or "").split(";") if x.strip()]


def install_list_import(module) -> None:
    app = module.app

    def import_list():
        f = request.files.get("file")
        if not f or not f.filename:
            return module._api_error(ValueError("Choose a SmartHub reconciliation CSV file."))
        if not f.filename.lower().endswith(".csv"):
            return module._api_error(ValueError("Upload a .csv file. Use the SmartHub reconciliation template headers."))
        raw = f.read()
        if not raw:
            return module._api_error(ValueError("The CSV file is empty."))
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        headers = set(reader.fieldnames or [])
        missing = REQUIRED - headers
        if missing:
            return module._api_error(ValueError("Missing required column(s): " + ", ".join(sorted(missing))))

        state = module._read_state()
        existing = {
            (str(x.get("date") or ""), module._normalize_name(str(x.get("payer") or "")), round(float(x.get("amount") or 0), 2))
            for x in state["checks"]
        }

        # Resolve customers once for the whole import.
        customers = module._all_customers()
        by_norm: dict[str, list[dict[str, Any]]] = {}
        for c in customers:
            for candidate in (c.get("DisplayName"), c.get("CompanyName"), c.get("FullyQualifiedName")):
                key = module._normalize_name(str(candidate or ""))
                if key:
                    by_norm.setdefault(key, [])
                    if not any(str(x.get("Id")) == str(c.get("Id")) for x in by_norm[key]):
                        by_norm[key].append(c)

        created = []
        skipped = 0
        review = []
        alias_updates = {}

        for row_no, row in enumerate(reader, start=2):
            payer = str(row.get("Payer") or "").strip()
            date = str(row.get("Check Date") or "").strip()
            try:
                amount = _money(row.get("Check Amount"))
            except Exception:
                review.append({"row": row_no, "error": "Invalid Check Amount"})
                continue
            if not payer or not date or amount is None:
                review.append({"row": row_no, "error": "Check Date, Payer and Check Amount are required"})
                continue
            key = (date, module._normalize_name(payer), round(amount, 2))
            if key in existing:
                skipped += 1
                continue

            status_label = str(row.get("Status") or "Ready to Apply").strip().lower()
            qbo_names = _parts(row.get("QBO Customer") or "")
            invoice_docs = _parts(row.get("Invoice Numbers") or "")
            amount_parts = _parts(row.get("Apply Amounts") or "")
            try:
                apply_amounts = [_money(x) for x in amount_parts]
            except Exception:
                apply_amounts = []
                review.append({"row": row_no, "error": "One of the Apply Amounts is invalid"})

            chosen = []
            for wanted in qbo_names:
                matches = by_norm.get(module._normalize_name(wanted), [])
                if len(matches) == 1:
                    c = matches[0]
                    if not any(str(x.get("Id")) == str(c.get("Id")) for x in chosen):
                        chosen.append(c)

            # If the CSV omitted QBO Customer, reuse any remembered alias.
            if not chosen:
                alias = module._alias_for(payer)
                alias_ids = {str(x.get("id")) for x in (alias or {}).get("customers", [])}
                chosen = [c for c in customers if str(c.get("Id")) in alias_ids]

            selected = [
                {"id": str(c.get("Id") or ""), "name": str(c.get("DisplayName") or "")}
                for c in chosen if str(c.get("Id") or "")
            ]

            invoices = []
            suggestion = {"allocations": [], "reason": "Imported list - review allocation"}
            if selected:
                invoices = module._open_invoices([x["id"] for x in selected])
                if invoice_docs:
                    by_doc = {str(x.get("doc_number") or "").strip().lower(): x for x in invoices}
                    allocations = []
                    missing_docs = []
                    for idx, doc in enumerate(invoice_docs):
                        inv = by_doc.get(doc.lower())
                        if not inv:
                            missing_docs.append(doc)
                            continue
                        val = apply_amounts[idx] if idx < len(apply_amounts) and apply_amounts[idx] is not None else None
                        if val is None:
                            principal = round(float(inv.get("balance") or 0) - float(inv.get("late_fees") or 0), 2)
                            val = principal if principal > 0 else round(float(inv.get("balance") or 0), 2)
                        allocations.append({"invoice_id": inv["id"], "amount": round(float(val), 2)})
                    if allocations:
                        suggestion = {
                            "allocations": allocations,
                            "reason": "Imported invoice allocation",
                            "late_fee_warning": any(float(by_doc.get(d.lower(), {}).get("late_fees") or 0) > 0 for d in invoice_docs),
                        }
                    if missing_docs:
                        review.append({"row": row_no, "error": "Invoice(s) not currently open in QBO: " + ", ".join(missing_docs)})
                else:
                    suggestion = module._suggest_allocations(amount, invoices)

            external_paid = status_label in {"already paid", "paid", "external_paid"}
            if external_paid:
                status = "external_paid"
            elif selected and suggestion.get("allocations"):
                status = "ready"
            elif selected:
                status = "matched"
            else:
                status = "new"

            rec = {
                "id": "chk_" + secrets.token_hex(8),
                "created_at": module._now(),
                "payer": payer,
                "date": date,
                "amount": amount,
                "check_number": str(row.get("Check Number") or "").strip(),
                "ocr_confidence": "csv_import",
                "file": "",
                "source": "csv_import",
                "status": status,
                "customer_matches": [],
                "selected_customers": selected,
                "invoices": invoices,
                "suggestion": suggestion,
                "payments": [],
                "external_paid": external_paid,
                "import_notes": str(row.get("Notes") or "").strip(),
                "import_status": str(row.get("Status") or "").strip(),
                "import_invoice_numbers": invoice_docs,
            }
            created.append(rec)
            existing.add(key)

            if selected and qbo_names:
                alias_updates[module._normalize_name(payer)] = {
                    "payer": payer,
                    "customers": selected,
                    "confirmed_at": module._now(),
                    "source": "csv_import",
                }

        def apply(state_obj):
            state_obj["checks"].extend(created)
            state_obj["aliases"].update(alias_updates)
        module._mutate(apply)
        module._audit("reconciliation_list_imported", count=len(created), skipped=skipped, review=len(review), filename=f.filename)
        return jsonify({"ok": True, "count": len(created), "skipped": skipped, "review": review[:100], "review_count": len(review)})

    if "checkrec_import_list" not in app.view_functions:
        app.add_url_rule("/api/import-list", "checkrec_import_list", import_list, methods=["POST"])

    page = getattr(module, "_PAGE", "")
    if not page or 'id="importListForm"' in page:
        return

    card = r'''
<div class="card">
  <div class="titleline"><div><b>Import reconciliation list</b><div class="muted">Upload a CSV created from the SmartHub reconciliation spreadsheet. Duplicates are skipped. Importing never posts a QuickBooks payment.</div></div></div>
  <form id="importListForm" class="toolbar" style="margin-top:12px">
    <input id="importListFile" type="file" name="file" accept=".csv,text/csv" required>
    <button class="btn" id="importListBtn" type="submit">Import reconciliation list</button>
    <span id="importListMsg" class="muted"></span>
  </form>
</div>
'''
    marker = '<div class="toolbar" style="margin:16px 0"><button class="btn" onclick="loadChecks()">Refresh</button>'
    page = page.replace(marker, card + marker, 1)

    script = r'''
<script>
(function(){
  function escList(s){return String(s||'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  async function wireListImport(){
    var form=document.getElementById('importListForm');
    if(!form || form.dataset.wired) return;
    form.dataset.wired='1';
    form.addEventListener('submit',async function(ev){
      ev.preventDefault();
      var file=document.getElementById('importListFile');
      var btn=document.getElementById('importListBtn');
      var msg=document.getElementById('importListMsg');
      if(!file || !file.files || !file.files.length) return;
      btn.disabled=true; msg.textContent='Importing…';
      try{
        var fd=new FormData(); fd.append('file',file.files[0]);
        var r=await fetch('api/import-list',{method:'POST',body:fd,headers:{'Accept':'application/json'}});
        var j=await r.json().catch(function(){return {error:'Unexpected server response'};});
        if(!r.ok || j.ok===false) throw new Error(j.error||('Request failed '+r.status));
        var extra=j.review_count?' · '+j.review_count+' row(s) need review':'';
        msg.innerHTML='<span class="success">Imported '+j.count+' row(s); skipped '+j.skipped+' duplicate(s)'+extra+'.</span>';
        if(j.review && j.review.length){
          msg.innerHTML += '<div class="warning">'+j.review.slice(0,8).map(function(x){return 'Row '+x.row+': '+escList(x.error);}).join('<br>')+'</div>';
        }
        file.value='';
        if(typeof window.loadChecks==='function') await window.loadChecks(); else window.location.reload();
      }catch(e){msg.innerHTML='<span class="error">'+escList(e.message||e)+'</span>';}
      finally{btn.disabled=false;}
    });
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',wireListImport); else wireListImport();
})();
</script>
'''
    if "</body>" in page:
        page = page.replace("</body>", script + "</body>", 1)
    else:
        page += script
    module._PAGE = page
