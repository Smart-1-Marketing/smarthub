"""Validate AI media-mix output before it can change an insertion order."""
from decimal import Decimal, InvalidOperation


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number.")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{label} must be a finite number.") from None
    if not result.is_finite() or result < 0:
        raise ValueError(f"{label} must be finite and nonnegative.")
    return result


def validate_recommendation(result, intake):
    if not isinstance(result, dict):
        raise ValueError("The recommendation must be a JSON object.")
    result = dict(result)
    for key in ('summary', 'primary_product', 'suggested_test_budget', 'minimum_run_length', 'rationale'):
        value = result.setdefault(key, '')
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text.")
    if not result['summary'].strip():
        raise ValueError("The recommendation needs a summary.")
    for key in ('supporting_products', 'excluded_products', 'warnings'):
        value = result.setdefault(key, [])
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise ValueError(f"{key} must be a list of text values.")
    allocations = result.setdefault('suggested_allocations', [])
    if not isinstance(allocations, list):
        raise ValueError("suggested_allocations must be a list.")
    if not allocations:
        return result
    budget = number(intake.get('monthly_budget'), 'Monthly budget')
    if budget <= 0 or budget != budget.quantize(Decimal('.01')):
        raise ValueError("Provide a positive monthly budget in dollars and cents.")
    duration = intake.get('campaign_duration')
    days = duration.get('days') if isinstance(duration, dict) else duration
    if not intake.get('goals') or not intake.get('geography') or number(days, 'Campaign days') <= 0:
        raise ValueError("Confirm goals, geography, and campaign duration before allocating a budget.")
    selected = intake.get('selected_products')
    if not isinstance(selected, list) or not selected:
        raise ValueError("Select products before requesting allocations.")
    if any(isinstance(p, dict) and p.get('budget_changes') for p in selected):
        raise ValueError('Edit scheduled budget changes manually to preserve the schedule.')
    names = [p.get('product') if isinstance(p, dict) else None for p in selected]
    if any(not isinstance(n, str) or not n.strip() for n in names) or len(set(names)) != len(names):
        raise ValueError("Automatic allocations require one line per product. Edit repeated product lines manually.")
    seen, total, percent_total = set(), Decimal(0), Decimal(0)
    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise ValueError("Each allocation must be an object.")
        name = allocation.get('product')
        if not isinstance(name, str) or name not in names or name in seen:
            raise ValueError("Allocations must name each selected product exactly once.")
        seen.add(name)
        amount = number(allocation.get('monthly_budget'), 'Allocation budget')
        percent = number(allocation.get('percent'), 'Allocation percent')
        if amount != amount.quantize(Decimal('.01')):
            raise ValueError("Allocation budgets must use dollars and cents.")
        if percent > 100 or abs(percent - amount / budget * 100) > Decimal('.02'):
            raise ValueError("Allocation percentages must match their budgets.")
        if not isinstance(allocation.get('reason'), str):
            raise ValueError("Each allocation needs a text reason.")
        total += amount
        percent_total += percent
    if seen != set(names) or total != budget or abs(percent_total - 100) > Decimal('.1'):
        raise ValueError("Allocations must cover every selected product and reconcile to the monthly budget and 100%.")
    return result
