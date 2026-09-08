"""Compile the real server template; JavaScript-only previews cannot catch Jinja errors."""
from pathlib import Path
from types import SimpleNamespace
from jinja2 import Environment

source = Path('modules/io_builder/templates/index.html').read_text(encoding='utf-8')
template = Environment().from_string(source)
for mount in ('', '/tools/io'):
    page = template.render(request=SimpleNamespace(script_root=mount), own_api_paths=[])
    assert 'id="startScreen"' in page
    assert 'id="ioReadiness"' in page
    assert 'id="ioReport" hidden' in page
    assert f'fetch("{mount}/api/drafts"' in page
print('PASS: IO template compiles and renders standalone and mounted')
