"""Local-only fixture for the browser workflow test; no provider calls."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import jsonify, send_from_directory
from test_industry_core import CoreTests
from hub import auth

def serve():
    CoreTests.setUpClass()
    app=CoreTests.app
    auth.user_from_environ=lambda environ:'Browser test'
    from hub import industry_prospect_store
    industry_prospect_store.init_store()
    industry_prospect_store.put('campaign:browser-audience', 'campaign', {'id':'browser-audience', 'name':'Ohio roofing audience', 'industry':'roofing', 'filters':{'organization_locations[]':['Columbus, OH']}})

    @app.get('/assets/<path:name>')
    def assets(name):
        return send_from_directory(str(ROOT/'hub/static'),name)

    @app.get('/test-parent/<page_id>')
    def parent(page_id):
        from flask import render_template_string
        return render_template_string('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><script src="/industry/widget/{{ page_id }}/embed.js"></script></body></html>',page_id=page_id)

    @app.post('/api/leads/capture')
    def lead_fixture():
        return jsonify(ok=True,lead_id='browser-fixture')

    app.run(host='127.0.0.1',port=8773,use_reloader=False)


if __name__=='__main__': serve()
