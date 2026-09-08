"""Checks required before a priced proposal can be shared or converted."""
from __future__ import annotations

import math
import re

from . import rate_card, creative_needs, product_intake


def readiness(state):
    state = state or {}
    problems = []
    if not str(state.get('client') or '').strip():
        problems.append('Enter the client name.')
    items = state.get('items') or []
    if not items:
        problems.append('Add a priced media or service plan.')
    for item in items:
        label = item.get('product') or item.get('category') or 'Product'
        try:
            amount = float(item.get('dollars') or 0)
        except (ValueError, TypeError):
            amount = float('nan')
        if not math.isfinite(amount) or amount <= 0:
            problems.append(f'{label}: enter a positive, finite price.')
        elif item.get('basis') != 'one_time' and item.get('product') != product_intake.CONSULTING['product']:
            minimum = rate_card.minimum_for(item.get('product'), item.get('category'))
            if amount < minimum:
                problems.append(f'{label}: ${amount:,.2f}/mo is below the ${minimum:,.2f} IO minimum.')
    for medium in creative_needs.evaluate(state)['unresolved']:
        problems.append('Confirm the creative source and production scope for ' + medium + '.')
    for package in state.get('packages') or []:
        for line in package.get('lines') or []:
            try:
                amount = float(line.get('amt') or 0)
            except (TypeError, ValueError):
                amount = float('nan')
            if not math.isfinite(amount) or amount <= 0:
                problems.append('Rebuild packages with positive, finite prices.')
                continue
            minimum = rate_card.minimum_for(line.get('name'), line.get('cat'))
            if 0 < amount < minimum and not line.get('consulting'):
                problems.append(f"{package.get('name', 'Package')}: {line.get('name')} is below its IO minimum. Rebuild the packages.")
    for failure in state.get('draftFailures') or []:
        problems.append('Retry or edit the failed section: ' + str(failure.get('title') or failure.get('id')))
    for section in state.get('sections') or []:
        if not section.get('enabled', True):
            continue
        body = str(section.get('body') or '')
        if section.get('kind') == 'text' and not body.strip():
            problems.append('Write or hide the empty section: ' + str(section.get('title') or section.get('id')))
        if re.search(r'\b(?:on the card|card rate|wholesale|buy.side rate|internal cost)\b', body, re.I):
            problems.append('Remove internal pricing from ' + str(section.get('title') or section.get('id')))
    return list(dict.fromkeys(problems))
