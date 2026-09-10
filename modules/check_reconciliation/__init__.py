"""SmartHub private QuickBooks check reconciliation module.

The Hub already owns one QuickBooks OAuth connection in ``hub.quickbooks``.
Check Reconciliation must use that same token store rather than opening a
second Intuit connection: Intuit rotates refresh tokens, so two independent
stores can invalidate one another. This package installs a one-shot loader
wrapper that wires the check module to the Hub connector immediately after the
module is imported.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import sys

import requests

_TARGET = "modules.check_reconciliation.app"


def _install(module) -> None:
    """Route every QuickBooks read/write through the Hub's shared connector."""
    from hub import quickbooks as qb

    def shared_record():
        tok = qb._load_tokens() or {}
        return {
            "realm_id": tok.get("realm_id"),
            "access_expires_at": int(tok.get("expires_at") or 0),
            "connected_at": tok.get("obtained_at"),
        }

    def shared_qbo(method: str, path: str, *, params=None, payload=None, retry=True):
        tok = qb._ensure_access_token()
        realm = tok.get("realm_id")
        if not realm:
            raise RuntimeError("QuickBooks is not connected. Connect it from System Status.")
        url = f"{qb._api_base()}/v3/company/{realm}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {tok['access_token']}",
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        query = dict(params or {})
        query.setdefault("minorversion", getattr(module, "QBO_MINOR_VERSION", "75"))
        response = requests.request(
            method, url, headers=headers, params=query, json=payload, timeout=45
        )
        if response.status_code == 401 and retry:
            stored = qb._load_tokens() or {}
            if stored:
                stored["expires_at"] = 0
                qb._save_tokens(stored)
            qb._ensure_access_token()
            return shared_qbo(method, path, params=query, payload=payload, retry=False)
        if not response.ok:
            try:
                detail = json.dumps(response.json())[:1600]
            except Exception:
                detail = response.text[:1600]
            raise RuntimeError(f"QuickBooks API error {response.status_code}: {detail}")
        if not response.content:
            return {}
        return response.json()

    module._qbo_configured = qb.configured
    module._oauth_record = shared_record
    module._qbo = shared_qbo
    module._redirect_uri = lambda: qb.redirect_uri(module.request)

    # Hub's shared theme also defines a generic `.spinner` animation. Keep this
    # module's small upload status isolated from it.
    page = getattr(module, "_PAGE", "")
    if page:
        page = page.replace(
            ".spinner{display:none}.busy .spinner{display:inline}",
            ".checkrec-loading{display:none;margin-left:8px;color:var(--muted);font-size:13px;vertical-align:middle}.busy .checkrec-loading{display:inline}",
        )
        page = page.replace(
            '<span class="spinner">Reading…</span>',
            '<span class="checkrec-loading" role="status" aria-live="polite">Reading…</span>',
        )
        module._PAGE = page

    # Add bulk intake and the one-time import of checks previously supplied in
    # ChatGPT. This extension only populates the reconciliation queue; all QBO
    # writes still go through the main module's explicit approval flow.
    from modules.check_reconciliation.bulk import install_bulk
    install_bulk(module)

    # Add owner-maintained CSV reconciliation lists. The import only creates
    # queue records and suggested invoice allocations; it never posts a QBO
    # Payment without the existing explicit approval step.
    from modules.check_reconciliation.list_import import install_list_import
    install_list_import(module)

    # The bulk extension originally injected importKnownChecks by replacing an
    # exact end-of-script string. That was brittle and could leave the button
    # visible with no click handler when the base page changed. Always install a
    # small independent handler after bulk has finished modifying the page.
    page = getattr(module, "_PAGE", "")
    if page and 'id="importKnown"' in page:
        import_script = r'''
<script>
(function(){
  async function runKnownImport(){
    var b=document.getElementById('importKnown');
    var m=document.getElementById('importKnownMsg');
    if(!b) return;
    b.disabled=true;
    if(m) m.textContent='Importing…';
    try{
      var r=await fetch('api/import-known',{
        method:'POST',
        headers:{'Accept':'application/json','Content-Type':'application/json'},
        body:'{}'
      });
      var j=await r.json().catch(function(){return {error:'Unexpected server response'};});
      if(!r.ok || j.ok===false) throw new Error(j.error || ('Request failed '+r.status));
      if(m){
        m.innerHTML='<div class="success">Imported '+j.count+' previous check'+(j.count===1?'':'s')+
          ' and '+j.aliases+' confirmed client match'+(j.aliases===1?'':'es')+'. Duplicates were skipped.</div>'+
          (j.customer_lookup_error?'<div class="warning">QuickBooks customer lookup warning: '+String(j.customer_lookup_error).replace(/[&<>"\']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];})+'</div>':'');
      }
      if(typeof window.loadChecks==='function') await window.loadChecks();
      else window.location.reload();
    }catch(e){
      if(m) m.innerHTML='<div class="error">'+String(e.message||e)+'</div>';
      else alert(e.message||e);
    }finally{
      b.disabled=false;
    }
  }
  window.importKnownChecks=runKnownImport;
  function wire(){
    var b=document.getElementById('importKnown');
    if(b){
      b.onclick=function(ev){ev.preventDefault();runKnownImport();};
      b.setAttribute('type','button');
    }
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',wire);
  else wire();
})();
</script>
'''
        if "</body>" in page:
            page = page.replace("</body>", import_script + "</body>", 1)
        else:
            page += import_script
        module._PAGE = page


class _BridgeLoader(importlib.abc.Loader):
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def create_module(self, spec):
        create = getattr(self.wrapped, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module):
        self.wrapped.exec_module(module)
        _install(module)


class _BridgeFinder(importlib.abc.MetaPathFinder):
    """Intercept only the check module, once, then get out of import machinery."""

    def find_spec(self, fullname, path, target=None):
        if fullname != _TARGET:
            return None
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.loader:
            spec.loader = _BridgeLoader(spec.loader)
        return spec


if _TARGET not in sys.modules:
    sys.meta_path.insert(0, _BridgeFinder())
