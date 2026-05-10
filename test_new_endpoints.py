import sys
sys.path.insert(0, '.')
from app.workspace_server import _build_widgets_json

# Verify widgets
widgets = _build_widgets_json()
print(f'Widgets: {len(widgets)} total')

# Check for new widgets
required = ['api_halal-status', 'api_consensus']
for r in required:
    wid = r.replace('/', '_')
    assert wid in widgets, f'Missing widget: {wid}'
    w = widgets[wid]
    print(f'  OK [Analytics] {w["name"]} ({w["gridData"]["w"]}x{w["gridData"]["h"]})')

# Count categories
cats = set()
for w in widgets.values():
    cats.add(w['category'])
print(f'\nCategories ({len(cats)}):')
for c in sorted(cats):
    print(f'  {c}')

print('\nAll widget checks passed')

# Verify imports work
from app.workspace_server import _screen_halal
print('\n_screen_halal imported OK')

# Verify consensus imports
from app.workspace_server import MODEL_NAMES, create_model, compute_forecast_metrics
print(f'Consensus imports OK: {len(MODEL_NAMES)} models available')

print('\nAll checks passed')
